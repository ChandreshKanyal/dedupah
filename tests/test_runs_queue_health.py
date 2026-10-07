"""The run queue (runs wait their turn, in order, and survive a restart), the run log, and connection health (test now, drift, history from runs)."""
import json
import sys

from api_world import PROJECT, csv_connection, hdr, make_live, small_csv
from fastapi.testclient import TestClient

from dedupe.api import ApiSettings, create_app
from dedupe.workspace import RunJobs, Workspace

P = f"/projects/{PROJECT}"
ADMIN = ("admin", "u-root")


def waiting(gate):
    """A stand-in pipeline that waits for `gate` to exist, then ends."""
    code = f"import pathlib,time\nfor _ in range(600):\n  time.sleep(0.05)\n  if pathlib.Path(r'{gate}').exists(): break\n"
    return lambda run_dir, sources, registry: [sys.executable, "-c", code]


def writing_summary(records):
    """A stand-in pipeline that finishes at once and leaves the summary a real run would (rows read per source)."""
    def command(run_dir, sources, registry):
        body = json.dumps({"ingest": {"records": records, "total": sum(records.values())}, "total_seconds": 0.1})
        return [sys.executable, "-c", f"import pathlib; pathlib.Path(r'{run_dir}/pipeline_summary.json').write_text({body!r}); print('contact asha@example.com PAN ABCDE1234F done')"]
    return command


def world(tmp_path, command, **settings):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="q" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}",
                    ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6, **settings)
    app = create_app(s, command=command)
    sv = app.state.services
    sv.workspace.create(PROJECT, "Demo", "", "setup")
    c = TestClient(app)
    make_live(c, s, csv_connection(small_csv(tmp_path / "crm.csv")))
    return c, s, sv


def start(c, s, **body):
    return c.post(P + "/runs", json={"connections": ["crm"], **body}, headers=hdr(s, *ADMIN))


def test_runs_wait_in_order_and_start_by_themselves(tmp_path):
    gate = tmp_path / "go"
    c, s, sv = world(tmp_path, waiting(gate))
    a, b, d = (start(c, s).json() for _ in range(3))
    assert [x["status"] for x in (a, b, d)] == ["running", "queued", "queued"] and [b["queue_position"], d["queue_position"]] == [1, 2]
    assert c.get(f"{P}/runs/{d['id']}", headers=hdr(s, "viewer")).json()["queue_position"] == 2
    cancelled = c.post(f"{P}/runs/{b['id']}/cancel", headers=hdr(s, *ADMIN))                                # a queued run is simply never started
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled" and cancelled.json()["queue_position"] is None
    assert c.get(f"{P}/runs/{d['id']}", headers=hdr(s, "viewer")).json()["queue_position"] == 1            # the one behind it moves up
    gate.write_text("x")
    assert sv.jobs.wait(PROJECT, a["id"], 30).status == "succeeded"
    assert sv.jobs.wait(PROJECT, d["id"], 30).status == "succeeded"                                         # started with nobody asking
    assert sv.jobs.get(PROJECT, b["id"]).status == "cancelled" and sv.jobs.get(PROJECT, b["id"]).started_at is None
    assert sv.jobs.active() is None


def test_several_runs_can_go_at_once_when_the_machine_allows_and_the_queue_has_a_limit(tmp_path):
    gate = tmp_path / "go"
    c, s, sv = world(tmp_path, waiting(gate), max_concurrent_runs=2, max_queued_runs=1)
    runs = [start(c, s) for _ in range(3)]
    assert [r.json()["status"] for r in runs] == ["running", "running", "queued"]
    full = start(c, s)
    assert full.status_code == 409 and "queue is full" in full.json()["error"]["message"]
    gate.write_text("x")
    for r in runs:
        assert sv.jobs.wait(PROJECT, r.json()["id"], 30).status == "succeeded"


def test_a_restart_keeps_waiting_runs_and_ends_the_ones_that_were_going(tmp_path):
    gate = tmp_path / "go"
    c, s, sv = world(tmp_path, waiting(gate))
    first, second = start(c, s).json(), start(c, s).json()
    assert (first["status"], second["status"]) == ("running", "queued")
    sv.jobs._procs[first["id"]].kill()                                                    # the old API process "dies" with its child
    sv.jobs._cancelled.add(first["id"])
    sv.jobs.wait(PROJECT, first["id"], 30)
    with sv.workspace.engine.begin() as conn:                                              # as if the API had died before it could write the outcome
        conn.exec_driver_sql("UPDATE project_runs SET status='running', finished_at=NULL WHERE id=?", (first["id"],))
        conn.exec_driver_sql("UPDATE project_runs SET status='queued', started_at=NULL WHERE id=?", (second["id"],))
    gate.write_text("x")
    again = RunJobs(Workspace(sv.workspace.root, sv.workspace.url), waiting(gate))        # the restarted API
    assert again.get(PROJECT, first["id"]).status == "interrupted"
    assert again.wait(PROJECT, second["id"], 30).status == "succeeded"                     # the queued run was picked up and ran


def test_the_run_log_is_scrubbed_and_only_for_people_who_can_open_the_project(tmp_path):
    c, s, sv = world(tmp_path, writing_summary({"crm": 12}))
    run = start(c, s).json()
    sv.jobs.wait(PROJECT, run["id"], 30)
    log = c.get(f"{P}/runs/{run['id']}/log", headers=hdr(s, "viewer")).json()
    assert log["available"] is True and any("done" in line for line in log["lines"])
    text = "\n".join(log["lines"])
    assert "asha@example.com" not in text and "ABCDE1234F" not in text and "[redacted]" in text        # personal data never leaves through the log
    assert c.get(f"{P}/runs/R000000000000/log", headers=hdr(s, "viewer")).status_code == 404
    c.post(P + "/access", json={"subject": "someone", "role": "viewer"}, headers=hdr(s, *ADMIN))
    assert c.get(f"{P}/runs/{run['id']}/log", headers=hdr(s, "viewer", "stranger")).status_code == 403


def test_a_run_records_rows_per_connection_and_each_connections_health(tmp_path):
    c, s, sv = world(tmp_path, writing_summary({"crm": 12}))
    run = start(c, s).json()
    done = sv.jobs.wait(PROJECT, run["id"], 30)
    assert done.summary["records_by_source"] == {"crm": 12}
    assert c.get(f"{P}/runs/{run['id']}", headers=hdr(s, "viewer")).json()["summary"]["records_by_source"] == {"crm": 12}
    health = c.get(P + "/connections/crm", headers=hdr(s, "viewer")).json()["health"]
    assert health[0]["kind"] == "run" and health[0]["ok"] is True and health[0]["rows"] == 12 and health[0]["version"] == 1
    listed = c.get(P + "/connections", headers=hdr(s, "viewer")).json()
    assert listed[0]["last_check"]["ok"] is True and listed[0]["last_check"]["rows"] == 12


def test_test_now_reads_the_source_and_reports_failures_without_values_or_paths(tmp_path):
    c, s, sv = world(tmp_path, writing_summary({"crm": 12}))
    admin = hdr(s, *ADMIN)
    path = tmp_path / "crm.csv"
    first = c.post(P + "/connections/crm/test", headers=admin).json()
    assert first["ok"] is True and first["rows"] == 12 and first["drift"] == {} and first["kind"] == "test" and first["version"] == 1
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([rows[0].replace(",internal_note", ",region")] + rows[1:]) + "\n", encoding="utf-8")        # the source renamed a column the mapping uses
    renamed = c.post(P + "/connections/crm/test", headers=admin).json()
    assert renamed["ok"] is False and "internal_note" in renamed["error"]                                  # it names the missing column...
    assert "Customer Number" not in json.dumps(renamed) and str(tmp_path) not in json.dumps(renamed)         # ...and never a value or a path
    path.unlink()
    gone = c.post(P + "/connections/crm/test", headers=admin).json()
    assert gone["ok"] is False and gone["error"] == "the file was not found" and str(tmp_path) not in json.dumps(gone)
    history = c.get(P + "/connections/crm", headers=hdr(s, "viewer")).json()["health"]
    assert [h["ok"] for h in history] == [False, False, True]                                              # newest first
    assert c.post(P + "/connections/crm/test", headers=hdr(s, "viewer")).status_code == 403                # testing reads the source: it needs sources.manage
    assert c.post(P + "/connections/nope/test", headers=admin).status_code == 404


def test_drift_lists_columns_added_and_removed_since_the_last_good_check(tmp_path):
    c, s, sv = world(tmp_path, writing_summary({"crm": 12}))
    admin = hdr(s, *ADMIN)
    path = tmp_path / "crm.csv"
    base = path.read_text(encoding="utf-8").splitlines()
    assert c.post(P + "/connections/crm/test", headers=admin).json()["drift"] == {}
    path.write_text("\n".join([base[0] + ",extra_col"] + [r + ",x" for r in base[1:]]) + "\n", encoding="utf-8")       # a column was added upstream
    added = c.post(P + "/connections/crm/test", headers=admin).json()
    assert added["ok"] is True and added["drift"] == {"added": ["extra_col"], "removed": []}
    assert c.post(P + "/connections/crm/test", headers=admin).json()["drift"] == {}                         # the new layout is now the baseline
    path.write_text("\n".join(base) + "\n", encoding="utf-8")                                               # and it went away again
    removed = c.post(P + "/connections/crm/test", headers=admin).json()
    assert removed["ok"] is True and removed["drift"] == {"added": [], "removed": ["extra_col"]}
