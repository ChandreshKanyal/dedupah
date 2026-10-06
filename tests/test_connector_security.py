"""A connector config is untrusted input: paths stay inside the data folders, queries cannot change data, sheet ids cannot become other URLs,
and credentials never follow a redirect to another host."""
import urllib.request
from urllib.error import URLError

import pytest
from sqlalchemy import create_engine, text

from dedupe.connectors import create
from dedupe.connectors.guard import (
    ConnectorConfigError,
    SafeRedirect,
    check_identifier,
    check_path,
    check_select_only,
    check_sheet_ids,
)


def test_files_must_be_inside_the_allowed_folders(tmp_path, monkeypatch):
    ok, outside = tmp_path / "ok", tmp_path / "outside"
    ok.mkdir()
    outside.mkdir()
    (ok / "a.csv").write_text("name\nRina\n", encoding="utf-8")
    (outside / "secret.csv").write_text("name\nX\n", encoding="utf-8")
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(ok))
    assert create({"type": "csv", "path": str(ok / "a.csv")}).columns() == ["name"]
    for bad in (outside / "secret.csv", ok / ".." / "outside" / "secret.csv", ok.parent / "elsewhere.csv", "/etc/passwd", "C:/Windows/win.ini"):
        with pytest.raises(ConnectorConfigError) as e:
            create({"type": "csv", "path": str(bad)})
        assert str(tmp_path) not in str(e.value) and "outside the folders" in str(e.value)       # the message does not reveal paths
    with pytest.raises(ConnectorConfigError):
        check_path("a\x00.csv")
    link = ok / "link.csv"
    try:
        link.symlink_to(outside / "secret.csv")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links need extra rights on this machine")
    with pytest.raises(ConnectorConfigError):
        create({"type": "csv", "path": str(link)})                  # a link inside the folder pointing outside it


def test_production_refuses_file_sources_without_listed_folders(monkeypatch, tmp_path):
    monkeypatch.delenv("DEDUPE_DATA_ROOTS", raising=False)
    (tmp_path / "a.csv").write_text("n\n1\n", encoding="utf-8")
    assert create({"type": "csv", "path": str(tmp_path / "a.csv")}).columns() == ["n"]       # development: no restriction
    monkeypatch.setenv("DEDUPE_ENV", "production")
    with pytest.raises(ConnectorConfigError, match="DEDUPE_DATA_ROOTS"):
        create({"type": "csv", "path": str(tmp_path / "a.csv")})


@pytest.mark.parametrize("query", [
    "select * from customers",
    "SELECT * FROM customers;",
    "with c as (select * from customers) select * from c",
    "select * from customers where note = 'a; drop table x'",          # a semicolon inside a string is just text
    "select created, updated_at, replace(name, 'a', 'b') from customers -- delete everything",
    'select "update", `drop` from customers',                            # quoted identifiers that look like keywords
])
def test_read_only_queries_are_accepted(query):
    assert check_select_only(query) == query


@pytest.mark.parametrize("query", [
    "select 1; drop table customers",
    "select 1;\ndelete from customers",
    "update customers set a = 1",
    "delete from customers",
    "with d as (delete from customers returning *) select * from d",
    "select * into backup_customers from customers",
    "select pg_read_file('/etc/passwd')",
    "select lo_import('/etc/passwd')",
    "copy customers to program 'curl evil'",
    "pragma writable_schema = 1",
    "select 1 /* hidden */; truncate customers",
    "insert into customers values (1)",
    "",
])
def test_queries_that_change_data_or_reach_outside_are_refused(query):
    with pytest.raises(ConnectorConfigError):
        check_select_only(query)


def test_table_and_schema_names_must_be_plain_names():
    assert check_identifier("customers_2026", "table") == "customers_2026"
    for bad in ("customers; drop table x", "a b", "a.b", "", "x" * 80, "t--", '"quoted"', None):
        with pytest.raises(ConnectorConfigError):
            check_identifier(bad, "table")


def test_the_sql_connector_applies_the_checks_and_still_reads(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'src.db'}"
    with create_engine(url).begin() as c:
        c.execute(text("CREATE TABLE customers (name TEXT, city TEXT)"))
        c.execute(text("INSERT INTO customers VALUES ('Rina', 'Pune')"))
    monkeypatch.setenv("SRC_URL", url)
    ok = create({"type": "sql", "url_env": "SRC_URL", "query": "select name from customers"})
    assert ok.columns() == ["name"] and next(iter(ok.read_batches())).to_pylist() == [{"name": "Rina"}]
    assert create({"type": "sql", "url_env": "SRC_URL", "table": "customers"}).columns() == ["name", "city"]
    with pytest.raises(ConnectorConfigError):
        create({"type": "sql", "url_env": "SRC_URL", "query": "select 1; drop table customers"})
    with pytest.raises(ConnectorConfigError):
        create({"type": "sql", "url_env": "SRC_URL", "table": "customers; drop table customers"})
    monkeypatch.setenv("DEDUPE_ENV", "production")
    with pytest.raises(ConnectorConfigError, match="url_env"):
        create({"type": "sql", "url": url, "table": "customers"})             # a literal URL carries the password in a config file


def test_sheet_ids_cannot_become_other_urls():
    assert check_sheet_ids("1AbC_dEf-GhIjKlMnOp", "0") == ("1AbC_dEf-GhIjKlMnOp", "0")
    for sid, gid in (("../../etc/passwd", "0"), ("abc?x=1#", "0"), ("1AbC_dEf-GhIjKlMnOp/../x", "0"), ("short", "0"), ("1AbC_dEf-GhIjKlMnOp", "0&format=pdf"),
                     ("1AbC_dEf-GhIjKlMnOp", "x"), ("http://169.254.169.254/", "0")):
        with pytest.raises(ConnectorConfigError):
            check_sheet_ids(sid, gid)
    with pytest.raises(ConnectorConfigError):
        create({"type": "gsheet", "spreadsheet_id": "evil.example.com/x", "gid": "0"})._url()


def test_redirects_only_go_to_google_and_the_token_goes_only_to_docs_google_com():
    h = SafeRedirect()
    req = urllib.request.Request("https://docs.google.com/spreadsheets/d/x/export", headers={"Authorization": "Bearer secret-token"})
    for target in ("https://evil.example.com/steal", "http://docs.google.com/plain", "https://docs.google.com.evil.example/x", "https://169.254.169.254/latest/meta-data"):
        with pytest.raises(URLError):
            h.redirect_request(req, None, 302, "Found", {}, target)
    same = h.redirect_request(req, None, 302, "Found", {}, "https://docs.google.com/spreadsheets/d/x/export?second=1")
    assert same.get_header("Authorization") == "Bearer secret-token"
    cdn = h.redirect_request(req, None, 302, "Found", {}, "https://doc-0c-1k-sheets.googleusercontent.com/export/abc")
    assert cdn.get_header("Authorization") is None and "Authorization" not in cdn.headers          # the token does not travel to the CDN host
