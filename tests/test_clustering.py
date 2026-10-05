import json
import os
from pathlib import Path

import duckdb
import pytest

from dedupe.blocking import generate as block
from dedupe.clustering import ClusterConfig, cluster
from dedupe.clustering.run import load_inputs, write_outputs
from dedupe.decision import DecisionConfig, decide
from dedupe.ingest import ingest
from dedupe.scoring.__main__ import load_tables
from dedupe.scoring import score, train
from dedupe.standardise.run import run as standardise_run
from dedupe.synthetic import generate as gen_data

PAN = lambda v: {"pan": v}


def groups(res):
    out = {}
    for rid, lab in res.labels.items():
        out.setdefault(lab, set()).add(rid)
    return sorted(map(sorted, out.values()))


def test_plain_chain_becomes_one_cluster_and_singletons_stay():
    res = cluster([1, 2, 3, 4, 9], [(1, 2, .99), (2, 3, .95), (3, 4, .93)])
    assert groups(res) == [[1, 2, 3, 4], [9]]
    assert res.stats["contradictory_components"] == 0 and res.stats["clusters"] == 2


def test_weak_bridge_between_two_different_pans_is_cut():
    ids = {1: PAN("A"), 2: PAN("A"), 4: PAN("B"), 5: PAN("B")}
    res = cluster([1, 2, 4, 5], [(1, 2, .99), (4, 5, .99), (2, 4, .91)], ids)
    assert groups(res) == [[1, 2], [4, 5]]
    assert res.removed_edges == [(2, 4, .91, "different_pan")] and res.stats["edges_removed"] == 1
    assert any("split" in f for fl in res.flags.values() for f in fl)
    assert groups(cluster([1, 2, 4, 5], [(1, 2, .99), (4, 5, .99), (2, 4, .91)], ids, guard=False)) == [[1, 2, 4, 5]]


def test_cannot_link_pair_splits_the_chain_at_its_weakest_link():
    res = cluster([1, 2, 3], [(1, 2, .95), (2, 3, .92)], cannot_links=[(1, 3)])
    assert groups(res) == [[1, 2], [3]] and res.removed_edges[0][3] == "vetoed_pair"


def test_redundant_paths_are_all_cut_until_the_conflict_is_separated():
    ids = {1: {"aadhaar": "x"}, 3: {"aadhaar": "y"}}
    res = cluster([1, 2, 3], [(1, 2, .99), (2, 3, .95), (1, 3, .93)], ids)
    assert groups(res) == [[1, 2], [3]] and res.stats["edges_removed"] == 2  # 2-3 first, then the direct 1-3


def test_consistent_ids_are_not_split_and_missing_ids_are_ignored():
    ids = {1: PAN("A"), 2: PAN("A"), 3: {}}  # 3 has no ID: no contradiction
    res = cluster([1, 2, 3], [(1, 2, .99), (2, 3, .9)], ids)
    assert groups(res) == [[1, 2, 3]] and res.stats["contradictory_components"] == 0


def test_unresolvable_group_is_released_not_looped_forever():
    ids = {i: PAN(str(i)) for i in range(1, 6)}  # five different PANs chained together
    edges = [(i, i + 1, .9 + i / 100) for i in range(1, 5)]
    res = cluster(range(1, 6), edges, ids, cfg=ClusterConfig(max_removals=1))
    assert res.stats["unresolved_components"] >= 1
    assert any("released_unresolved" in f for fl in res.flags.values() for f in fl)
    assert len(res.labels) == 5
    full = cluster(range(1, 6), edges, ids)
    assert groups(full) == [[i] for i in range(1, 6)] and full.stats["unresolved_components"] == 0


def test_oversize_cluster_is_flagged_not_split():
    nodes = list(range(20))
    res = cluster(nodes, [(i, i + 1, .95) for i in range(19)], cfg=ClusterConfig(max_cluster_size=15))
    assert res.stats["largest_cluster"] == 20 and res.stats["oversize_clusters"] == 1


def test_deterministic_regardless_of_input_order():
    ids = {1: PAN("A"), 3: PAN("B")}
    edges = [(1, 2, .9), (2, 3, .9)]  # tie on probability
    a = cluster([1, 2, 3], edges, ids)
    b = cluster([3, 2, 1], list(reversed(edges)), ids)
    assert groups(a) == groups(b) and a.removed_edges == b.removed_edges


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    d = tmp_path_factory.mktemp("cl")
    gen_data(150, seed=44, out=d / "syn")
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(d / "syn" / os.path.basename(s["connector"]["path"]))
    (d / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    ingest(d / "cfg.json", d / "canon.parquet")
    standardise_run(d / "canon.parquet", d / "std.parquet")
    block(d / "std.parquet", d / "cand.parquet")
    con = duckdb.connect()
    load_tables(con, d / "std.parquet", d / "cand.parquet")
    score(con, train(con), d / "scored.parquet")
    dcfg = DecisionConfig.load("configs/decision_default.json")
    decide(con, d / "scored.parquet", dcfg, d / "dec.parquet")
    return d, dcfg


def test_end_to_end_outputs_and_quality(pipeline):
    from dedupe.evaluation.loaders import load_truth, record_key
    from dedupe.evaluation.metrics import evaluate
    d, dcfg = pipeline
    con = duckdb.connect()
    nodes, edges, cannot, ids = load_inputs(con, d / "std.parquet", d / "cand.parquet", d / "dec.parquet", dcfg)
    res = cluster(nodes, edges, ids, cannot)
    write_outputs(con, res, d / "out")
    n = len(nodes)
    cl = duckdb.sql(f"select count(*), count(distinct rid), count(distinct cluster_id) from '{d / 'out' / 'clusters.parquet'}'").fetchone()
    assert cl[0] == n == cl[1] and cl[2] == res.stats["clusters"]  # every record in exactly one cluster
    assert (d / "out" / "possible_matches.parquet").exists() and (d / "out" / "cluster_stats.parquet").exists()
    keys = {rid: record_key(s, i) for rid, s, i in con.execute("SELECT rid, source_id, source_record_id FROM records").fetchall()}
    truth, hh, traps = load_truth(d / "syn")
    rep = evaluate({keys[r]: f"c{lab}" for r, lab in res.labels.items()}, truth, hh, traps)
    assert rep["precision"] > 0.97 and rep["f1"] > 0.7
    pm = duckdb.sql(f"select count(*) from '{d / 'out' / 'possible_matches.parquet'}' where cluster_l = cluster_r").fetchone()[0]
    assert pm == 0  # possible matches always join two different clusters
