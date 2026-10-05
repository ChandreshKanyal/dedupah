import csv
import json
import sqlite3

import pyarrow as pa
import pytest

from dedupe.connectors import REGISTRY, create
from dedupe.connectors.sheets import GoogleSheetsConnector
from dedupe.evaluation.loaders import load_truth
from dedupe.ingest import ingest
from dedupe.mapping import MappingError, SourceConfig, compile_select, suggest
from dedupe.synthetic import generate


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("d")
    generate(80, seed=5, out=d)
    return d


def test_registry_has_expected_connectors():
    assert {"csv", "tsv", "xlsx", "parquet", "sql", "postgres", "mysql", "gsheet"} <= set(REGISTRY)
    with pytest.raises(ValueError):
        create({"type": "nope"})


def test_csv_and_xlsx_read_everything_as_text(data):
    c = create({"type": "csv", "path": str(data / "bank_crm.csv")})
    c.test_connection()
    t = next(c.read_batches())
    assert t.schema.field("mobile").type == pa.string() and "cust_id" in c.columns()
    x = create({"type": "xlsx", "path": str(data / "insurance_policies.xlsx"), "sheet": "policies"})
    assert "party_name" in x.columns() and next(x.read_batches()).num_rows > 0


def test_batching_covers_all_rows(data):
    c = create({"type": "xlsx", "path": str(data / "insurance_policies.xlsx")})
    total = sum(b.num_rows for b in c.read_batches(batch_size=100))
    assert total == sum(b.num_rows for b in c.read_batches(batch_size=100_000))


def test_parquet_roundtrip(data, tmp_path):
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq
    pq.write_table(pacsv.read_csv(data / "loans.csv"), tmp_path / "l.parquet")
    c = create({"type": "parquet", "path": str(tmp_path / "l.parquet")})
    assert sum(b.num_rows for b in c.read_batches()) > 0 and "pan" in c.columns()


def test_sql_connector_via_sqlite_and_secret_from_env(data, tmp_path, monkeypatch):
    db = tmp_path / "x.db"
    with open(data / "loans.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE loans (loan_id TEXT, applicant_name TEXT, pan TEXT)")
    con.executemany("INSERT INTO loans VALUES (?,?,?)", [(r["loan_id"], r["applicant_name"], r["pan"]) for r in rows])
    con.commit()
    con.close()
    monkeypatch.setenv("TEST_DB_URL", f"sqlite:///{db}")
    c = create({"type": "sql", "url_env": "TEST_DB_URL", "table": "loans"})
    c.test_connection()
    assert c.columns() == ["loan_id", "applicant_name", "pan"]
    assert sum(b.num_rows for b in c.read_batches(batch_size=50)) == len(rows)
    q = create({"type": "sql", "url_env": "TEST_DB_URL", "query": "SELECT loan_id FROM loans WHERE pan <> ''"})
    assert q.columns() == ["loan_id"]
    with pytest.raises(ValueError):
        create({"type": "sql", "url_env": "TEST_DB_URL", "query": "DELETE FROM loans"})
    monkeypatch.delenv("TEST_DB_URL")
    with pytest.raises(ValueError):
        create({"type": "sql", "url_env": "TEST_DB_URL", "table": "loans"})


def test_google_sheets_with_mocked_fetch(monkeypatch):
    monkeypatch.setattr(GoogleSheetsConnector, "_fetch", lambda self: "name,phone\nRaj,98765 43210\nAnu,\n")
    c = create({"type": "gsheet", "spreadsheet_id": "abc"})
    c.test_connection()
    t = next(c.read_batches())
    assert c.columns() == ["name", "phone"] and t["phone"].to_pylist() == ["98765 43210", None]


def _run(cfg, table):
    import duckdb
    con = duckdb.connect()
    con.register("batch", table)
    return con.execute(compile_select(cfg, table.column_names)).to_arrow_table().to_pylist()


def test_mapping_expressions():
    t = pa.table({"id": ["1", "2"], "a": ["x", " "], "b": ["y", "z"], "t": ["PAN", "AADHAAR"], "n": ["AB1", "999"]})
    cfg = SourceConfig(source_id="s", connector={}, record_id="id", fields={
        "full_name": {"concat": ["a", "b"], "sep": "-"}, "email": {"coalesce": ["a", "b"]},
        "pan": {"column": "n", "when": {"t": "PAN"}}, "aadhaar": {"column": "n", "when": {"t": "AADHAAR"}}},
        attributes=["t"])
    r = _run(cfg, t)
    assert r[0]["full_name"] == "x-y" and r[1]["full_name"] == "z"  # blank parts skipped
    assert r[1]["email"] == "z"  # blank coalesced past
    assert r[0]["pan"] == "AB1" and r[1]["pan"] is None and r[1]["aadhaar"] == "999"
    assert json.loads(r[0]["attributes"]) == {"t": "PAN"} and r[0]["source_record_id"] == "1"


def test_mapping_errors_are_clear_and_sql_is_injection_safe():
    cfg = SourceConfig(source_id="s", connector={}, fields={"full_name": "nope"})
    with pytest.raises(MappingError, match="nope"):
        compile_select(cfg, ["a"])
    with pytest.raises(ValueError):
        SourceConfig(source_id="s", connector={}, fields={"not_a_field": "a"})
    t = pa.table({'we"ird': ["v"]})
    cfg = SourceConfig(source_id="it's", connector={}, fields={"full_name": 'we"ird'})
    r = _run(cfg, t)[0]
    assert r["full_name"] == "v" and r["source_id"] == "it's" and r["source_record_id"] == "1"


def test_suggest_finds_the_right_columns(data):
    c = create({"type": "csv", "path": str(data / "bank_crm.csv")})
    got = {s["field"]: s["column"] for s in suggest(c.sample())}
    assert got["pan"] == "pan" and got["email"] == "email" and got["full_name"] == "full_name"
    assert got["dob"] == "dob" and got["mobile"] == "mobile" and got["pincode"] == "pincode"
    l = create({"type": "csv", "path": str(data / "loans.csv")})
    got = {s["field"]: s["column"] for s in suggest(l.sample())}
    assert got["full_name"] == "applicant_name" and got["aadhaar"] == "aadhaar_no" and got["father_name"] == "father_name"


def test_ingest_end_to_end_matches_truth_keys(data, tmp_path):
    import os

    import duckdb
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(data / os.path.basename(s["connector"]["path"]))
    cp = tmp_path / "cfg.json"
    cp.write_text(json.dumps(cfg), encoding="utf-8")
    out = tmp_path / "canonical.parquet"
    stats = ingest(cp, out, batch_size=300)
    assert stats["duplicate_source_record_ids"] == {}
    rec_person, _, _ = load_truth(data)
    keys = {f"{a}:{b}" for a, b in duckdb.sql(f"select source_id, source_record_id from '{out}'").fetchall()}
    assert keys == set(rec_person)
    kyc = duckdb.sql(f"select count(*) filter (pan is not null), count(*) filter (aadhaar is not null) from '{out}' "
                     "where source_id='kyc_documents'").fetchone()
    assert kyc[0] > 0 and kyc[1] > 0
