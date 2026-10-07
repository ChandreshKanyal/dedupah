"""The workspace on a real PostgreSQL server: one main database and one database per project.

SKIPPED unless you point it at a server. The databases must already exist (the product never creates databases):

    createdb dedupe_main ; createdb dedupe_pgtest
    set DEDUPE_TEST_PG_MAIN=postgresql+psycopg2://dedupe:secret@localhost:5432/dedupe_main
    set DEDUPE_TEST_PG_PROJECT=postgresql+psycopg2://dedupe:secret@localhost:5432/dedupe_{project}
    pip install -e .[postgres]

(`docker compose up postgres` starts a server; create the two databases in it.) The project used is `pgtest`, so its database is `dedupe_pgtest`.
Run twice on purpose: the second run must find the first run's tables and rows and still pass (it uses a fresh project folder each time)."""
import os
import sys

import pytest

MAIN, PROJECT = os.environ.get("DEDUPE_TEST_PG_MAIN"), os.environ.get("DEDUPE_TEST_PG_PROJECT")
pytestmark = pytest.mark.skipif(not (MAIN and PROJECT), reason="set DEDUPE_TEST_PG_MAIN and DEDUPE_TEST_PG_PROJECT to run against a real PostgreSQL server")

from api_world import csv_connection, hdr, make_live, small_csv  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dedupe.api import ApiSettings, create_app  # noqa: E402
from dedupe.workspace import Workspace  # noqa: E402


def test_workspace_projects_grants_decisions_rules_and_runs_work_on_postgres(tmp_path):
    ws = Workspace(tmp_path / "ws", MAIN, PROJECT)
    assert ws.engine.dialect.name == "postgresql"
    pid = "pgtest"
    if ws.get(pid) is None:
        ws.create(pid, "Postgres test", "", "tester")
    ws.grant(pid, "alice", "steward", "tester")
    assert "steward" in ws.roles_for(pid, "alice")
    ws.revoke(pid, "alice", "steward")
    assert ws.roles_for(pid, "alice") == frozenset()
    judgments, configs = ws.stores(pid)
    assert judgments.engine.dialect.name == "postgresql" and configs.engine.dialect.name == "postgresql"
    assert judgments.verify()["ok"] and configs.verify()["ok"]                       # the hash chains hold on this database too
    version = configs.propose("settings", {}, "tester", "postgres check")
    assert version >= 1 and configs.verify()["ok"]


def test_the_api_runs_a_project_against_postgres(tmp_path):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="p" * 40, work=tmp_path / "ws", database_url=MAIN, project_database_url=PROJECT)
    app = create_app(s, command=lambda run_dir, sources, registry: [sys.executable, "-c", "pass"])
    sv = app.state.services
    if sv.workspace.get("pgtest") is None:
        sv.workspace.create("pgtest", "Postgres test", "", "tester")
    c = TestClient(app)
    make_live(c, s, csv_connection(small_csv(tmp_path / "crm.csv"), cid=f"crm{os.getpid()}"))
    r = c.post("/projects/pgtest/runs", json={"connections": [f"crm{os.getpid()}"]}, headers=hdr(s, "platform_admin", "u-ops"))
    assert r.status_code == 202
    assert sv.jobs.wait("pgtest", r.json()["id"], 60).status in ("succeeded", "failed")       # the fake pipeline writes no results; the point is the database round trip
    assert c.get("/projects/pgtest/runs", headers=hdr(s, "viewer")).status_code == 200
