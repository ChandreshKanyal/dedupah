"""Fixes for the findings of the cloud review (2026-10-08): DNS rebinding around the host checks, the mobile data-quality count, url_env for
BigQuery/Databricks, the SFTP size cap during the download, '#n' record-id suffixes that collide, and record sources read twice."""
import csv
import json
import socket

import pytest

from dedupe.connectors import guard
from dedupe.connectors.guard import ConnectorConfigError, check_peer, resolve_checked
from dedupe.connectors.records import RecordSource
from dedupe.mapping.check import check_connector_settings


class FakeSock:
    def __init__(self, ip):
        self.ip, self.closed = ip, False

    def getpeername(self):
        return (self.ip, 443)

    def close(self):
        self.closed = True


def test_a_name_that_starts_resolving_to_the_metadata_service_is_refused_at_connect_time(monkeypatch):
    s = FakeSock("169.254.169.254")
    with pytest.raises(ConnectorConfigError, match="metadata"):
        check_peer(s)
    assert s.closed
    check_peer(FakeSock("10.1.2.3"))                                       # an ordinary internal address is fine
    answers = iter([[(socket.AF_INET, 0, 0, "", ("10.0.0.5", 0))], [(socket.AF_INET, 0, 0, "", ("169.254.169.254", 0))]])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: next(answers))
    with pytest.raises(ConnectorConfigError, match="metadata"):           # first answer passes check_host, the pinned lookup sees the switch
        resolve_checked("api.partner.example")


def test_the_https_opener_checks_the_address_it_really_reached():
    handlers = [type(h).__name__ for h in guard.same_host_opener().handlers]
    assert "_CheckedHTTPSHandler" in handlers and "_CheckedHTTPHandler" in handlers and "HTTPSHandler" not in handlers


def test_postgres_is_pinned_to_the_checked_address(monkeypatch):
    from dedupe.connectors.sql import build_url
    monkeypatch.setenv("DEDUPE_SRC_PW", "pw")
    url, _ = build_url("postgres", {"host": "127.0.0.1", "database": "d", "username": "u", "password_env": "DEDUPE_SRC_PW"})
    assert url.query["hostaddr"] == "127.0.0.1"


def test_mobile_quality_is_judged_on_the_mobile_column_itself():
    from dedupe.standardise.run import _VALIDATION
    raw = {"mobile": "not a phone", "mobile_alt": "9811122222"}
    assert _VALIDATION["mobile"](raw, {"mobile": "9811122222"}) is False   # the cleaned value came from the second column
    assert _VALIDATION["mobile"]({"mobile": "+91 98111 22222"}, {"mobile": "9811122222"}) is True


def test_bigquery_and_databricks_accept_a_complete_url():
    check_connector_settings({"type": "bigquery", "url_env": "DEDUPE_SRC_BQ_URL", "table": "customers"})
    check_connector_settings({"type": "databricks", "url_env": "DEDUPE_SRC_DBX_URL", "table": "customers"})


def test_the_sftp_cap_holds_even_when_the_server_under_reports_the_size(monkeypatch, tmp_path, request):
    paramiko = pytest.importorskip("paramiko")
    from dedupe.connectors import create, storage
    server = request.getfixturevalue("sftp_server")
    monkeypatch.setenv("DEDUPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("DEDUPE_SRC_SFTP_PW", "pw-123")
    monkeypatch.setattr(storage, "MAX_DOWNLOAD", 20)
    real_stat = paramiko.SFTPClient.stat
    monkeypatch.setattr(paramiko.SFTPClient, "stat", lambda self, p: type("A", (), {"st_size": 1})() if p.endswith(".csv") else real_stat(self, p))
    c = create({"type": "sftp", "host": "127.0.0.1", "username": "bank", "password_env": "DEDUPE_SRC_SFTP_PW", "path": "/outbound/customers.csv",
                "format": "csv", **server})
    with pytest.raises(ConnectorConfigError, match="larger than the allowed size"):
        c.columns()
    assert not list((tmp_path / "work" / "incoming").glob("*")), "a partial download must not stay on disk"


def test_renamed_duplicate_ids_never_collide_with_real_ids(tmp_path):
    import duckdb

    from dedupe.ingest import ingest
    with (tmp_path / "s.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name"])
        w.writerows([["ACME001", "Rahul"], ["ACME001#2", "Anita"], ["ACME001", "Rahul S"], ["B7", "Priya"]])
    (tmp_path / "c.json").write_text(json.dumps({"sources": [{"source_id": "s", "connector": {"type": "csv", "path": str(tmp_path / "s.csv")},
                                                              "record_id": "id", "fields": {"full_name": "name"}}]}), encoding="utf-8")
    out = ingest(tmp_path / "c.json", tmp_path / "canonical.parquet")
    ids = [r[0] for r in duckdb.sql(f"SELECT source_record_id FROM '{tmp_path / 'canonical.parquet'}'").fetchall()]
    assert len(ids) == len(set(ids)) == 4 and "ACME001#2" in ids and "ACME001#dup1-2" in ids
    assert out["duplicate_source_record_ids"] == {"s": 1}


def test_a_record_source_is_read_once_per_run():
    class Counting(RecordSource):
        calls = 0

        def _records(self):
            Counting.calls += 1
            for i in range(2500):
                yield {"id": i, "name": {"first": f"P{i}"}}

    c = Counting()
    assert c.columns() == ["id", "name.first"]
    rows = sum(t.num_rows for t in c.read_batches(1000))
    assert rows == 2500 and Counting.calls == 1
    assert sum(t.num_rows for t in c.read_batches(1000)) == 2500 and Counting.calls == 2      # a second read starts a fresh stream
