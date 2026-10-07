"""Projects belong to the people who made them: private by default, connections and uploads inside the project, copy between projects, a one-person
project approving its own changes, the project's activity feed, and the schema upgrade that makes this possible on an existing database."""
import json
import sqlite3

import pytest
from api_world import csv_connection, hdr, small_csv
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect

from dedupe.api import ApiSettings, SecurityLog, create_app
from dedupe.schema import create_and_upgrade

ALICE, BOB, ADMIN = ("analyst", "alice"), ("analyst", "bob"), ("admin", "root")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="p" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}",
                    ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6)
    app = create_app(s)
    return TestClient(app), s, app.state.services, tmp_path


def as_(s, who):
    return hdr(s, who[0], who[1])


def test_a_project_is_private_to_its_creator_until_they_add_someone(world):
    c, s, sv, _ = world
    alice, bob, admin = as_(s, ALICE), as_(s, BOB), as_(s, ADMIN)
    assert c.post("/projects", json={"id": "clientx", "name": "Client X"}, headers=alice).status_code == 201
    assert c.get("/projects/clientx", headers=alice).status_code == 200
    for url in ("/projects/clientx", "/projects/clientx/runs", "/projects/clientx/connections", "/projects/clientx/uploads", "/projects/clientx/activity"):
        assert c.get(url, headers=bob).status_code == 403, url                                      # a colleague with the same role sees nothing
    assert c.get("/projects", headers=bob).json() == [] and [x["id"] for x in c.get("/projects", headers=alice).json()] == ["clientx"]
    assert c.get("/home", headers=bob).json()["projects"] == 0
    assert c.get("/projects/clientx", headers=admin).status_code == 200                              # administration (projects.all) still sees every project
    assert c.get("/projects/clientx/access", headers=alice).json()[0]["role"] == "owner"
    assert c.delete("/projects/clientx/access/alice/owner", headers=alice).status_code == 409        # the owner cannot be removed
    assert c.post("/projects/clientx/access", json={"subject": "bob", "role": "owner"}, headers=alice).status_code == 422
    assert c.get("/projects/clientx/access", headers=bob).status_code == 403                          # members do not manage membership; owners do
    assert c.post("/projects/clientx/access", json={"subject": "bob", "role": "analyst"}, headers=alice).status_code == 201     # the owner shares it: no admin needed
    assert c.get("/projects/clientx", headers=bob).status_code == 200


def test_creating_a_project_needs_its_own_permission(world):
    c, s, sv, _ = world
    for role in ("viewer", "steward", "approver", "rule_admin", "auditor"):
        assert c.post("/projects", json={"id": "nope-" + role[:3], "name": "x"}, headers=hdr(s, role)).status_code == 403
    assert c.post("/projects", json={"id": "by-admin", "name": "x"}, headers=as_(s, ADMIN)).status_code == 201


def test_each_project_has_its_own_connections_and_they_never_leak(world):
    c, s, sv, tmp = world
    alice, bob = as_(s, ALICE), as_(s, BOB)
    c.post("/projects", json={"id": "client-a", "name": "A"}, headers=alice)
    c.post("/projects", json={"id": "client-b", "name": "B"}, headers=bob)
    a_csv, b_csv = small_csv(tmp / "a.csv", rows=12), small_csv(tmp / "b.csv", rows=6)
    r = c.post("/projects/client-a/connections", json={"connection": csv_connection(a_csv), "reason": "client A's CRM export"}, headers=alice)
    assert r.status_code == 200 and r.json()["next"] == "make it live"                                  # one-person project: no second approver needed
    assert c.post("/projects/client-a/connections/crm/versions/1/activate", json={}, headers=alice).status_code == 200
    assert [x["id"] for x in c.get("/projects/client-a/connections", headers=alice).json()] == ["crm"]
    assert c.get("/projects/client-b/connections", headers=bob).json() == []                           # B has nothing of A's
    assert c.get("/projects/client-b/connections/crm", headers=bob).status_code == 404
    assert c.get("/projects/client-a/connections/crm", headers=bob).status_code == 403
    c.post("/projects/client-b/connections", json={"connection": csv_connection(b_csv), "reason": "client B's CRM export"}, headers=bob)   # the same id "crm" is fine here
    assert c.get("/projects/client-b/connections/crm", headers=bob).json()["latest"]["connector"]["path"].endswith("b.csv")
    r = c.post("/projects/client-b/runs", json={"connections": ["crm"]}, headers=hdr(s, "admin", "bob"))
    assert r.status_code == 409 and "no approved live version" in r.json()["error"]["message"]        # B's own crm is not live yet; A's does not count


def test_an_uploaded_file_belongs_to_the_project_that_uploaded_it(world):
    c, s, sv, tmp = world
    alice, bob = as_(s, ALICE), as_(s, BOB)
    c.post("/projects", json={"id": "client-a", "name": "A"}, headers=alice)
    c.post("/projects", json={"id": "client-b", "name": "B"}, headers=bob)
    up = c.post("/projects/client-a/uploads?filename=people.csv", content=b"id,name\n1,Asha Verma\n2,Ravi Rao\n", headers=alice)
    assert up.status_code == 201
    uid = up.json()["upload_id"]
    assert [u["upload_id"] for u in c.get("/projects/client-a/uploads", headers=alice).json()] == [uid]
    assert c.get("/projects/client-b/uploads", headers=bob).json() == []
    conn = {"source_id": "people", "connector": {"type": "upload", "upload_id": uid}, "record_id": "id", "fields": {"full_name": "name"}, "attributes": []}
    for path in ("inspect", "preview"):
        body = {"connector": conn["connector"]} if path == "inspect" else conn
        assert c.post(f"/projects/client-b/connections/{path}", json=body, headers=bob).status_code == 422          # B cannot read A's file, even knowing its id
    assert c.post("/projects/client-b/connections", json={"connection": conn, "reason": "try to use A's file"}, headers=bob).status_code == 422
    assert c.post("/projects/client-a/connections", json={"connection": conn, "reason": "the file is ours"}, headers=alice).status_code == 200


def test_a_connection_can_be_copied_between_projects_you_can_open(world):
    c, s, sv, tmp = world
    alice, bob = as_(s, ALICE), as_(s, BOB)
    c.post("/projects", json={"id": "client-a", "name": "A"}, headers=alice)
    c.post("/projects", json={"id": "client-b", "name": "B"}, headers=alice)
    c.post("/projects", json={"id": "bobs-own", "name": "Bob's"}, headers=bob)
    c.post("/projects/client-a/connections", json={"connection": csv_connection(small_csv(tmp / "a.csv")), "reason": "client A's CRM export"}, headers=alice)
    c.post("/projects/client-a/connections/crm/versions/1/activate", json={}, headers=alice)
    r = c.post("/projects/client-b/connections/copy", json={"from_project": "client-a", "connection_id": "crm", "new_id": "crm_copy"}, headers=alice)
    assert r.status_code == 201 and r.json() == {"id": "crm_copy", "version": 1, "status": "proposed"}
    copied = c.get("/projects/client-b/connections/crm_copy", headers=alice).json()
    assert copied["latest"]["source_id"] == "crm_copy" and "copied from client-a/crm version 1" in copied["versions"][0]["reason"]
    assert c.get("/projects/client-b/connections/crm", headers=alice).status_code == 404                  # only the copy exists in B
    assert c.post("/projects/bobs-own/connections/copy", json={"from_project": "client-a", "connection_id": "crm"}, headers=bob).status_code == 403    # not Bob's to read
    assert c.post("/projects/client-b/connections/copy", json={"from_project": "client-a", "connection_id": "nope"}, headers=alice).status_code == 404
    up = c.post("/projects/client-a/uploads?filename=p.csv", content=b"id,name\n1,Asha Verma\n", headers=alice).json()["upload_id"]
    c.post("/projects/client-a/connections", json={"connection": {"source_id": "pf", "connector": {"type": "upload", "upload_id": up}, "record_id": "id",
                                                                   "fields": {"full_name": "name"}, "attributes": []}, "reason": "an uploaded file"}, headers=alice)
    assert c.post("/projects/client-b/connections/copy", json={"from_project": "client-a", "connection_id": "pf"}, headers=alice).status_code == 422


def test_a_shared_project_can_require_a_second_person_and_a_solo_one_cannot_switch_it_on(world):
    c, s, sv, tmp = world
    alice, bob = as_(s, ALICE), as_(s, BOB)
    assert c.post("/projects", json={"id": "team-job", "name": "Team job"}, headers=alice).json()["second_approval"] is False
    assert c.patch("/projects/team-job/settings", json={"second_approval": True}, headers=alice).status_code == 409           # nobody else is in the project yet
    c.post("/projects/team-job/access", json={"subject": "bob", "role": "analyst"}, headers=alice)
    assert c.patch("/projects/team-job/settings", json={"second_approval": True}, headers=bob).status_code == 403            # members cannot change it; the owner can
    assert c.patch("/projects/team-job/settings", json={"second_approval": True}, headers=alice).json()["second_approval"] is True
    c.post("/projects/team-job/connections", json={"connection": csv_connection(small_csv(tmp / "a.csv")), "reason": "the CRM export, first version"}, headers=alice)
    assert c.post("/projects/team-job/connections/crm/versions/1/activate", json={}, headers=alice).status_code == 409        # needs approval now
    assert c.post("/projects/team-job/connections/crm/versions/1/approve", json={}, headers=alice).status_code == 409         # not by the proposer
    assert c.post("/projects/team-job/connections/crm/versions/1/approve", json={}, headers=bob).status_code == 200
    assert c.post("/projects/team-job/connections/crm/versions/1/activate", json={}, headers=bob).status_code == 200
    assert c.get("/projects/team-job", headers=alice).json()["second_approval"] is True


def test_the_activity_feed_lists_a_projects_own_events_newest_first(world):
    c, s, sv, tmp = world
    alice, bob = as_(s, ALICE), as_(s, BOB)
    c.post("/projects", json={"id": "client-a", "name": "A"}, headers=alice)
    c.post("/projects", json={"id": "client-b", "name": "B"}, headers=bob)
    c.post("/projects/client-a/connections", json={"connection": csv_connection(small_csv(tmp / "a.csv")), "reason": "client A's CRM export"}, headers=alice)
    c.post("/projects/client-b/connections", json={"connection": csv_connection(small_csv(tmp / "b.csv")), "reason": "client B's CRM export"}, headers=bob)
    feed = c.get("/projects/client-a/activity", headers=alice).json()
    assert [e["detail"].get("action") for e in feed][:2] == ["propose", "create_project"]                  # newest first
    assert {e["project_id"] for e in feed} == {"client-a"} and {e["actor"] for e in feed} == {"alice"}
    assert "client-b" not in json.dumps(feed)                                                              # B's events are not in A's feed
    older = c.get(f"/projects/client-a/activity?before_id={feed[0]['id']}", headers=alice).json()
    assert [e["id"] for e in older] == [e["id"] for e in feed[1:]]
    assert c.get("/projects/client-a/activity", headers=bob).status_code == 403
    assert sv.seclog.verify()["ok"] is True                                                               # the project field is inside the hash chain


def test_the_security_log_keeps_verifying_rows_written_before_projects_existed(tmp_path):
    db = tmp_path / "old.db"
    SecurityLog.open(f"sqlite:///{db}").record("startup", detail={"env": "dev"})                          # a row in today's shape...
    con = sqlite3.connect(db)
    con.executescript("DROP TRIGGER IF EXISTS security_log_no_update; DROP TRIGGER IF EXISTS security_log_no_delete;")
    con.execute("DROP INDEX IF EXISTS ix_security_log_project")
    con.execute("ALTER TABLE security_log DROP COLUMN project_id")                                          # ...made to look like the older shape without the column
    con.commit()
    con.close()
    log = SecurityLog.open(f"sqlite:///{db}")                                                             # opening it upgrades the table
    assert "project_id" in {col["name"] for col in inspect(log.engine).get_columns("security_log")}
    assert log.verify()["ok"] is True
    log.record("config_change", project_id="demo", detail={"action": "x"})
    log.record("config_change", detail={"action": "y"})
    assert log.verify() == {"ok": True, "rows": 3, "first_bad_id": None}
    assert [e["detail"]["action"] for e in log.activity("demo")] == ["x"]
    with log.engine.begin() as conn:                                                                      # the project field is tamper-evident too
        conn.exec_driver_sql("DROP TRIGGER IF EXISTS security_log_no_update")
        conn.exec_driver_sql("UPDATE security_log SET project_id = 'other' WHERE id = 2")
    assert log.verify()["ok"] is False


def test_the_schema_helper_adds_columns_to_existing_tables_and_refuses_unsafe_ones(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    old = MetaData()
    Table("things", old, Column("id", String(10), primary_key=True))
    create_and_upgrade(engine, old)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO things VALUES ('a')")
    new = MetaData()
    Table("things", new, Column("id", String(10), primary_key=True), Column("label", String(20)), Column("flag", String(5), nullable=False, server_default="x"))
    assert create_and_upgrade(engine, new) == ["things.label", "things.flag"]
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT id, label, flag FROM things").first() == ("a", None, "x")        # old rows get the default
    assert create_and_upgrade(engine, new) == []                                                          # running it again changes nothing
    risky = MetaData()
    Table("things", risky, Column("id", String(10), primary_key=True), Column("must", String(5), nullable=False))
    with pytest.raises(RuntimeError, match="NOT NULL"):
        create_and_upgrade(engine, risky)
