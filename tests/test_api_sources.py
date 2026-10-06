"""Sources and mapping from the API: untrusted settings are refused without echoing anything, samples are masked, proposals are checked against the
real source, go through approval, and a run uses the ACTIVE version."""
import csv
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from test_pipeline import _setup

from dedupe.api import ApiSettings, create_app
from dedupe.api.devtoken import issue

SECRET = "s" * 40
PANS = ["ABCDE1234F", "PQRST5678K", "LMNOP9012Z", "QWERT3456A", "ZXCVB7890B", "ASDFG1234H"]


def hdr(s, role, sub=None):
    return {"Authorization": "Bearer " + issue(s, sub or f"u-{role}", [role])}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    data = tmp_path / "crm.csv"
    with data.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cust_id", "full_name", "dob", "mobile", "pan", "city", "internal_note"])
        for i in range(12):
            w.writerow([f"C{i:03d}", f"Customer Number{i} Kumar", "1980-05-1" + str(i % 9), f"98765432{i:02d}", PANS[i % 6], "Pune", "vip client"])
    db = tmp_path / "ext.db"
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE people (pid TEXT, nm TEXT, ph TEXT)"))
        c.execute(text("INSERT INTO people VALUES ('1','Asha Verma','9876543210'), ('2','Ravi Rao','9123456780')"))
    monkeypatch.setenv("DEDUPE_SRC_EXT", f"sqlite:///{db}")
    monkeypatch.setenv("DEDUPE_SRC_BROKEN", "sqlite:////no/such/dir/hunter2-password.db")
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp_path / "work", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    app = create_app(s)
    return {"c": TestClient(app), "s": s, "sv": app.state.services, "csv": str(data), "dir": tmp_path}


CSV_SOURCE = lambda path: {"source_id": "crm", "connector": {"type": "csv", "path": path}, "record_id": "cust_id",  # noqa: E731
                           "fields": {"full_name": "full_name", "dob": "dob", "mobile": "mobile", "pan": "pan", "city": "city"}, "attributes": ["internal_note"]}


def test_inspect_lists_columns_suggests_a_mapping_and_masks_the_sample(world):
    c, s, sv = world["c"], world["s"], world["sv"]
    r = c.post("/sources/inspect", json={"connector": {"type": "csv", "path": world["csv"]}}, headers=hdr(s, "platform_admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["cust_id", "full_name", "dob", "mobile", "pan", "city", "internal_note"]
    mapped = {x["field"]: x["column"] for x in body["suggestions"]}
    assert mapped["full_name"] == "full_name" and mapped["pan"] == "pan" and mapped["mobile"] == "mobile" and mapped["dob"] == "dob"
    assert len(body["sample"]) == 5 and body["sample"][0]["pan"].startswith("AB") and "ABCDE1234F" not in r.text and "Customer Number0" not in r.text
    events = [e for e in sv.seclog.events(limit=50) if e["event"] == "config_change"]
    assert events[-1]["detail"]["action"] == "source_inspect" and events[-1]["actor"] == "u-platform_admin"
    assert "ABCDE1234F" not in json.dumps(sv.seclog.events(limit=50))
    assert c.get("/sources/options", headers=hdr(s, "platform_admin")).json()["connectors"]["csv"]["settings"] == ["delimiter", "encoding", "path"]


def test_a_database_source_works_with_an_environment_variable_name(world):
    c, s, _ = world["c"], world["s"], None
    r = c.post("/sources/inspect", json={"connector": {"type": "sql", "url_env": "DEDUPE_SRC_EXT", "table": "people"}}, headers=hdr(s, "platform_admin"))
    assert r.status_code == 200 and r.json()["columns"] == ["pid", "nm", "ph"] and "Asha Verma" not in r.text


@pytest.mark.parametrize("connector, fragment", [
    ({"type": "csv", "path": "x.csv", "password": "p"}, "not allowed"),                                                 # unknown setting
    ({"type": "sql", "url": "postgresql://u:pw@h/db", "table": "t"}, "not allowed"),                                    # a literal URL with a password
    ({"type": "sql", "url_env": "DEDUPE_DATA_KEY", "table": "t"}, "starting with DEDUPE_SRC_"),                           # pointing at one of our own secrets
    ({"type": "sql", "url_env": "HOME", "table": "t"}, "starting with DEDUPE_SRC_"),
    ({"type": "ftp", "path": "x"}, "unknown connector type"),
    ({"type": "sql", "url_env": "DEDUPE_SRC_EXT", "query": "DELETE FROM people"}, "SELECT"),                               # not a read
    ({"type": "sql", "url_env": "DEDUPE_SRC_EXT", "query": "SELECT 1; DROP TABLE people"}, ""),
    ({"type": "csv", "path": "/etc/passwd"}, ""),                                                                         # outside the data folders
])
def test_untrusted_source_settings_are_refused(world, connector, fragment):
    c, s = world["c"], world["s"]
    r = c.post("/sources/inspect", json={"connector": connector}, headers=hdr(s, "platform_admin"))
    assert r.status_code == 422 and fragment in r.json()["error"]["message"]
    assert "root:" not in r.text


def test_errors_never_repeat_paths_urls_or_passwords(world):
    c, s = world["c"], world["s"]
    h = hdr(s, "platform_admin")
    missing = c.post("/sources/inspect", json={"connector": {"type": "csv", "path": str(world["dir"] / "nope.csv")}}, headers=h)
    assert missing.status_code == 422 and "nope.csv" not in missing.text and "not found" in missing.json()["error"]["message"]
    broken = c.post("/sources/inspect", json={"connector": {"type": "sql", "url_env": "DEDUPE_SRC_BROKEN", "table": "people"}}, headers=h)
    assert broken.status_code == 422 and "hunter2" not in broken.text and "no/such/dir" not in broken.text
    unset = c.post("/sources/inspect", json={"connector": {"type": "sql", "url_env": "DEDUPE_SRC_NOT_SET", "table": "people"}}, headers=h)
    assert unset.status_code == 422


def test_preview_shows_coverage_and_masked_rows_and_names_a_missing_column(world):
    c, s = world["c"], world["s"]
    h = hdr(s, "platform_admin")
    r = c.post("/sources/preview", json=CSV_SOURCE(world["csv"]), headers=h)
    assert r.status_code == 200
    v = r.json()
    assert v["sample_size"] == 12 and v["filled"]["pan"] == 12 and v["filled"]["full_name"] == 12 and len(v["rows"]) == 5
    assert v["rows"][0]["source_id"] == "crm" and "ABCDE1234F" not in r.text and "Customer Number" not in r.text
    bad = {**CSV_SOURCE(world["csv"]), "fields": {"full_name": "fullname_typo"}}
    e = c.post("/sources/preview", json=bad, headers=h)
    assert e.status_code == 422 and "fullname_typo" in e.json()["error"]["message"]
    assert c.get("/sources/options", headers=hdr(s, "steward")).status_code == 403                                      # only whoever manages sources


def test_a_source_is_proposed_after_a_real_check_approved_by_someone_else_and_shows_in_the_history(world):
    c, s, sv = world["c"], world["s"], world["sv"]
    admin, approver = hdr(s, "platform_admin", "u-ops"), hdr(s, "approver", "u-lee")
    content = {"sources": [CSV_SOURCE(world["csv"])]}
    bad = {"sources": [{**CSV_SOURCE(world["csv"]), "fields": {"full_name": "typo_column"}}]}
    r = c.post("/sources/propose", json={"content": bad, "reason": "first attempt, typo"}, headers=admin)
    assert r.status_code == 422 and "typo_column" in r.json()["error"]["message"] and sv.configs.versions("sources") == []
    for broken in ({"sources": []}, {"sources": [{**CSV_SOURCE(world["csv"]), "fields": {"city": "city"}}]},
                   {"sources": [CSV_SOURCE(world["csv"]), CSV_SOURCE(world["csv"])]}, {"sources": [{"source_id": "x"}]}):
        assert c.post("/sources/propose", json={"content": broken, "reason": "should be refused"}, headers=admin).status_code == 422
    ok = c.post("/sources/propose", json={"content": content, "reason": "connect the CRM export"}, headers=admin)
    assert ok.status_code == 200 and ok.json()["version"] == 1
    assert c.post("/config/sources/propose", json={"content": content, "reason": "bypass the source checks"}, headers=hdr(s, "rule_admin")).status_code == 422
    assert c.post("/config/sources/versions/1/approve", json={}, headers=admin).status_code == 403                    # the person who manages sources does not approve them
    assert c.post("/config/sources/versions/1/approve", json={"reason": "checked mapping"}, headers=approver).status_code == 200
    assert c.post("/config/sources/versions/1/activate", json={}, headers=approver).status_code == 200
    second = {"sources": [{**CSV_SOURCE(world["csv"]), "fields": {**CSV_SOURCE(world["csv"])["fields"], "city": "internal_note"}}]}
    assert c.post("/sources/propose", json={"content": second, "reason": "map city from the note column"}, headers=admin).json()["version"] == 2
    diff = c.get("/config/sources/diff?old=1&new=2", headers=hdr(s, "viewer")).json()
    assert diff == ["source 'crm': field city: \"city\" -> \"internal_note\""]
    assert any(e["detail"].get("action") == "propose" for e in sv.seclog.events(limit=100) if e["event"] == "config_change")


def test_a_run_uses_the_active_sources_version_and_records_it(tmp_path, monkeypatch):
    syn, sources_file = _setup(tmp_path, seed=77, households=40)
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    content = json.loads(sources_file.read_text(encoding="utf-8"))
    for src in content["sources"]:                                                                                       # the synthetic config uses relative paths
        if "path" in src["connector"]:
            src["connector"]["path"] = str((tmp_path / src["connector"]["path"]).resolve()) if not str(src["connector"]["path"]).startswith(str(tmp_path)) else src["connector"]["path"]
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp_path / "work", database_url=f"sqlite:///{tmp_path / 'api.db'}")   # no pipeline_sources
    app = create_app(s)
    c = TestClient(app)
    assert c.post("/runs", json={}, headers=hdr(s, "platform_admin")).status_code == 409                                  # nothing configured yet
    r = c.post("/sources/propose", json={"content": content, "reason": "connect all three synthetic sources"}, headers=hdr(s, "platform_admin", "u-ops"))
    assert r.status_code == 200, r.text
    c.post("/config/sources/versions/1/approve", json={}, headers=hdr(s, "approver", "u-lee"))
    c.post("/config/sources/versions/1/activate", json={}, headers=hdr(s, "approver", "u-lee"))
    job = c.post("/runs", json={"reason": "first run from the screens"}, headers=hdr(s, "platform_admin"))
    assert job.status_code == 202
    done = app.state.services.jobs.wait(job.json()["id"], 300)
    assert done.status == "succeeded", (tmp_path / "work" / "jobs" / f"{done.id}.log").read_text()[-1500:]
    latest = c.get("/runs/latest", headers=hdr(s, "viewer")).json()
    assert latest["configs"]["sources"] == {"source": "store", "version": 1, "hash": latest["configs"]["sources"]["hash"]} and latest["records"] > 100
    assert json.loads((tmp_path / "work" / "config_used" / "sources.json").read_text(encoding="utf-8"))["sources"][0]["source_id"] == content["sources"][0]["source_id"]
