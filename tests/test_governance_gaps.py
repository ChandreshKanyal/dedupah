"""Gaps in the second approval and the schedules found by the platform review: earlier versions nobody else approved (P1-04), switching the
second approval off alone (P1-05), a second API process (P1-08), the unmasked results table (P1-03), and who a schedule runs for (P1-13)."""
import sys

import pytest
from api_world import app_with_run, csv_connection, hdr, pipeline_output, small_csv
from fastapi.testclient import TestClient

from dedupe.api import ApiSettings, create_app

OLGA, XAVIER, ADMIN = ("analyst", "olga"), ("analyst", "xavier"), ("admin", "root")


def app(tmp_path, **kw):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="g" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}",
                    ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6, **kw)
    a = create_app(s, command=lambda run_dir, sources, registry: [sys.executable, "-c", "pass"])
    return TestClient(a), s, a.state.services


@pytest.fixture
def gov(tmp_path):
    """olga's project: v1 of the matching rules put in use while she worked alone, then xavier joins and the second approval is switched on."""
    c, s, sv = app(tmp_path)
    o, x = hdr(s, *OLGA), hdr(s, *XAVIER)
    assert c.post("/projects", json={"id": "gov", "name": "Gov"}, headers=o).status_code == 201
    base = c.get("/projects/gov/config/decision/default", headers=o).json()["content"]
    c.post("/projects/gov/config/decision/propose", json={"content": {**base, "auto_merge_at": 0.2, "rules": []}, "reason": "my quick rules"}, headers=o)
    assert c.post("/projects/gov/config/decision/versions/1/activate", json={}, headers=o).status_code == 200
    c.post("/projects/gov/access", json={"subject": "xavier", "role": "approver"}, headers=o)
    assert c.patch("/projects/gov/settings", json={"second_approval": True}, headers=o).status_code == 200
    c.post("/projects/gov/config/decision/propose", json={"content": base, "reason": "standard rules for the bank"}, headers=o)
    c.post("/projects/gov/config/decision/versions/2/approve", json={"reason": "ok"}, headers=x)
    assert c.post("/projects/gov/config/decision/versions/2/activate", json={}, headers=x).status_code == 200
    return c, s, sv, o, x, base


def test_a_version_nobody_else_approved_can_be_neither_restored_nor_run_once_a_second_approval_is_required(gov):
    c, s, sv, o, x, base = gov
    versions = {v["version"]: v for v in c.get("/projects/gov/config/decision", headers=o).json()}
    assert versions[1]["reviewed"] is False and versions[2]["reviewed"] is True
    r = c.post("/projects/gov/config/decision/rollback", json={"to_version": 1, "reason": "back to my rules"}, headers=o)
    assert r.status_code == 409 and "second person" in r.text
    c.post("/projects/gov/connections", json={"connection": csv_connection(small_csv(sv.settings.work / "crm.csv")), "reason": "connect test data"}, headers=o)
    c.post("/projects/gov/connections/crm/versions/1/approve", json={}, headers=x)
    assert c.post("/projects/gov/connections/crm/versions/1/activate", json={}, headers=x).status_code == 200
    r = c.post("/projects/gov/runs", json={"connections": ["crm"], "rules": {"decision": 1}}, headers=o)
    assert r.status_code == 422 and "second person" in r.text
    # the way that works: propose the same content again and have it approved
    old = c.get("/projects/gov/config/decision/versions/1", headers=o).json()["content"]
    assert c.post("/projects/gov/config/decision/propose", json={"content": old, "reason": "my rules, properly this time"}, headers=o).status_code == 200
    c.post("/projects/gov/config/decision/versions/3/approve", json={"reason": "checked"}, headers=x)
    assert c.post("/projects/gov/config/decision/versions/3/activate", json={}, headers=x).status_code == 200
    # going back to a version a second person approved is still one person's emergency action, and the restored copy counts as approved
    assert c.post("/projects/gov/config/decision/rollback", json={"to_version": 2, "reason": "the bank's rules again"}, headers=o).status_code == 200
    assert c.get("/projects/gov/config/decision", headers=o).json()[-1]["reviewed"] is True


def test_one_person_cannot_switch_the_second_approval_off_while_others_work_in_the_project(gov):
    c, s, sv, o, x, base = gov
    c.post("/projects/gov/config/decision/propose", json={"content": {**base, "auto_merge_at": 0.3}, "reason": "lower the bar"}, headers=o)
    r = c.patch("/projects/gov/settings", json={"second_approval": False}, headers=o)
    assert r.status_code == 409 and "administrator" in r.text
    assert c.post("/projects/gov/config/decision/versions/3/activate", json={}, headers=o).status_code == 409        # still waits for xavier
    assert c.patch("/projects/gov/settings", json={"second_approval": False}, headers=hdr(s, *ADMIN)).status_code == 200


def test_a_second_api_process_sees_the_second_approval_at_once(tmp_path):
    c1, s, sv = app(tmp_path)
    c2, _, _ = app(tmp_path, scheduler_enabled=False)
    o = hdr(s, *OLGA)
    c1.post("/projects", json={"id": "two", "name": "Two"}, headers=o)
    assert c2.get("/projects/two", headers=o).json()["second_approval"] is False                  # process 2 has read the setting once
    c1.post("/projects/two/access", json={"subject": "xavier", "role": "approver"}, headers=o)
    c1.patch("/projects/two/settings", json={"second_approval": True}, headers=o)
    base = c1.get("/projects/two/config/decision/default", headers=o).json()["content"]
    c1.post("/projects/two/config/decision/propose", json={"content": base, "reason": "first rules"}, headers=o)
    assert c2.post("/projects/two/config/decision/versions/1/activate", json={}, headers=o).status_code == 409


def test_paging_the_whole_results_table_unmasked_needs_the_export_right(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("unmask")
    c, s, sv, run = app_with_run(tmp, pipeline_output(tmp / "out", seed=95, households=20), ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6)
    steward, analyst = hdr(s, "steward", "stella"), hdr(s, "analyst", "ana")
    rows = run + "/datasets/golden_records/rows?limit=100"
    assert c.get(rows, headers=steward).status_code == 200                                         # masked: fine
    r = c.get(rows + "&unmask=true", headers=steward)
    assert r.status_code == 403 and "one person" in r.text
    person = c.get(rows, headers=steward).json()["rows"][0][0]
    assert c.get(f"{run}/people/{person}?unmask=true", headers=steward).json()["unmasked"] is True   # one person at a time stays possible
    assert c.get(rows + "&unmask=true", headers=analyst).json()["unmasked"] is True


def test_a_schedule_runs_only_for_someone_who_may_still_start_runs_there_and_every_start_is_logged(tmp_path):
    c, s, sv = app(tmp_path, scheduler_enabled=False)
    o, b = hdr(s, *OLGA), hdr(s, "analyst", "bob")
    c.post("/projects", json={"id": "sch", "name": "Sch"}, headers=o)
    c.post("/projects/sch/access", json={"subject": "bob", "role": "analyst"}, headers=o)
    conn = csv_connection(small_csv(tmp_path / "crm.csv"))
    c.post("/projects/sch/connections", json={"connection": conn, "reason": "connect test data"}, headers=b)
    c.post("/projects/sch/connections/crm/versions/1/activate", json={}, headers=b)
    sid = c.post("/projects/sch/schedules", json={"name": "hourly", "connections": ["crm"], "frequency": "hourly", "at_time": "00:05", "timezone": "UTC"},
                 headers=b).json()["id"]

    def due():
        with sv.schedules.engine.begin() as con:
            con.exec_driver_sql("update project_schedules set next_run_at='2000-01-01T00:00:00+00:00' where enabled")

    assert c.delete("/projects/sch/access/bob/analyst", headers=o).status_code == 204              # bob leaves the project
    due()
    assert sv.schedules.tick()[0]["outcome"] == "paused_creator"
    assert c.patch(f"/projects/sch/schedules/{sid}", json={"enabled": True}, headers=o).json()["created_by"] == "olga"   # olga takes it over
    due()
    started = sv.schedules.tick()[0]
    assert started["outcome"] == "started"
    logged = [e for e in sv.seclog.events(limit=50) if e["event"] == "run_started" and e["actor"] == f"schedule:{sid}"]
    assert logged and logged[0]["detail"]["on_behalf_of"] == "olga" and logged[0]["object_ref"].endswith(started["run_id"])
