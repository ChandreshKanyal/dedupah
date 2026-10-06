"""Encryption at rest: a real pipeline run writes only encrypted files, nothing readable leaks to disk (files, spill, summaries), results are the
same as without encryption, wrong/missing/mixed keys are refused, plain data can be converted and re-keyed, and production insists on a key."""
import base64
import os
import threading
import time

import duckdb
import pytest
from fastapi.testclient import TestClient
from test_pipeline import _setup

from dedupe import crypto
from dedupe.api import ApiSettings, create_app
from dedupe.api.devtoken import issue
from dedupe.pipeline import PipelineConfig, run_pipeline


def new_key(key_id="k1"):
    return crypto.parse_key(base64.b64encode(os.urandom(32)).decode(), key_id)


@pytest.fixture(autouse=True)
def _no_key_leaks_between_tests():
    crypto.configure(None)
    yield
    crypto.configure()


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    d = tmp_path_factory.mktemp("enc_src")
    return _setup(d, seed=76, households=60)[1]


def read_shared(path, limit=8 * 1024 * 1024):
    """Read a file DuckDB is still using, without blocking its deletion (Windows refuses to delete a file another process holds open)."""
    if os.name != "nt":
        with open(path, "rb") as f:
            return f.read(limit)
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    h = k.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)          # read access; others may read, write and DELETE; open existing
    if h is None or h == wintypes.HANDLE(-1).value:
        raise OSError("cannot open")
    try:
        buf, n = ctypes.create_string_buffer(limit), wintypes.DWORD(0)
        k.ReadFile(h, buf, limit, ctypes.byref(n), None)
        return buf.raw[:n.value]
    finally:
        k.CloseHandle(h)


def parquet_files(work):
    return [f for f in work.rglob("*.parquet")]


def test_a_run_with_a_key_writes_only_encrypted_files_and_no_readable_personal_data(sources, tmp_path):
    key = new_key("prod-2026")
    crypto.configure(key)
    work = tmp_path / "work"
    summary = run_pipeline(PipelineConfig(sources, work))
    files = parquet_files(work)
    assert len(files) >= 10 and all(crypto.is_encrypted_file(f) for f in files), [f for f in files if not crypto.is_encrypted_file(f)]
    assert summary["encryption_at_rest"] == {"enabled": True, "key_id": "prod-2026"}
    # take real values out of the (decrypted) data, then look for them in every byte under the work folder and the model folder
    con = crypto.connect()
    rows = con.execute(f"SELECT full_name, pan, mobile, email FROM {crypto.rp(work / 'canonical.parquet')} WHERE pan IS NOT NULL AND mobile IS NOT NULL LIMIT 25").fetchall()
    con.close()
    needles = {v.encode() for r in rows for v in r if v and len(v) >= 6}
    assert len(needles) > 20
    everything = [f for f in list(work.rglob("*")) + list((work.parent / "models").rglob("*")) if f.is_file()]
    for f in everything:
        blob = f.read_bytes()
        assert not any(n in blob for n in needles), f"readable personal data in {f}"
        assert key.key.encode() not in blob, f"the key itself was written to {f}"
    assert not (work / ".duckdb_tmp").exists() or not any((work / ".duckdb_tmp").iterdir())
    side = (work / crypto.SIDECAR).read_text()
    assert key.check in side and key.key not in side and "prod-2026" in side                       # the folder knows WHICH key, never the key


def test_results_are_identical_with_and_without_encryption(sources, tmp_path):
    plain = run_pipeline(PipelineConfig(sources, tmp_path / "plain"))
    crypto.configure(new_key())
    enc = run_pipeline(PipelineConfig(sources, tmp_path / "enc"))
    def same(x):
        return {k: v for k, v in x.items() if k != "output"} if isinstance(x, dict) else x          # output paths differ by design
    for part in ("ingest", "standardise", "decisions", "clustering", "review_queue", "households"):
        assert same(plain[part]) == same(enc[part]), part
    a = duckdb.sql(f"SELECT * FROM '{tmp_path / 'plain' / 'golden' / 'golden_records.parquet'}' ORDER BY cluster_id").fetchall()
    con = crypto.connect()
    b = con.execute(f"SELECT * FROM {crypto.rp(tmp_path / 'enc' / 'golden' / 'golden_records.parquet')} ORDER BY cluster_id").fetchall()
    assert a == b and len(a) > 50


def test_missing_wrong_or_mixed_keys_are_refused_with_a_clear_reason(sources, tmp_path):
    k1 = new_key("a")
    crypto.configure(k1)
    work = tmp_path / "work"
    run_pipeline(PipelineConfig(sources, work))
    crypto.configure(None)
    with pytest.raises(crypto.CryptoError, match="DEDUPE_DATA_KEY is not set"):
        run_pipeline(PipelineConfig(sources, work))
    crypto.configure(new_key("b"))
    with pytest.raises(crypto.CryptoError, match="different key"):
        run_pipeline(PipelineConfig(sources, work))
    plain = tmp_path / "plain"
    crypto.configure(None)
    run_pipeline(PipelineConfig(sources, plain))
    crypto.configure(k1)
    with pytest.raises(crypto.CryptoError, match="unencrypted Parquet"):
        run_pipeline(PipelineConfig(sources, plain))
    # without the key, DuckDB itself cannot read an encrypted file either
    with pytest.raises(duckdb.Error):
        duckdb.sql(f"SELECT * FROM '{work / 'standardised.parquet'}'").fetchall()


def test_plain_data_can_be_converted_and_moved_to_a_new_key(sources, tmp_path):
    work = tmp_path / "work"
    run_pipeline(PipelineConfig(sources, work))                                                 # a plain run, as in development
    before = duckdb.sql(f"SELECT count(*), count(DISTINCT cluster_id) FROM '{work / 'golden' / 'golden_records.parquet'}'").fetchone()
    k1, k2 = new_key("old"), new_key("new")
    crypto.configure(k1)
    assert crypto.status(work)["encrypted"] == 0
    r = crypto.rekey(work, k1, None)
    assert r["rewritten"] >= 10 and crypto.status(work)["plain"] == [] and crypto.status(work)["key_matches_recorded"] is True
    assert crypto.rekey(work, k1, k1)["rewritten"] == 0                                         # repeat: nothing to do
    con = crypto.connect()
    assert con.execute(f"SELECT count(*), count(DISTINCT cluster_id) FROM {crypto.rp(work / 'golden' / 'golden_records.parquet')}").fetchone() == before
    crypto.configure(k2)
    moved = crypto.rekey(work, k2, k1)
    assert moved["rewritten"] >= 10 and moved["key_id"] == "new"
    crypto.bind_work(work, write=False)                                                         # accepted with the new key
    con = crypto.connect()
    assert con.execute(f"SELECT count(*) FROM {crypto.rp(work / 'golden' / 'golden_records.parquet')}").fetchone()[0] == before[0]
    crypto.configure(k1)
    with pytest.raises(crypto.CryptoError, match="different key"):
        crypto.bind_work(work, write=False)                                                     # the old key no longer opens it


def test_the_api_reads_encrypted_outputs_and_refuses_to_start_without_the_key(sources, tmp_path):
    key = new_key()
    crypto.configure(key)
    work = tmp_path / "work"
    run_pipeline(PipelineConfig(sources, work))
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="e" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'e.db'}")
    app = create_app(s)
    sv = app.state.services
    sv.workspace.create("demo", "Demo", "", "setup")
    run = f"/projects/demo/runs/{sv.jobs.import_results('demo', work, 'setup').id}"
    c = TestClient(app)
    h = {"Authorization": "Bearer " + issue(s, "u-v", ["viewer"])}
    assert c.get(run + "/queue?limit=3", headers=h).status_code == 200 and c.get(run, headers=h).json()["summary"]["records"] > 100
    p = c.get(run + "/queue?limit=1", headers=h).json()[0]["person_l"]
    assert c.get(f"{run}/people/{p}", headers=h).json()["records"]
    assert all(crypto.is_encrypted_file(f) for f in (tmp_path / "ws").rglob("*.parquet"))
    crypto.configure(None)
    with pytest.raises(crypto.CryptoError):
        create_app(s)


def test_production_refuses_to_run_without_a_key(tmp_path):
    with pytest.raises(crypto.CryptoError, match="production needs encryption"):
        crypto.require_in_production("production")
    crypto.require_in_production("dev")
    from dedupe.api import SettingsError
    prod = dict(env="production", auth_mode="oidc", oidc_issuer="https://i", oidc_audience="a", oidc_jwks_url="https://i/jwks", allowed_hosts=("h.example",),
                require_https=True, database_url="postgresql://u:p@db/x", project_database_url="postgresql://u:p@db/x_{project}", work=tmp_path)
    with pytest.raises(SettingsError, match="encryption at rest"):
        ApiSettings(**prod).validate()
    crypto.configure(new_key())
    ApiSettings(**prod).validate()


def test_keys_are_validated_and_never_shown():
    for bad in ("", "not base64!!", base64.b64encode(b"short").decode(), base64.b64encode(os.urandom(64)).decode()):
        with pytest.raises(crypto.CryptoError):
            crypto.parse_key(bad)
    with pytest.raises(crypto.CryptoError):
        crypto.parse_key(base64.b64encode(os.urandom(32)).decode(), "bad id!")
    k = new_key()
    assert k.key not in repr(k) and k.key not in str(k)
    assert len(k.check) == 16 and k.check != new_key().check


def test_duckdb_spill_files_are_encrypted_when_a_key_is_set(tmp_path):
    """A query bigger than the memory limit writes temporary files; with a key none of them may contain readable data."""
    src = tmp_path / "big.parquet"
    duckdb.sql(f"COPY (SELECT i, 'SPILLMARKER-' || (i * 7919 % 6000000)::VARCHAR || repeat('x', 40) AS s FROM range(4000000) t(i)) TO '{src}' (FORMAT PARQUET)")
    results = {}
    for label, key in (("plain", None), ("encrypted", new_key())):
        crypto.configure(key)
        spill = tmp_path / f"spill_{label}"
        spill.mkdir()
        seen = {"files": 0, "readable": False}
        stop = threading.Event()

        def watch(spill=spill, seen=seen, stop=stop):
            while not stop.is_set():
                for f in spill.rglob("*"):
                    try:
                        if f.is_file():
                            data = read_shared(f)
                            seen["files"] += 1
                            seen["readable"] = seen["readable"] or b"SPILLMARKER" in data
                    except OSError:
                        pass
                time.sleep(0.05)
        t = threading.Thread(target=watch, daemon=True)
        t.start()
        con = crypto.connect({"memory_limit": "150MB", "temp_directory": str(spill), "threads": 2})
        try:
            con.execute(f"SELECT s, count(*) FROM read_parquet('{src}') GROUP BY s ORDER BY s DESC LIMIT 5").fetchall()      # a grouping bigger than memory spills
        finally:
            stop.set()
            t.join()
            con.close()
        results[label] = seen
    assert results["plain"]["readable"] is True, "the control run must spill readable data, otherwise this test proves nothing"
    assert results["encrypted"]["files"] > 0 and results["encrypted"]["readable"] is False
