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
    assert c.post(P + "/runs", json={"connections": ["crm"]}, headers=admin).status_code == 409             # one run at a time
    assert c.get(earlier + "/queue", headers=hdr(s, "viewer")).status_code == 200                              # the earlier run is still readable
    assert c.get(f"{P}/runs/{rid}/queue", headers=hdr(s, "viewer")).status_code == 409                         # the running one is not, yet
    sources = json.loads((sv.workspace.run_dir(PROJECT, rid) / "sources.json").read_text(encoding="utf-8"))
    assert [x["source_id"] for x in sources["sources"]] == ["crm"]                                             # the exact connection versions used
    gate.write_text("x")
    assert sv.jobs.wait(PROJECT, rid, 30).status == "succeeded"
    assert [x["id"] for x in c.get(P + "/runs", headers=hdr(s, "viewer")).json()] == [rid, earlier.rsplit("/", 1)[1]]
    events = {e["event"]: e for e in sv.seclog.events(limit=200) if e["event"] in ("run_started", "run_finished")}
    assert events["run_started"]["object_ref"] == f"{PROJECT}/{rid}" and events["run_finished"]["detail"]["outcome"] == "succeeded"


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
    ids = []
    for src in json.loads(sources_file.read_text(encoding="utf-8"))["sources"]:
        make_live(c, s, src)
        ids.append(src["source_id"])
    excel = next(x["source_id"] for x in json.loads(sources_file.read_text(encoding="utf-8"))["sources"] if x["connector"]["type"] == "xlsx")
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
    assert home["projects"] == 2 and home["connections"] == len(ids) and len(home["recent_runs"]) == 3
