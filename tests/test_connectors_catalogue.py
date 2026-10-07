"""The connector catalogue: every file format read from real files, SQLite and DuckDB files, the database URLs each driver documents, the safety
checks shared by all connectors, the catalogue itself (forms, logos, allow-lists) and what the screens get from the API."""
import json
import sqlite3
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dedupe.connectors import REGISTRY, create
from dedupe.connectors.catalogue import BY_TYPE, C
from dedupe.connectors.guard import ConnectorConfigError, check_host, check_url
from dedupe.connectors.records import DriverMissing
from dedupe.connectors.sql import build_url
from dedupe.mapping.check import SourceProblem, check_connector_settings, inspect_source

LOGOS = Path(__file__).resolve().parents[1] / "src" / "dedupe" / "api" / "ui" / "logos"
ROWS = [("C1", "Rahul Sharma", "1985-05-12", "9811122222"), ("C2", "Anita Rao", "1990-01-31", "9822233333"), ("C3", "Priya Nair", None, "7012345678")]


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    monkeypatch.delenv("DEDUPE_SOURCE_HOSTS", raising=False)
    return tmp_path


def read_all(conf: dict) -> pa.Table:
    c = create(conf)
    c.test_connection()
    t = pa.concat_tables(list(c.read_batches(2)))
    assert t.column_names == c.columns() and all(f.type == pa.string() for f in t.schema)
    return t


# ---- files ----------------------------------------------------------------------------------------------------------------------
def test_every_file_format_reads_the_same_three_people(tmp_path):
    import fastavro
    import xlwt
    from openpyxl import Workbook
    recs = [{"id": i, "name": n, "dob": d, "mobile": m} for i, n, d, m in ROWS]
    (tmp_path / "p.csv").write_text("id,name,dob,mobile\n" + "\n".join(",".join(x or "" for x in r) for r in ROWS), encoding="utf-8")
    (tmp_path / "p.tsv").write_text("id\tname\tdob\tmobile\n" + "\n".join("\t".join(x or "" for x in r) for r in ROWS), encoding="utf-8")
    wb = Workbook()
    wb.active.append(["id", "name", "dob", "mobile"])
    for r in ROWS:
        wb.active.append(list(r))
    wb.save(tmp_path / "p.xlsx")
    old = xlwt.Workbook()
    sh = old.add_sheet("people")
    for j, h in enumerate(["id", "name", "dob", "mobile"]):
        sh.write(0, j, h)
    for i, r in enumerate(ROWS, 1):
        for j, v in enumerate(r):
            if v is not None:
                sh.write(i, j, int(v) if j == 3 else v)                    # mobiles stored as numbers, as old sheets do
    old.save(str(tmp_path / "p.xls"))
    (tmp_path / "p.json").write_text(json.dumps({"data": {"customers": recs}}), encoding="utf-8")
    (tmp_path / "p.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    pq.write_table(pa.table({k: [r[k] for r in recs] for k in recs[0]}), tmp_path / "p.parquet")
    schema = {"type": "record", "name": "P", "fields": [{"name": k, "type": ["null", "string"]} for k in recs[0]]}
    with open(tmp_path / "p.avro", "wb") as f:
        fastavro.writer(f, schema, recs)
    (tmp_path / "p.txt").write_text("HEADER LINE\n" + "\n".join(f"{i:<4}{n:<14}{d or '':<10}{m}" for i, n, d, m in ROWS) + "\n", encoding="utf-8")
    confs = {"csv": {"type": "csv", "path": str(tmp_path / "p.csv")}, "tsv": {"type": "tsv", "path": str(tmp_path / "p.tsv")},
             "xlsx": {"type": "xlsx", "path": str(tmp_path / "p.xlsx")}, "xls": {"type": "xls", "path": str(tmp_path / "p.xls"), "sheet": "people"},
             "json": {"type": "json", "path": str(tmp_path / "p.json"), "records_path": "data.customers"},
             "jsonl": {"type": "jsonl", "path": str(tmp_path / "p.jsonl")}, "parquet": {"type": "parquet", "path": str(tmp_path / "p.parquet")},
             "avro": {"type": "avro", "path": str(tmp_path / "p.avro")},
             "fixed_width": {"type": "fixed_width", "path": str(tmp_path / "p.txt"), "widths": "id:4, name:14, dob:10, mobile:10", "skip_rows": "1"}}
    for kind, conf in confs.items():
        t = read_all(conf)
        assert t.column_names == ["id", "name", "dob", "mobile"], kind
        got = [tuple(r.values()) for r in t.to_pylist()]
        assert [g[1] for g in got] == ["Rahul Sharma", "Anita Rao", "Priya Nair"], kind
        assert got[0][3] == "9811122222" and got[0][2] in ("1985-05-12", "1985-05-12 00:00:00"), (kind, got[0])
        assert got[2][2] in (None, ""), kind


def test_xml_records_nested_and_repeated_elements_and_no_external_entities(tmp_path):
    (tmp_path / "c.xml").write_text("""<?xml version="1.0"?><Customers xmlns="urn:bank">
      <Customer id="C1"><Name>Rahul Sharma</Name><Address><City>Pune</City><Pin>411045</Pin></Address><Phone>98111 22222</Phone><Phone>7012345678</Phone></Customer>
      <Customer id="C2"><Name>Anita Rao</Name><Address><City>Mumbai</City></Address></Customer></Customers>""", encoding="utf-8")
    t = read_all({"type": "xml", "path": str(tmp_path / "c.xml"), "record_tag": "Customer"})
    assert t.column_names == ["@id", "Name", "Address.City", "Address.Pin", "Phone"]
    assert t.to_pylist()[0] == {"@id": "C1", "Name": "Rahul Sharma", "Address.City": "Pune", "Address.Pin": "411045", "Phone": "98111 22222 | 7012345678"}
    (tmp_path / "xxe.xml").write_text('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r><Customer><Name>&x;</Name></Customer></r>',
                                      encoding="utf-8")
    with pytest.raises(Exception, match="(?i)dtd|entit"):
        read_all({"type": "xml", "path": str(tmp_path / "xxe.xml"), "record_tag": "Customer"})


def test_a_folder_of_daily_files_is_one_source_with_the_file_name_on_each_row(tmp_path):
    d = tmp_path / "drops"
    d.mkdir()
    (d / "2026-10-01.csv").write_text("id,name\nC1,Rahul\n", encoding="utf-8")
    (d / "2026-10-02.csv").write_text("id,name,mobile\nC2,Anita,9822233333\n", encoding="utf-8")
    (d / "notes.txt").write_text("ignore me", encoding="utf-8")
    t = read_all({"type": "folder", "path": str(d), "pattern": "*.csv", "format": "csv"})
    assert t.column_names == ["id", "name", "mobile", "source_file"]
    assert t.to_pylist() == [{"id": "C1", "name": "Rahul", "mobile": None, "source_file": "2026-10-01.csv"},
                             {"id": "C2", "name": "Anita", "mobile": "9822233333", "source_file": "2026-10-02.csv"}]
    for bad in ("../*.csv", "/etc/*", "C:*"):
        with pytest.raises(ConnectorConfigError):
            create({"type": "folder", "path": str(d), "pattern": bad})


def test_files_outside_the_allowed_folders_are_refused(tmp_path):
    for t in ("json", "xml", "avro", "fixed_width", "xls", "folder", "sqlite", "duckdb"):
        with pytest.raises(ConnectorConfigError):
            create({"type": t, "path": str(Path(tmp_path).parent / "elsewhere.dat"), "table": "x", "widths": "a:1", "record_tag": "R"})


@pytest.mark.skipif(sys.platform == "win32", reason="pyarrow has no ORC reader on Windows")
def test_orc(tmp_path):
    import pyarrow.orc as orc
    orc.write_table(pa.table({"id": ["C1"], "name": ["Rahul"]}), str(tmp_path / "p.orc"))
    assert read_all({"type": "orc", "path": str(tmp_path / "p.orc")}).to_pylist() == [{"id": "C1", "name": "Rahul"}]


# ---- database files and URLs ----------------------------------------------------------------------------------------------------------
def test_sqlite_and_duckdb_files_are_read_only(tmp_path):
    db = tmp_path / "core.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE customers (id TEXT, name TEXT, dob DATE)")
        con.executemany("INSERT INTO customers VALUES (?, ?, ?)", [(r[0], r[1], r[2]) for r in ROWS])
    t = read_all({"type": "sqlite", "path": str(db), "table": "customers"})
    assert t.num_rows == 3 and t.column_names == ["id", "name", "dob"]
    assert read_all({"type": "sqlite", "path": str(db), "query": "SELECT name FROM customers WHERE dob IS NOT NULL"}).num_rows == 2
    with pytest.raises(ConnectorConfigError):
        create({"type": "sqlite", "path": str(db), "query": "DELETE FROM customers"})
    ddb = tmp_path / "w.duckdb"
    con = duckdb.connect(str(ddb))
    con.execute("CREATE SCHEMA crm; CREATE TABLE crm.people AS SELECT * FROM (VALUES ('C1', 'Rahul', DATE '1985-05-12', 42)) t(id, name, dob, score)")
    con.close()
    t = read_all({"type": "duckdb", "path": str(ddb), "schema": "crm", "table": "people"})
    assert t.to_pylist() == [{"id": "C1", "name": "Rahul", "dob": "1985-05-12", "score": "42"}]
    with pytest.raises(duckdb.Error, match="(?i)external|disabled|permission"):     # the query may not read files from the server's disk
        read_all({"type": "duckdb", "path": str(ddb), "query": f"SELECT * FROM read_csv('{(tmp_path / 'x.csv').as_posix()}')"})


@pytest.mark.parametrize("engine,conf,expected", [
    ("postgres", {"host": "db.bank.in", "database": "core", "username": "reader", "ssl_mode": "verify-full"},
     "postgresql+psycopg2://reader:***@db.bank.in:5432/core?sslmode=verify-full"),
    ("mysql", {"host": "db.bank.in", "port": "3307", "database": "crm", "username": "reader"}, "mysql+pymysql://reader:***@db.bank.in:3307/crm?charset=utf8mb4"),
    ("mariadb", {"host": "db.bank.in", "database": "crm", "username": "reader"}, "mariadb+pymysql://reader:***@db.bank.in:3306/crm?charset=utf8mb4"),
    ("sqlserver", {"host": "sql.bank.in", "database": "CRM", "username": "reader"},
     "mssql+pyodbc://reader:***@sql.bank.in:1433/CRM?Encrypt=yes&TrustServerCertificate=no&driver=ODBC+Driver+18+for+SQL+Server"),
    ("oracle", {"host": "ora.bank.in", "username": "reader", "service_name": "FINPDB"}, "oracle+oracledb://reader:***@ora.bank.in:1521?service_name=FINPDB"),
    ("db2", {"host": "db2.bank.in", "database": "CORE", "username": "reader"}, "db2+ibm_db://reader:***@db2.bank.in:50000/CORE"),
    ("redshift", {"host": "rs.bank.in", "database": "dw", "username": "reader", "ssl_mode": "verify-full"},
     "redshift+redshift_connector://reader:***@rs.bank.in:5439/dw?sslmode=verify-full"),
    ("snowflake", {"account": "myorg-bank", "username": "reader", "database": "CRM", "schema": "PUBLIC", "warehouse": "WH", "role": "READER"},
     "snowflake://reader:***@myorg-bank/CRM/PUBLIC?role=READER&warehouse=WH"),
    ("bigquery", {"project": "bank-dw", "dataset": "crm"}, "bigquery://bank-dw/crm"),
    ("databricks", {"host": "adb-1.azuredatabricks.net", "http_path": "/sql/1.0/warehouses/abc", "catalog": "main", "schema": "crm", "token_env": "DEDUPE_SRC_T"},
     "databricks://token:***@adb-1.azuredatabricks.net:443?catalog=main&http_path=%2Fsql%2F1.0%2Fwarehouses%2Fabc&schema=crm"),
])
def test_database_urls_follow_each_drivers_documented_format(engine, conf, expected, monkeypatch):
    monkeypatch.setenv("DEDUPE_SRC_PW", "s3cr@t:/pw")
    monkeypatch.setenv("DEDUPE_SRC_T", "dapi123")
    url, _ = build_url(engine, {"password_env": "DEDUPE_SRC_PW", **conf})
    assert url.render_as_string(hide_password=True) == expected
    if engine not in ("bigquery", "databricks"):                       # the password reaches the driver whole, and escaped inside the URL
        assert url.password == "s3cr@t:/pw" and "s3cr%40t%3A%2Fpw@" in url.render_as_string(hide_password=False)


def test_rds_picks_the_engine_and_a_missing_driver_is_explained(monkeypatch):
    monkeypatch.setenv("DEDUPE_SRC_PW", "pw")
    rds = create({"type": "rds", "engine": "mysql", "host": "x.ap-south-1.rds.amazonaws.com", "database": "crm", "username": "r", "password_env": "DEDUPE_SRC_PW",
                  "table": "customers"})
    assert rds.engine.dialect.name == "mysql"
    for t, conf in {"snowflake": {"account": "a-b", "username": "r", "database": "D"}, "db2": {"host": "h.in", "database": "D", "username": "r"}}.items():
        with pytest.raises(DriverMissing, match=f"dedupe\\[{t}\\]"):
            create({"type": t, "password_env": "DEDUPE_SRC_PW", "table": "customers", **conf})


# ---- shared safety checks ---------------------------------------------------------------------------------------------------------------
def test_hosts_never_reach_cloud_metadata_and_follow_the_allow_list(monkeypatch):
    for bad in ("169.254.169.254", "metadata.google.internal", "fd00:ec2::254", "bad host", "x" * 300):
        with pytest.raises(ConnectorConfigError):
            check_host(bad)
    assert check_host("127.0.0.1") == "127.0.0.1"                           # a database on the same machine is normal
    monkeypatch.setenv("DEDUPE_SOURCE_HOSTS", "db.bank.in, *.rds.amazonaws.com")
    assert check_host("db.bank.in") and check_host("x.ap-south-1.rds.amazonaws.com")
    with pytest.raises(ConnectorConfigError, match="DEDUPE_SOURCE_HOSTS"):
        check_host("evil.example.com")
    with pytest.raises(ConnectorConfigError, match="https"):
        check_url("http://db.bank.in/api")
    with pytest.raises(ConnectorConfigError, match="user name or password"):
        check_url("https://u:p@db.bank.in/api")


def test_settings_check_uses_the_catalogue_as_its_allow_list():
    with pytest.raises(SourceProblem, match="not allowed"):
        check_connector_settings({"type": "postgres", "host": "h", "password": "plain"})
    with pytest.raises(SourceProblem, match="DEDUPE_SRC_"):
        check_connector_settings({"type": "s3", "bucket": "b", "path": "x.csv", "access_key_env": "AWS_SECRET_ACCESS_KEY"})
    with pytest.raises(SourceProblem, match="Host is required"):
        check_connector_settings({"type": "postgres", "database": "d", "username": "u", "password_env": "DEDUPE_SRC_P", "table": "t"})
    check_connector_settings({"type": "postgres", "url_env": "DEDUPE_SRC_URL", "table": "t"})             # a full URL replaces the form fields
    with pytest.raises(SourceProblem, match="Record element is required"):
        check_connector_settings({"type": "xml", "path": "x.xml"})
    with pytest.raises(SourceProblem, match="Server host key is required"):
        check_connector_settings({"type": "sftp", "host": "h", "username": "u", "path": "/x.csv"})
    check_connector_settings({"type": "rest_api", "url": "https://api.bank.in/c"})                       # defaults fill the rest


def test_the_catalogue_is_complete_and_every_logo_is_shipped_and_clean():
    assert len(C) >= 35 and len({c["type"] for c in C}) == len(C)
    for c in C:
        assert c["type"] in REGISTRY and all(a in REGISTRY for a in c["aliases"]), c["type"]
        assert (LOGOS / f"{c['logo']}.svg").is_file(), c["logo"]
        assert c["tested"] in ("live", "simulated", "unverified") and c["summary"] and c["group"]
        names = [s["name"] for s in c["settings"]]
        assert len(names) == len(set(names)), c["type"]
        for s in c["settings"]:
            assert s["kind"] in ("text", "number", "select", "env", "textarea", "path")
            assert not s["name"].endswith("_env") or s["kind"] == "env", (c["type"], s["name"])
            assert "password" not in s["name"] or s["name"].endswith("_env")       # no setting ever holds a secret
    for svg in LOGOS.glob("*.svg"):
        text = svg.read_text(encoding="utf-8").lower()
        assert "<script" not in text and "onload" not in text and "javascript:" not in text and "foreignobject" not in text, svg.name
    assert BY_TYPE["excel"]["type"] == "xlsx" and BY_TYPE["aurora"]["type"] == "rds"


def test_inspect_explains_a_missing_driver_without_a_stack_trace(monkeypatch):
    monkeypatch.setenv("DEDUPE_SRC_PW", "pw")
    with pytest.raises(SourceProblem, match="not installed"):
        inspect_source({"type": "snowflake", "account": "a-b", "username": "r", "database": "D", "password_env": "DEDUPE_SRC_PW", "table": "t"})


def test_the_security_log_stays_one_chain_when_many_requests_write_at_once(tmp_path):
    """Found while testing the connection screens: simultaneous rate-limit events both took the same next id (SQLite locks only at the insert)."""
    from concurrent.futures import ThreadPoolExecutor

    from dedupe.api.seclog import SecurityLog
    log = SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}")
    with ThreadPoolExecutor(16) as pool:
        ids = list(pool.map(lambda i: log.record("rate_limited", actor=f"u{i}", route="/x"), range(200)))
    assert sorted(ids) == list(range(1, 201)) and log.verify()["ok"]
