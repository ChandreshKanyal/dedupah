"""A server variable holding a database URL or a password belongs to the projects its operator allowed (DEDUPE_ENV_GRANTS), never to every
project: another client's project cannot read its source, write into its warehouse, or run with it (agent review P1-01)."""
import sqlite3

import pytest
from api_world import app_with_run, csv_connection, hdr, pipeline_output, small_csv

from dedupe import envgrants

ADMIN = ("admin", "u-root")
WHY = "the client's reporting warehouse"


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    return pipeline_output(tmp_path_factory.mktemp("grants_out"), seed=91, households=30)


def test_grants_are_read_from_the_operator_setting(monkeypatch):
    monkeypatch.setenv(envgrants.GRANTS_VAR, " DEDUPE_SRC_A_DB = client-a , client-a2 ;\nDEDUPE_DST_SHARED=*; DEDUPE_SRC_B_*=client-b; bad entry; DEDUPE_PATH=x;"
                       " DEDUPE_SRC_C=Not Valid")
    g = envgrants.grants()
    assert g == {"DEDUPE_SRC_A_DB": {"client-a", "client-a2"}, "DEDUPE_DST_SHARED": {"*"}, "DEDUPE_SRC_B_*": {"client-b"}}   # malformed entries grant nothing
    assert envgrants.env_names({"type": "sql", "url_env": "DEDUPE_SRC_A_DB", "password_env": "", "table": "t"}) == ["DEDUPE_SRC_A_DB"]
    envgrants.check("client-a", {"url_env": "DEDUPE_SRC_A_DB"})
    envgrants.check("anyone", {"url_env": "DEDUPE_DST_SHARED"})
    envgrants.check("client-b", {"url_env": "DEDUPE_SRC_B_CRM", "password_env": "DEDUPE_SRC_B_PW"})        # a prefix covers its variables
    for project, conf in (("client-b", {"url_env": "DEDUPE_SRC_A_DB"}), ("client-a", {"url_env": "DEDUPE_SRC_B_CRM"}), ("client-a", {"url_env": "DEDUPE_SRC_C"})):
        with pytest.raises(envgrants.EnvNotGranted, match="DEDUPE_ENV_GRANTS"):
            envgrants.check(project, conf)
    envgrants.check("client-b", {"type": "csv", "path": "/data/x.csv"})                                     # no variable, nothing to allow
    monkeypatch.delenv(envgrants.GRANTS_VAR)
    with pytest.raises(envgrants.EnvNotGranted):                                                            # nothing set: nothing allowed
        envgrants.check("client-a", {"url_env": "DEDUPE_SRC_A_DB"})


def test_another_clients_project_cannot_use_its_database_variables(tmp_path, output, monkeypatch):
    """The review's attack, step by step: project B names client A's warehouse and source variables. Every door refuses, and A's data is untouched."""
    warehouse, source = tmp_path / "client_a_warehouse.db", tmp_path / "client_a_source.db"
    monkeypatch.setenv("DEDUPE_DST_CLIENT_A", f"sqlite:///{warehouse}")
    monkeypatch.setenv("DEDUPE_SRC_CLIENT_A_DB", f"sqlite:///{source}")
    monkeypatch.setenv(envgrants.GRANTS_VAR, "DEDUPE_DST_CLIENT_A=client-a; DEDUPE_SRC_CLIENT_A_DB=client-a")
    con = sqlite3.connect(source)
    con.execute("create table customers(cust_id text, full_name text, pan text)")
    con.execute("insert into customers values ('A1', 'Asha Rao', 'ABCDE1234F')")
    con.commit()
    con.close()
    c, s, sv, run_a = app_with_run(tmp_path, output, project="client-a", ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6, export_limit_per_min=10**6)
    sv.workspace.create("client-b", "Client B", "", "setup")
    run_b = f"/projects/client-b/runs/{sv.jobs.import_results('client-b', output, 'setup').id}"
    admin = hdr(s, *ADMIN)

    # client A, allowed: its destination works and writes its table
    dest = {"destination": {"destination_id": "wh", "url_env": "DEDUPE_DST_CLIENT_A"}, "reason": WHY}
    assert c.post("/projects/client-a/destinations", json=dest, headers=admin).status_code == 200
    configs_a = sv.workspace.stores("client-a")[1]
    configs_a.approve("destination/wh", 1, "u-lee")
    configs_a.activate("destination/wh", 1, "u-lee")
    ok = c.post(run_a + "/export/database", json={"destination": "wh", "dataset": "golden_records", "table": "golden", "mode": "create", "reason": WHY}, headers=admin)
    assert ok.status_code == 200, ok.text
    rows_a = sqlite3.connect(warehouse).execute("select count(*) from golden").fetchone()[0]

    # client B: proposing, inspecting, previewing and saving with A's variables are refused, and the message never shows the URL
    no = c.post("/projects/client-b/destinations", json=dest, headers=admin)
    assert no.status_code == 403 and "DEDUPE_ENV_GRANTS" in no.text and str(warehouse) not in no.text
    sql = {"type": "sql", "url_env": "DEDUPE_SRC_CLIENT_A_DB", "table": "customers"}
    for url, body in (("/projects/client-b/connections/inspect?unmask=true", {"connector": sql}),
                      ("/projects/client-b/connections/preview", {"source_id": "theirs", "connector": sql, "fields": {"full_name": "full_name"}}),
                      ("/projects/client-b/connections", {"connection": {"source_id": "theirs", "connector": sql, "fields": {"full_name": "full_name"}}, "reason": WHY})):
        r = c.post(url, json=body, headers=admin)
        assert r.status_code == 403 and "Asha" not in r.text and "ABCDE1234F" not in r.text, (url, r.status_code, r.text[:200])
    # copying A's data source into B brings no permission either
    assert c.post("/projects/client-a/connections", json={"connection": {"source_id": "crm", "connector": sql, "fields": {"full_name": "full_name"}}, "reason": WHY},
                  headers=admin).status_code == 200
    assert c.post("/projects/client-b/connections/copy", json={"from_project": "client-a", "connection_id": "crm"}, headers=admin).status_code == 403

    # versions saved before the grants existed (written straight into B's store) are stopped where they would be used
    configs_b = sv.workspace.stores("client-b")[1]
    configs_b.propose("destination/x", {"destination_id": "x", "url_env": "DEDUPE_DST_CLIENT_A"}, "u-bob", "saved before the grants")
    configs_b.approve("destination/x", 1, "u-lee")
    configs_b.activate("destination/x", 1, "u-lee")
    for mode in ("create", "replace"):
        r = c.post(run_b + "/export/database", json={"destination": "x", "dataset": "person_records", "table": "golden", "mode": mode, "confirm_replace": True,
                                                    "reason": WHY}, headers=admin)
        assert r.status_code == 403, r.text
    assert c.post("/projects/client-b/destinations/x/test", headers=admin).json()["ok"] is False
    configs_b.propose("connection/theirs", {"source_id": "theirs", "connector": sql, "fields": {"full_name": "full_name"}}, "u-bob", "saved before the grants")
    configs_b.approve("connection/theirs", 1, "u-lee")
    configs_b.activate("connection/theirs", 1, "u-lee")
    started = c.post("/projects/client-b/runs", json={"connections": ["theirs"]}, headers=admin)
    assert started.status_code in (409, 422) and "DEDUPE_ENV_GRANTS" in started.text
    tested = c.post("/projects/client-b/connections/theirs/test", headers=admin).json()
    assert tested["ok"] is False and "DEDUPE_ENV_GRANTS" in tested["error"]

    # client A's warehouse is exactly as A left it
    con = sqlite3.connect(warehouse)
    assert [r[0] for r in con.execute("select name from sqlite_master where type='table'")] == ["golden"]
    assert con.execute("select count(*) from golden").fetchone()[0] == rows_a
    con.close()


def test_a_grant_taken_away_stops_the_next_write(tmp_path, output, monkeypatch):
    monkeypatch.setenv("DEDUPE_DST_WH", f"sqlite:///{tmp_path / 'wh.db'}")
    monkeypatch.setenv(envgrants.GRANTS_VAR, "DEDUPE_DST_WH=demo")
    c, s, sv, run = app_with_run(tmp_path, output, ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6, export_limit_per_min=10**6)
    admin = hdr(s, *ADMIN)
    assert c.post("/projects/demo/destinations", json={"destination": {"destination_id": "wh", "url_env": "DEDUPE_DST_WH"}, "reason": WHY}, headers=admin).status_code == 200
    configs = sv.workspace.stores("demo")[1]
    configs.approve("destination/wh", 1, "u-lee")
    configs.activate("destination/wh", 1, "u-lee")
    body = {"destination": "wh", "dataset": "households", "table": "hh", "mode": "create", "reason": WHY}
    assert c.post(run + "/export/database", json=body, headers=admin).status_code == 200
    monkeypatch.setenv(envgrants.GRANTS_VAR, "DEDUPE_DST_WH=another-project")
    assert c.post(run + "/export/database", json={**body, "table": "hh2"}, headers=admin).status_code == 403


def test_an_upload_or_a_file_needs_no_grant(tmp_path, monkeypatch):
    monkeypatch.delenv(envgrants.GRANTS_VAR)
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    c, s, sv, _ = app_with_run(tmp_path, None, ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6)
    r = c.post("/projects/demo/connections/inspect", json={"connector": csv_connection(small_csv(tmp_path / "crm.csv"))["connector"]}, headers=hdr(s, *ADMIN))
    assert r.status_code == 200, r.text
