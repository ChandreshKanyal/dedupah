import csv
import json
import os
from pathlib import Path

import duckdb
import pytest

import dedupe.standardise.identifiers as ids
from dedupe.pipeline import PipelineConfig, redact_aadhaar, run_pipeline
from dedupe.synthetic import generate as gen_data


def _setup(tmp_path, seed=71, households=80):
    syn = tmp_path / "syn"
    gen_data(households, seed=seed, out=syn)
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(syn / os.path.basename(s["connector"]["path"]))
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return syn, path


def _raw_aadhaar(syn: Path) -> set:
    out = set()
    for r in csv.DictReader(open(syn / "loans.csv", encoding="utf-8")):
        if r["aadhaar_no"]:
            out.add(r["aadhaar_no"])
    for r in csv.DictReader(open(syn / "kyc_documents.csv", encoding="utf-8")):
        if r["doc_type"] == "AADHAAR":
            out.add(r["doc_number"].replace(" ", ""))
    return {a for a in out if a.isdigit() and len(a) == 12}


@pytest.fixture(scope="module")
def run_a(tmp_path_factory):
    d = tmp_path_factory.mktemp("pa")
    syn, sources = _setup(d)
    summary = run_pipeline(PipelineConfig(sources, d / "work", truth=syn))
    return d, syn, summary


def test_one_command_produces_every_artifact_and_a_sane_summary(run_a):
    d, _, s = run_a
    for f in ["canonical.parquet", "standardised.parquet", "candidates.parquet", "scored_pairs.parquet", "decisions.parquet",
              "clusters/clusters.parquet", "clusters/possible_matches.parquet", "review_queue.parquet", "households/household_members.parquet",
              "households/relationships.parquet", "pipeline_summary.json"]:
        assert (d / "work" / f).exists(), f
    assert (d / "models" / "fs_model.json").exists()
    assert [r["stage"] for r in s["stages"]][:3] == ["ingest", "standardise", "redact_aadhaar"]
    assert s["eval_clusters"]["f1"] > 0.7 and s["eval_decisions"]["auto_merge_quality"]["precision"] > 0.97
    assert s["peak_memory_mb"] > 0 and s["total_seconds"] > 0
    n = s["ingest"]["total"]
    assert duckdb.sql(f"select count(*) from '{d / 'work' / 'clusters' / 'clusters.parquet'}'").fetchone()[0] == n


def test_raw_aadhaar_never_survives_in_any_output(run_a):
    d, syn, _ = run_a
    raw = _raw_aadhaar(syn)
    assert raw  # the test is meaningful: the sources do contain Aadhaar numbers
    assert duckdb.sql(f"select count(aadhaar) from '{d / 'work' / 'canonical.parquet'}'").fetchone()[0] == 0
    scanned = 0
    for f in list((d / "work").rglob("*.parquet")):
        blob = str(duckdb.sql(f"select * from read_parquet('{f}')").fetchall())
        assert not any(a in blob for a in raw), f
        scanned += 1
    for f in list((d / "work").rglob("*.json")) + [d / "models" / "fs_model.json"]:
        text = f.read_text(encoding="utf-8")
        assert not any(a in text for a in raw), f
    assert scanned >= 8
    tokens = duckdb.sql(f"select count(aadhaar_token) from '{d / 'work' / 'standardised.parquet'}'").fetchone()[0]
    assert tokens > 0  # tokens (not numbers) are still available for matching


def test_pipeline_is_reproducible(run_a, tmp_path):
    d, syn, s1 = run_a
    _, sources = _setup(tmp_path)  # same seed -> same data in a fresh folder
    s2 = run_pipeline(PipelineConfig(sources, tmp_path / "work", truth=tmp_path / "syn"))
    q = "select source_id, source_record_id, cluster_id from '{}' order by 1, 2"
    a = duckdb.sql(q.format(d / "work" / "clusters" / "clusters.parquet")).fetchall()
    b = duckdb.sql(q.format(tmp_path / "work" / "clusters" / "clusters.parquet")).fetchall()
    assert a == b
    assert s1["decisions"] == s2["decisions"] and s1["clustering"] == s2["clustering"]
    m1 = json.loads((d / "models" / "fs_model.json").read_text(encoding="utf-8"))["weights"]
    m2 = json.loads((tmp_path / "models" / "fs_model.json").read_text(encoding="utf-8"))["weights"]
    assert m1.keys() == m2.keys()
    for k in m1:
        assert m1[k] == pytest.approx(m2[k], abs=1e-9)  # float summation order may differ in the last digits


def test_redact_is_idempotent_and_keeps_everything_else(tmp_path):
    f = tmp_path / "c.parquet"
    duckdb.sql(f"COPY (SELECT 'a' AS source_id, '1' AS source_record_id, '234567890123' AS aadhaar, '0123' AS aadhaar_last4) TO '{f}' (FORMAT PARQUET)")
    redact_aadhaar(f)
    redact_aadhaar(f)
    assert duckdb.sql(f"select aadhaar, aadhaar_last4 from '{f}'").fetchall() == [(None, "0123")]


def test_production_mode_refuses_the_development_token_key(monkeypatch):
    import random

    from dedupe.synthetic.identifiers import aadhaar
    valid = aadhaar(random.Random(9))
    monkeypatch.delenv("DEDUPE_TOKEN_KEY", raising=False)
    monkeypatch.setenv("DEDUPE_ENV", "production")
    with pytest.raises(RuntimeError, match="DEDUPE_TOKEN_KEY"):
        ids.aadhaar(valid)
    monkeypatch.setenv("DEDUPE_TOKEN_KEY", "a-real-secret")
    assert ids.aadhaar(valid)[0] is not None  # with a key it works


def test_token_key_changes_tokens_so_data_cannot_be_linked_across_keys(monkeypatch):
    import random

    from dedupe.synthetic.identifiers import aadhaar
    a = aadhaar(random.Random(5))
    monkeypatch.setenv("DEDUPE_TOKEN_KEY", "key-one")
    t1 = ids.aadhaar(a)[0]
    monkeypatch.setenv("DEDUPE_TOKEN_KEY", "key-two")
    assert t1 and ids.aadhaar(a)[0] != t1


def test_review_queue_is_grouped_ranked_and_excludes_resolved_pairs(run_a):
    d, _, s = run_a
    w = d / "work"
    f = w / "review_queue.parquet"
    n = duckdb.sql(f"select count(*) from '{f}'").fetchone()[0]
    assert n == s["review_queue"]["queue_items"] > 0
    assert n <= s["decisions"]["review"]                              # grouped: never more items than record pairs
    assert duckdb.sql(f"select count(*) from (select distinct cluster_l, cluster_r from '{f}')").fetchone()[0] == n  # one item per pair of people
    assert duckdb.sql(f"select count(*) from '{f}' where cluster_l >= cluster_r").fetchone()[0] == 0
    pr = [r[0] for r in duckdb.sql(f"select priority from '{f}'").fetchall()]   # file order is rank order
    assert pr == sorted(pr, reverse=True)                              # best cases first


def test_review_queue_workload_controls(run_a, tmp_path):
    d, _, _ = run_a
    from dedupe.review import build_queue
    con = duckdb.connect()
    w = d / "work"
    con.execute(f"CREATE TABLE decisions AS SELECT * FROM read_parquet('{w / 'decisions.parquet'}')")
    con.execute(f"""CREATE TABLE cluster_map AS SELECT i.rid, c.cluster_id FROM read_parquet('{w / 'clusters' / 'clusters.parquet'}') c
                    JOIN read_parquet('{w / 'candidates.index.parquet'}') i USING (source_id, source_record_id)""")
    full = build_queue(con, tmp_path / "a.parquet")["queue_items"]
    high = build_queue(con, tmp_path / "b.parquet", min_probability=0.5)["queue_items"]
    top = build_queue(con, tmp_path / "c.parquet", cap=3)["queue_items"]
    assert full > high > 0 and top == 3
    assert duckdb.sql(f"select min(top_probability) from '{tmp_path / 'b.parquet'}'").fetchone()[0] >= 0.5
