"""Runs inside projects: 202 + status, one run at a time on the machine, other runs stay readable, idempotency, failure, restart, and real runs
over live connections (one Excel-only run, one multi-connection run, a re-run)."""
import json
import sys

from api_world import PROJECT, csv_connection, hdr, make_live, pipeline_output, small_csv
from fastapi.testclient import TestClient
from test_pipeline import _setup

from dedupe.api import ApiSettings, create_app
from dedupe.workspace import RunJobs, Workspace

P = f"/projects/{PROJECT}"


def fake(code):
    """A stand-in for the pipeline: runs `code` as a separate python process."""
    return lambda run_dir, sources, registry: [sys.executable, "-c", code]


def world(tmp_path, command, output=None):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="j" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}")
    app = create_app(s, command=command)
    sv = app.state.services
    sv.workspace.create(PROJECT, "Demo", "", "setup")
    prefix = None
    if output is not None:
        prefix = f"{P}/runs/{sv.jobs.import_results(PROJECT, output, 'setup').id}"
    c = TestClient(app)
    make_live(c, s, csv_connection(small_csv(tmp_path / "crm.csv")))
    return c, s, sv, prefix


def test_a_run_starts_with_202_runs_alone_and_earlier_runs_stay_readable(tmp_path, tmp_path_factory):
    out = pipeline_output(tmp_path_factory.mktemp("runs_out"), seed=78, households=40)
    gate = tmp_path / "go"
    c, s, sv, earlier = world(tmp_path, fake(f"import pathlib,time\nfor _ in range(300):\n  time.sleep(0.1)\n  if pathlib.Path(r'{gate}').exists(): break\n"), out)
    admin = hdr(s, "platform_admin", "u-ops")
    r = c.post(P + "/runs", json={"connections": ["crm"], "reason": "weekly sweep"}, headers=admin)
    assert r.status_code == 202 and r.json()["status"] == "running" and r.json()["requested_by"] == "u-ops" and r.json()["connections"] == [{"id": "crm", "version": 1}]
    rid = r.json()["id"]
    second = c.post(P + "/runs", json={"connections": ["crm"]}, headers=admin)                                # one at a time: the next one waits its turn
    assert second.status_code == 202 and second.json()["status"] == "queued" and second.json()["queue_position"] == 1
    assert c.get(earlier + "/queue", headers=hdr(s, "viewer")).status_code == 200                              # the earlier run is still readable
    assert c.get(f"{P}/runs/{rid}/queue", headers=hdr(s, "viewer")).status_code == 409                         # the running one is not, yet
    sources = json.loads((sv.workspace.run_dir(PROJECT, rid) / "sources.json").read_text(encoding="utf-8"))
    assert [x["source_id"] for x in sources["sources"]] == ["crm"]                                             # the exact connection versions used
    gate.write_text("x")
    assert sv.jobs.wait(PROJECT, rid, 30).status == "succeeded"
    assert sv.jobs.wait(PROJECT, second.json()["id"], 30).status == "succeeded"                                  # the queued run started by itself
    assert [x["id"] for x in c.get(P + "/runs", headers=hdr(s, "viewer")).json()] == [second.json()["id"], rid, earlier.rsplit("/", 1)[1]]
    events = {e["event"]: e for e in sv.seclog.events(limit=200) if e["event"] in ("run_started", "run_finished") and e["object_ref"] == f"{PROJECT}/{rid}"}
    assert events["run_started"]["project_id"] == PROJECT and events["run_finished"]["detail"]["outcome"] == "succeeded"


def test_runs_need_live_connections_and_a_known_project(tmp_path):
    c, s, sv, _ = world(tmp_path, fake("pass"))
    admin = hdr(s, "platform_admin")
    assert c.post(P + "/runs", json={"connections": []}, headers=admin).status_code == 422
    assert c.post(P + "/runs", json={"connections": ["nothing_here"]}, headers=admin).status_code == 409
    assert c.post(P + "/runs", json={"connections": ["crm", "crm"]}, headers=admin).status_code == 422
    assert c.post("/projects/unknown/runs", json={"connections": ["crm"]}, headers=admin).status_code == 404
    assert c.post(P + "/runs", json={"connections": ["crm"], "sources": "/etc"}, headers=admin).status_code == 422          # nothing else may be chosen
    assert c.post(P + "/runs", json={"connections": ["crm"]}, headers=hdr(s, "steward")).status_code == 403


def test_the_same_idempotency_key_never_starts_two_runs_and_a_rerun_reuses_connections(tmp_path):
    c, s, sv, _ = world(tmp_path, fake("pass"))
    admin = hdr(s, "platform_admin", "u-ops", **{"Idempotency-Key": "weekly-2026-10-06"})
    first = c.post(P + "/runs", json={"connections": ["crm"]}, headers=admin)
    sv.jobs.wait(PROJECT, first.json()["id"], 30)
    again = c.post(P + "/runs", json={"connections": ["crm"]}, headers=admin)
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["id"] == first.json()["id"]
    rerun = c.post(P + "/runs", json={"from_run": first.json()["id"]}, headers=hdr(s, "platform_admin"))
    assert rerun.status_code == 202 and rerun.json()["connections"] == first.json()["connections"]


def test_a_failing_run_is_reported_as_failed_without_its_output(tmp_path):
    c, s, sv, _ = world(tmp_path, fake("import sys; print('Aadhaar 1234 5678 9012 broke'); sys.exit(3)"))
    r = c.post(P + "/runs", json={"connections": ["crm"]}, headers=hdr(s, "platform_admin"))
    sv.jobs.wait(PROJECT, r.json()["id"], 30)
    j = c.get(f"{P}/runs/{r.json()['id']}", headers=hdr(s, "viewer"))
    assert j.json()["status"] == "failed" and j.json()["exit_code"] == 3 and "Aadhaar" not in j.text


def test_the_child_gets_the_project_database_and_upload_folder_but_not_the_login_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_DEV_AUTH_SECRET", "j" * 40)
    out = tmp_path / "env.txt"
    code = (f"import os; open(r'{out}','w').write(os.environ['DEDUPE_DATABASE_URL'] + '|' + os.environ['DEDUPE_UPLOAD_DIR'] + '|' + "
            f"str('DEDUPE_DEV_AUTH_SECRET' in os.environ))")
    c, s, sv, _ = world(tmp_path, fake(code))
    r = c.post(P + "/runs", json={"connections": ["crm"]}, headers=hdr(s, "platform_admin"))
    sv.jobs.wait(PROJECT, r.json()["id"], 30)
    assert out.read_text() == f"{sv.workspace.project_url(PROJECT)}|{sv.workspace.upload_dir}|False"


def test_runs_left_running_by_a_dead_api_are_marked_interrupted(tmp_path):
    c, s, sv, _ = world(tmp_path, fake("import time; time.sleep(30)"))
    rid = c.post(P + "/runs", json={"connections": ["crm"]}, headers=hdr(s, "platform_admin")).json()["id"]
    again = RunJobs(Workspace(sv.workspace.root, sv.workspace.url))                                  # a restarted API opens the same database
    assert again.get(PROJECT, rid).status == "interrupted" and again.active() is None


def test_real_runs_over_connections_excel_alone_then_several_then_again(tmp_path, monkeypatch):
    syn, sources_file = _setup(tmp_path, seed=79, households=40)
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="j" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}")
    app = create_app(s)
    sv = app.state.services
    c = TestClient(app)
    admin = hdr(s, "platform_admin", "u-ops")
    assert c.post("/projects", json={"id": "insurance-only", "name": "Insurance file"}, headers=admin).status_code == 201
    assert c.post("/projects", json={"id": "customer-master", "name": "Customer master"}, headers=admin).status_code == 201
    for pid in ("insurance-only", "customer-master"):                          # new projects are private to their creator: add the colleagues who approve and read
        for who, role in (("u-lee", "approver"), ("u-viewer", "viewer")):
            assert c.post(f"/projects/{pid}/access", json={"subject": who, "role": role}, headers=admin).status_code == 201
    ids = []
    everything = json.loads(sources_file.read_text(encoding="utf-8"))["sources"]
    for src in everything:                                                    # connections belong to a project: every source goes into the master project
        make_live(c, s, src, project="customer-master")
        ids.append(src["source_id"])
    excel_src = next(x for x in everything if x["connector"]["type"] == "xlsx")
    excel = excel_src["source_id"]
    make_live(c, s, excel_src, project="insurance-only")                      # the one-off project connects only the Excel file, on its own
    assert c.get("/projects/insurance-only/connections", headers=hdr(s, "viewer")).json()[0]["id"] == excel
    assert len(c.get("/projects/insurance-only/connections", headers=hdr(s, "viewer")).json()) == 1
    one = c.post("/projects/insurance-only/runs", json={"connections": [excel]}, headers=admin).json()
    assert sv.jobs.wait("insurance-only", one["id"], 300).status == "succeeded"
    many = c.post("/projects/customer-master/runs", json={"connections": ids}, headers=admin).json()
    done = sv.jobs.wait("customer-master", many["id"], 300)
    assert done.status == "succeeded", (sv.workspace.run_dir("customer-master", many["id"]) / "run.log").read_text()[-1500:]
    a = c.get(f"/projects/insurance-only/runs/{one['id']}", headers=hdr(s, "viewer")).json()["summary"]
    b = c.get(f"/projects/customer-master/runs/{many['id']}", headers=hdr(s, "viewer")).json()["summary"]
    assert a["sources"] == [excel] and sorted(b["sources"]) == sorted(ids) and b["records"] > a["records"]
    again = c.post("/projects/customer-master/runs", json={"from_run": many["id"]}, headers=admin).json()
    assert sv.jobs.wait("customer-master", again["id"], 300).status == "succeeded"
    ids_1 = c.get(f"/projects/customer-master/runs/{many['id']}/queue?limit=3", headers=hdr(s, "viewer")).json()
    ids_2 = c.get(f"/projects/customer-master/runs/{again['id']}/queue?limit=3", headers=hdr(s, "viewer")).json()
    assert [(i["person_l"], i["person_r"]) for i in ids_1] == [(i["person_l"], i["person_r"]) for i in ids_2]   # person IDs are stable across a project's runs
    home = c.get("/home", headers=admin).json()
    assert home["projects"] == 2 and home["connections"] == len(ids) + 1 and len(home["recent_runs"]) == 3


def test_a_run_can_be_started_with_chosen_rule_versions(tmp_path):
    """The run screen names the rule versions to use; they are checked, written next to the run and handed to the pipeline."""
    from dedupe.workspace.runs import CONFIG_VERSIONS, pipeline_command
    c, s, sv, _ = world(tmp_path, fake("pass"))
    admin = hdr(s, "platform_admin", "u-ops")
    default = c.get(P + "/config/decision/default", headers=admin).json()["content"]
    v = c.post(P + "/config/decision/propose", json={"content": {**default, "auto_merge_at": 0.97}, "reason": "stricter merges to try"}, headers=hdr(s, "rule_admin", "u-ops")).json()["version"]
    run = {"connections": ["crm"]}
    assert c.post(P + "/runs", json={**run, "rules": {"decision": 99}}, headers=admin).status_code == 422                  # no such version
    assert c.post(P + "/runs", json={**run, "rules": {"golden": 1}}, headers=admin).status_code == 422                     # no such rule set
    held = c.post(P + "/runs", json={**run, "rules": {"decision": v}}, headers=admin)                                       # second approval on: a draft never runs
    assert held.status_code == 422 and "approval" in held.text
    assert c.post(f"{P}/config/decision/versions/{v}/approve", json={}, headers=hdr(s, "approver", "u-lee")).status_code == 200
    r = c.post(P + "/runs", json={**run, "rules": {"decision": v}}, headers=admin)                                          # approved, not live: may run
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    sv.jobs.wait(PROJECT, rid, 30)
    run_dir = sv.workspace.run_dir(PROJECT, rid)
    assert json.loads((run_dir / CONFIG_VERSIONS).read_text(encoding="utf-8")) == {"decision": v}
    assert c.get(f"{P}/runs/{rid}", headers=admin).json()["options"] == {"full": False, "rules": {"decision": v}}
    cmd = pipeline_command(run_dir, run_dir / "sources.json", tmp_path)
    assert cmd[cmd.index("--config-versions") + 1] == str(run_dir / CONFIG_VERSIONS)
    plain = c.post(P + "/runs", json=run, headers=admin).json()["id"]                                                   # nothing chosen: the live versions
    sv.jobs.wait(PROJECT, plain, 30)
    assert not (sv.workspace.run_dir(PROJECT, plain) / CONFIG_VERSIONS).exists()
