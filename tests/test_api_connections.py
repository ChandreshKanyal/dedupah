"""Connections and uploads: untrusted settings refused without echoing anything, samples masked, proposals checked against the real source and
approved by someone else, uploads checked (type by content, zip bombs, headers, size) and stored without the original bytes."""
import io
import json
import zipfile

import pytest
from api_world import app_with_run, csv_connection, hdr, make_live, small_csv
from sqlalchemy import create_engine, text

from dedupe import crypto


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    db = tmp_path / "ext.db"
    with create_engine(f"sqlite:///{db}").begin() as c:
        c.execute(text("CREATE TABLE people (pid TEXT, nm TEXT, ph TEXT)"))
        c.execute(text("INSERT INTO people VALUES ('1','Asha Verma','9876543210'), ('2','Ravi Rao','9123456780')"))
    monkeypatch.setenv("DEDUPE_SRC_EXT", f"sqlite:///{db}")
    monkeypatch.setenv("DEDUPE_SRC_BROKEN", "sqlite:////no/such/dir/hunter2-password.db")
    c, s, sv, _ = app_with_run(tmp_path, None)
    return c, s, sv, small_csv(tmp_path / "crm.csv"), tmp_path


def test_inspect_lists_columns_suggests_a_mapping_and_masks_the_sample(api):
    c, s, sv, path, _ = api
    r = c.post("/connections/inspect", json={"connector": {"type": "csv", "path": str(path)}}, headers=hdr(s, "platform_admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["cust_id", "full_name", "dob", "mobile", "pan", "city", "internal_note"]
    assert {x["field"]: x["column"] for x in body["suggestions"]}["pan"] == "pan"
    assert len(body["sample"]) == 5 and "ABCDE1234F" not in r.text and "Customer Number0" not in r.text
    assert "ABCDE1234F" not in json.dumps(sv.seclog.events(limit=50))


def test_a_database_source_works_with_an_environment_variable_name(api):
    c, s, *_ = api
    r = c.post("/connections/inspect", json={"connector": {"type": "sql", "url_env": "DEDUPE_SRC_EXT", "table": "people"}}, headers=hdr(s, "platform_admin"))
    assert r.status_code == 200 and r.json()["columns"] == ["pid", "nm", "ph"] and "Asha Verma" not in r.text


@pytest.mark.parametrize("connector, fragment", [
    ({"type": "csv", "path": "x.csv", "password": "p"}, "not allowed"),
    ({"type": "sql", "url": "postgresql://u:pw@h/db", "table": "t"}, "not allowed"),
    ({"type": "sql", "url_env": "DEDUPE_DATA_KEY", "table": "t"}, "starting with DEDUPE_SRC_"),
    ({"type": "ftp", "path": "x"}, "unknown connector type"),
    ({"type": "sql", "url_env": "DEDUPE_SRC_EXT", "query": "DELETE FROM people"}, "SELECT"),
    ({"type": "csv", "path": "/etc/passwd"}, ""),
    ({"type": "upload", "upload_id": "../../etc/passwd"}, ""),
])
def test_untrusted_settings_are_refused(api, connector, fragment):
    c, s, *_ = api
    r = c.post("/connections/inspect", json={"connector": connector}, headers=hdr(s, "platform_admin"))
    assert r.status_code == 422 and fragment in r.json()["error"]["message"] and "root:" not in r.text


def test_errors_never_repeat_paths_urls_or_passwords(api):
    c, s, _, _, tmp = api
    h = hdr(s, "platform_admin")
    missing = c.post("/connections/inspect", json={"connector": {"type": "csv", "path": str(tmp / "nope.csv")}}, headers=h)
    assert missing.status_code == 422 and "nope.csv" not in missing.text
    broken = c.post("/connections/inspect", json={"connector": {"type": "sql", "url_env": "DEDUPE_SRC_BROKEN", "table": "people"}}, headers=h)
    assert broken.status_code == 422 and "hunter2" not in broken.text


def test_preview_shows_coverage_and_masked_rows(api):
    c, s, _, path, _ = api
    r = c.post("/connections/preview", json=csv_connection(path), headers=hdr(s, "platform_admin"))
    assert r.status_code == 200 and r.json()["filled"]["pan"] == 12 and "ABCDE1234F" not in r.text
    bad = c.post("/connections/preview", json={**csv_connection(path), "fields": {"full_name": "fullname_typo"}}, headers=hdr(s, "platform_admin"))
    assert bad.status_code == 422 and "fullname_typo" in bad.json()["error"]["message"]


def test_a_connection_is_checked_proposed_approved_by_someone_else_and_versioned(api):
    c, s, sv, path, _ = api
    admin, approver = hdr(s, "platform_admin", "u-ops"), hdr(s, "approver", "u-lee")
    bad = {**csv_connection(path), "fields": {"full_name": "typo_column"}}
    assert c.post("/connections", json={"connection": bad, "reason": "first attempt, typo"}, headers=admin).status_code == 422
    assert c.get("/connections", headers=hdr(s, "viewer")).json() == []
    r = c.post("/connections", json={"connection": csv_connection(path), "reason": "connect the CRM export"}, headers=admin)
    assert r.status_code == 200 and r.json() == {"id": "crm", "version": 1, "status": "proposed", "next": "another person approves it, then makes it live"}
    assert c.post("/connections/crm/versions/1/approve", json={}, headers=admin).status_code == 403
    assert c.post("/connections/crm/versions/1/activate", json={}, headers=approver).status_code == 409                  # not approved yet
    assert c.post("/connections/crm/versions/1/approve", json={"reason": "mapping checked"}, headers=approver).status_code == 200
    assert c.post("/connections/crm/versions/1/activate", json={}, headers=approver).status_code == 200
    second = {**csv_connection(path), "fields": {**csv_connection(path)["fields"], "city": "internal_note"}}
    assert c.post("/connections", json={"connection": second, "reason": "map city from the note column"}, headers=admin).json()["version"] == 2
    listed = c.get("/connections", headers=hdr(s, "viewer")).json()
    assert listed[0]["id"] == "crm" and listed[0]["live_version"] == 1 and listed[0]["latest_status"] == "proposed"
    assert c.get("/connections/crm/diff?old=1&new=2", headers=hdr(s, "viewer")).json() == ["source 'crm': field city: \"city\" -> \"internal_note\""]
    detail = c.get("/connections/crm", headers=hdr(s, "viewer")).json()
    assert detail["live"]["fields"]["city"] == "city" and [v["status"] for v in detail["versions"]] == ["active", "proposed"]


def _xlsx(rows) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_an_excel_upload_becomes_an_encrypted_table_and_a_connection(api):
    c, s, sv, _, tmp = api
    crypto.configure(crypto.parse_key("a2V5LWtleS1rZXkta2V5LWtleS1rZXkta2V5LWtleXg="))
    try:
        data = _xlsx([["Policy No", "Insured Name", "DOB", "PAN"], ["P1", "Asha Verma", "1984-03-12", "ABCDE1234F"], ["P2", "Ravi Rao", "1979-11-02", "PQRST5678K"]])
        r = c.post("/uploads?filename=policies.xlsx", content=data, headers=hdr(s, "platform_admin"))
        assert r.status_code == 201, r.text
        up = r.json()
        assert up["rows"] == 2 and up["columns"] == ["Policy No", "Insured Name", "DOB", "PAN"] and up["kind"] == "xlsx"
        stored = sv.uploads.path(up["upload_id"])
        assert crypto.is_encrypted_file(stored) and b"Asha" not in stored.read_bytes()
        assert list((sv.workspace.upload_dir / "incoming").iterdir()) == []                                    # the original bytes are gone
        conn = {"source_id": "policies", "connector": {"type": "upload", "upload_id": up["upload_id"]}, "record_id": "Policy No",
                "fields": {"full_name": "Insured Name", "dob": "DOB", "pan": "PAN"}}
        prev = c.post("/connections/preview", json=conn, headers=hdr(s, "platform_admin"))
        assert prev.status_code == 200 and prev.json()["filled"] == {"full_name": 2, "dob": 2, "pan": 2}
        assert make_live(c, s, conn) == 1
        assert c.get("/uploads", headers=hdr(s, "platform_admin")).json()[0]["filename"] == "policies.xlsx"
    finally:
        crypto.configure()


def _bomb() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/worksheets/sheet1.xml", "0" * 50_000_000)
    return buf.getvalue()


@pytest.mark.parametrize("name, data, fragment", [
    ("a.xlsx", b"PK\x03\x04 not really a zip", "not a valid Excel"),
    ("bomb.xlsx", None, "compression bomb"),
    ("old.xls", b"\xd0\xcf\x11\xe0 old excel", "old .xls"),
    ("data.csv", "name,dob\nAsha,1984\n".encode("utf-16"), ""),
    ("data.csv", b"name,name\nAsha,Asha\n", "same name"),
    ("data.csv", b",dob\nAsha,1984\n", "needs a name"),
    ("data.csv", b"name,dob\n", "no rows"),
    ("data.exe", b"MZ....", "upload a .csv"),
    ("data.csv", b"PK\x03\x04", "name says otherwise"),
])
def test_bad_uploads_are_refused_and_leave_nothing_behind(api, name, data, fragment):
    c, s, sv, *_ = api
    r = c.post(f"/uploads?filename={name}", content=_bomb() if data is None else data, headers=hdr(s, "platform_admin"))
    assert r.status_code == 422 and fragment in r.json()["error"]["message"]
    assert not list(sv.workspace.upload_dir.glob("*.parquet")) and not list((sv.workspace.upload_dir / "incoming").iterdir())


def test_uploads_are_capped_and_named_safely(tmp_path):
    c, s, sv, _ = app_with_run(tmp_path, None, upload_max_bytes=2048)
    assert c.post("/uploads?filename=big.csv", content=b"a,b\n" + b"1,2\n" * 2000, headers=hdr(s, "platform_admin")).status_code == 413
    r = c.post("/uploads?filename=../../etc/<x>.csv", content=b"a,b\n1,2\n", headers=hdr(s, "platform_admin"))
    assert r.status_code == 201 and r.json()["filename"] == "_x_.csv"
    assert c.post("/uploads?filename=x.csv", content=b"a,b\n1,2\n", headers=hdr(s, "steward")).status_code == 403
