"""Run jobs: 202 + status, one run at a time, reads refused while a run rewrites the outputs, idempotency, failure, restart, and one real run."""
import sys

from fastapi.testclient import TestClient
from test_pipeline import _setup

from dedupe.api import ApiSettings, create_app
from dedupe.api.devtoken import issue
from dedupe.api.jobs import RunJobs

SECRET = "j" * 40


def hdr(s, role, sub=None, **extra):
    return {"Authorization": "Bearer " + issue(s, sub or f"u-{role}", [role]), **extra}


def fake(code):
    """A stand-in for the pipeline: runs `code` as a separate python process."""
    return lambda sources, work: [sys.executable, "-c", code]


def make(tmp_path, command, sources=True):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp_path / "work", database_url=f"sqlite:///{tmp_path / 'api.db'}",
                    pipeline_sources=tmp_path if sources else None)
    jobs = RunJobs.open(s.database_url, s.work, s.pipeline_sources, command)
    app = create_app(s, jobs=jobs)
    return TestClient(app), s, app.state.services


def test_a_run_starts_with_202_runs_alone_and_blocks_reads_until_finished(tmp_path):
    gate = tmp_path / "go"
    c, s, sv = make(tmp_path, fake(f"import pathlib,time\nfor _ in range(300):\n  time.sleep(0.1)\n  if pathlib.Path(r'{gate}').exists(): break\n"))
    admin = hdr(s, "platform_admin", "u-ops")
    r = c.post("/runs", json={"reason": "weekly sweep"}, headers=admin)
    assert r.status_code == 202 and r.json()["status"] == "running" and r.json()["requested_by"] == "u-ops" and r.json()["replayed"] is False
    jid = r.json()["id"]
    assert c.post("/runs", json={}, headers=admin).status_code == 409                          # one run at a time
    assert c.get("/runs/latest", headers=hdr(s, "viewer")).status_code == 503                    # outputs are being rewritten
    assert c.get("/queue", headers=hdr(s, "viewer")).status_code == 503
    assert c.get(f"/runs/{jid}", headers=hdr(s, "viewer")).json()["status"] == "running"
    gate.write_text("x")
    assert sv.jobs.wait(jid, 30).status == "succeeded"
    done = c.get(f"/runs/{jid}", headers=hdr(s, "viewer")).json()
    assert done["status"] == "succeeded" and done["exit_code"] == 0 and done["finished_at"]
    assert c.get("/queue", headers=hdr(s, "viewer")).status_code == 404                         # reads work again (this fake run produced no outputs)
    events = {e["event"]: e for e in sv.seclog.events(limit=100) if e["event"] in ("run_started", "run_finished")}
    assert events["run_started"]["actor"] == "u-ops" and events["run_started"]["object_ref"] == jid
    assert events["run_finished"]["detail"] == {"exit_code": 0, "outcome": "succeeded"}
    assert [j["id"] for j in c.get("/runs", headers=hdr(s, "viewer")).json()] == [jid]


def test_the_same_idempotency_key_never_starts_two_runs(tmp_path):
    c, s, sv = make(tmp_path, fake("pass"))
    admin = hdr(s, "platform_admin", "u-ops", **{"Idempotency-Key": "weekly-2026-10-06"})
    first = c.post("/runs", json={}, headers=admin)
    sv.jobs.wait(first.json()["id"], 30)
    again = c.post("/runs", json={}, headers=admin)
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["id"] == first.json()["id"]
    assert len(c.get("/runs", headers=hdr(s, "viewer")).json()) == 1


def test_a_failing_run_is_reported_as_failed_without_its_output(tmp_path):
    c, s, sv = make(tmp_path, fake("import sys; print('Aadhaar 1234 5678 9012 broke'); sys.exit(3)"))
    r = c.post("/runs", json={}, headers=hdr(s, "platform_admin"))
    sv.jobs.wait(r.json()["id"], 30)
    j = c.get(f"/runs/{r.json()['id']}", headers=hdr(s, "viewer"))
    assert j.json()["status"] == "failed" and j.json()["exit_code"] == 3 and "Aadhaar" not in j.text      # the child's log is never served
    assert [e["detail"]["outcome"] for e in sv.seclog.events(limit=50) if e["event"] == "run_finished"] == ["failed"]


def test_runs_need_configured_sources_and_strict_bodies(tmp_path):
    c, s, _ = make(tmp_path, fake("pass"), sources=False)
    admin = hdr(s, "platform_admin")
    assert c.post("/runs", json={}, headers=admin).status_code == 409
    assert c.post("/runs", json={"sources": "/etc"}, headers=admin).status_code == 422          # the caller never chooses what runs
    assert c.get("/runs/J000000000000", headers=hdr(s, "viewer")).status_code == 404
    assert c.get("/runs/bogus", headers=hdr(s, "viewer")).status_code == 422


def test_the_child_gets_the_database_in_its_environment_and_not_the_login_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_DEV_AUTH_SECRET", SECRET)
    out = tmp_path / "env.txt"
    c, s, sv = make(tmp_path, fake(f"import os; open(r'{out}','w').write(str(os.environ.get('DEDUPE_DATABASE_URL')) + '|' + str('DEDUPE_DEV_AUTH_SECRET' in os.environ))"))
    r = c.post("/runs", json={}, headers=hdr(s, "platform_admin"))
    sv.jobs.wait(r.json()["id"], 30)
    assert out.read_text() == f"{s.database_url}|False"


def test_jobs_left_running_by_a_dead_api_are_marked_interrupted(tmp_path):
    c, s, sv = make(tmp_path, fake("import time; time.sleep(60)"))
    jid = c.post("/runs", json={}, headers=hdr(s, "platform_admin")).json()["id"]
    assert RunJobs.open(s.database_url, s.work, s.pipeline_sources).get(jid).status == "interrupted"          # a restarted API opens the same database
    assert RunJobs.open(s.database_url, s.work, s.pipeline_sources).active() is None


def test_a_real_pipeline_run_through_the_api(tmp_path):
    syn, sources = _setup(tmp_path, seed=75, households=60)
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp_path / "work", database_url=f"sqlite:///{tmp_path / 'api.db'}", pipeline_sources=sources)
    app = create_app(s)
    c = TestClient(app)
    r = c.post("/runs", json={"reason": "first run"}, headers=hdr(s, "platform_admin"))
    assert r.status_code == 202
    job = app.state.services.jobs.wait(r.json()["id"], 300)
    assert job.status == "succeeded", (tmp_path / "work" / "jobs" / f"{job.id}.log").read_text()[-1500:]
    latest = c.get("/runs/latest", headers=hdr(s, "viewer")).json()
    assert latest["records"] > 100 and latest["configs"]["decision"]["source"] in ("file", "store")
    assert c.get("/queue", headers=hdr(s, "viewer")).status_code == 200
