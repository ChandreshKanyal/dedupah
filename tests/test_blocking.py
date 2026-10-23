import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from dedupe.blocking import DEFAULT_RULES, Rule, generate
from dedupe.blocking.report import blocking_report
from dedupe.ingest import ingest
from dedupe.standardise.run import SCHEMA
from dedupe.standardise.run import run as standardise_run
from dedupe.synthetic import generate as gen_data


def _std(tmp_path, rows):
    base = {"source_id": "s"}
    recs = [{**base, "source_record_id": f"{i:04d}", **r} for i, r in enumerate(rows)]
    path = tmp_path / "std.parquet"
    pq.write_table(pa.Table.from_pylist(recs, schema=SCHEMA), path)
    return path


def _pairs(out):
    return {(r[0], r[1]): r[2] for r in duckdb.sql(f"select l_rid, r_rid, rules from '{out}'").fetchall()}


def test_pairs_are_unique_ordered_and_union_rules(tmp_path):
    std = _std(tmp_path, [
        {"mobile": "9000000001", "pan": "ABCPD1234E"},   # rid 0
        {"mobile": "9000000001", "pan": "ABCPD1234E"},   # rid 1: shares phone AND pan with 0
        {"mobile": "9000000002"},                         # rid 2: shares nothing
        {"mobile_alt": "9000000001"},                     # rid 3: alt phone matches phone of 0 and 1
    ])
    out = tmp_path / "c.parquet"
    stats = generate(std, out, rules=[r for r in DEFAULT_RULES if r.name in ("pan", "phone")])
    p = _pairs(out)
    assert set(p) == {(0, 1), (0, 3), (1, 3)}
    assert p[(0, 1)] == ["pan", "phone"] and p[(0, 3)] == ["phone"]
    assert all(l < r for l, r in p) and stats["candidate_pairs"] == 3

def test_one_person_with_many_records_stays_connected_beyond_the_cap_but_a_generic_phone_does_not(tmp_path):
    """12 records with one PAN (a customer's loans and policies) and 12 sharing one phone (an agent), with a cap of 5: the PAN block is linked with
    a star and a chain, the phone block is skipped as generic."""
    std = _std(tmp_path, [{"pan": "ABCPD1234E"} for _ in range(12)] + [{"mobile": "9000000009"} for _ in range(12)])
    out = tmp_path / "c.parquet"
    stats = generate(std, out, rules=[r for r in DEFAULT_RULES if r.name in ("pan", "phone")], cap=5)
    p = _pairs(out)
    assert all(rules == ["pan"] for rules in p.values()) and all(r < 12 for _, r in p)           # nothing from the phone block, no pairs across the groups
    assert len(p) < 66 and len(p) == 11 + 10                                                     # a star (11) and a chain (10 more), not all 66 pairs
    seen = {0}
    for _ in range(12):
        seen |= {x for pair in p for x in pair if pair[0] in seen or pair[1] in seen}
    assert seen == set(range(12))                                                                # all twelve are one connected group
    assert stats["rules"]["phone"]["capped_blocks"] == 1



def test_frequency_cap_skips_generic_keys(tmp_path):
    rows = [{"mobile": "9111111111"} for _ in range(60)] + [{"mobile": "9222222222"}, {"mobile": "9222222222"}]
    std = _std(tmp_path, rows)
    out = tmp_path / "c.parquet"
    stats = generate(std, out, rules=[r for r in DEFAULT_RULES if r.name == "phone"], cap=50)
    assert set(_pairs(out)) == {(60, 61)}  # the 60-record "agent phone" block is ignored
    assert stats["rules"]["phone"]["capped_blocks"] == 1 and stats["rules"]["phone"]["records_in_capped_blocks"] == 60
    generate(std, out, rules=[r for r in DEFAULT_RULES if r.name == "phone"], cap=100)
    assert len(_pairs(out)) == 60 * 59 // 2 + 1  # raising the cap lets it through


def test_null_keys_never_pair_and_custom_rules_work(tmp_path):
    std = _std(tmp_path, [{"name_last_phon": "srm"}, {"name_last_phon": "srm"}, {}, {}])  # no dob -> surname_dob can't fire
    out = tmp_path / "c.parquet"
    generate(std, out, rules=[r for r in DEFAULT_RULES if r.name == "surname_dob"])
    assert _pairs(out) == {}
    custom = Rule("surname_only", "SELECT rid, name_last_phon AS key FROM records WHERE name_last_phon IS NOT NULL")
    generate(std, out, rules=[custom])
    assert set(_pairs(out)) == {(0, 1)}


def test_blocking_on_realistic_data_reduces_pairs_and_finds_most_duplicates(tmp_path):
    gen_data(120, seed=21, out=tmp_path / "syn")
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(tmp_path / "syn" / os.path.basename(s["connector"]["path"]))
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    ingest(tmp_path / "cfg.json", tmp_path / "canon.parquet")
    standardise_run(tmp_path / "canon.parquet", tmp_path / "std.parquet")
    con = duckdb.connect()
    gen = generate(tmp_path / "std.parquet", tmp_path / "cand.parquet", con=con)
    rep = blocking_report(con, tmp_path / "syn", gen)
    assert rep["reduction_ratio"] > 0.98
    assert rep["transitive_completeness"] > 0.85
    assert rep["pairs_completeness"] <= rep["transitive_completeness"] <= 1
    idx = duckdb.sql(f"select count(*) from '{tmp_path / 'cand.index.parquet'}'").fetchone()[0]
    assert idx == gen["records"]
