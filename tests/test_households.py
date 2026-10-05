import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dedupe.blocking import generate as block
from dedupe.clustering import cluster
from dedupe.clustering.run import load_inputs, write_outputs
from dedupe.decision import DecisionConfig, decide
from dedupe.households import evidence as ev
from dedupe.households import group_households
from dedupe.ingest import ingest
from dedupe.scoring import score, train
from dedupe.scoring.__main__ import load_tables
from dedupe.standardise.run import SCHEMA, run as standardise_run, standardise_row
from dedupe.synthetic import generate as gen_data

RAW = ["full_name", "father_name", "dob", "gender", "mobile", "mobile_alt", "email", "address", "address_alt", "city",
       "state", "pincode", "pan", "aadhaar", "aadhaar_last4", "passport_no", "dl_no", "voter_id", "related_name",
       "party_role", "relation_to_proposer"]


def world(tmp_path, recs):
    """recs: list of (cluster, raw_dict, policy_no|None). Builds std/clusters/canonical parquet and runs evidence.build."""
    std, cl, can = [], [], []
    for i, (cluster_id, raw, policy) in enumerate(recs):
        base = {k: None for k in RAW}
        base.update(raw, source_id="s", source_record_id=f"{i:04d}")
        std.append(standardise_row(base))
        cl.append({"source_id": "s", "source_record_id": f"{i:04d}", "cluster_id": cluster_id})
        can.append({"source_id": "s", "source_record_id": f"{i:04d}", "attributes": json.dumps({"policy_no": policy}) if policy else None})
    pq.write_table(pa.Table.from_pylist(std, schema=SCHEMA), tmp_path / "std.parquet")
    pq.write_table(pa.Table.from_pylist(cl), tmp_path / "cl.parquet")
    pq.write_table(pa.Table.from_pylist(can, schema=pa.schema([("source_id", pa.string()), ("source_record_id", pa.string()), ("attributes", pa.string())])), tmp_path / "can.parquet")
    con = duckdb.connect()
    ev.build(con, str(tmp_path / "std.parquet"), str(tmp_path / "cl.parquet"), str(tmp_path / "can.parquet"))
    return {(a, b, e): w for a, b, e, w in con.execute("SELECT ca, cb, ev, w FROM evidence").fetchall()}


def test_shared_phone_counts_once_and_generic_numbers_are_ignored(tmp_path):
    recs = [("A", {"mobile": "9812345670", "full_name": "Asha Rao"}, None), ("A", {"mobile": "9812345670"}, None),
            ("B", {"mobile": "9812345670", "full_name": "Ravi Rao"}, None)]
    recs += [(f"G{i}", {"mobile": "9000011111"}, None) for i in range(9)]  # an agent number on 9 people
    e = world(tmp_path, recs)
    assert e[("A", "B", "shared_phone")] == ev.WEIGHTS["shared_phone"] and e[("A", "B", "same_surname")] == 1.0
    assert not any(k[2] == "shared_phone" and k[0].startswith("G") for k in e)


def test_home_neighbours_and_policy_roles(tmp_path):
    home = "Flat 12, Green Valley Society, Station Road, Pune 411001"
    recs = [("A", {"address": home}, None), ("B", {"address": home}, None),                       # same home
            ("C", {"address": "Flat 40, Green Valley Society, Station Road, Pune 411001"}, None),  # neighbour
            ("P", {"party_role": "Proposer"}, "POL1"), ("I", {"party_role": "Insured"}, "POL1"),
            ("N", {"party_role": "Nominee"}, "POL1")]
    e = world(tmp_path, recs)
    assert e[("A", "B", "same_home")] == ev.WEIGHTS["same_home"]
    assert ("A", "C", "same_home") not in e and e[("A", "C", "same_area_text")] == ev.WEIGHTS["same_area_text"]
    assert e[("I", "P", "policy_member")] == 5.0 and e[("N", "P", "policy_nominee")] == 3.0 and e[("I", "N", "policy_nominee")] == 3.0


def test_named_relative_needs_matching_place_and_is_diluted_by_namesakes(tmp_path):
    recs = [("A", {"full_name": "Raj Shah", "related_name": "Anita Shah", "city": "Pune"}, None),
            ("B", {"full_name": "Anita Shah", "city": "Pune"}, None),
            ("C", {"full_name": "Meera Iyer", "related_name": "Kavitha Iyer", "city": "Chennai"}, None),
            ("D", {"full_name": "Kavitha Iyer", "city": "Hyderabad"}, None)]                    # different city: no link
    e = world(tmp_path, recs)
    assert e[("A", "B", "named_relative")] == 3.0
    assert not any(k[2] == "named_relative" and "C" in k[:2] for k in e)
    recs2 = recs[:2] + [("B2", {"full_name": "Anita Shah", "city": "Pune"}, None)]               # a namesake: weight is shared
    e2 = world(tmp_path, recs2)
    assert e2[("A", "B", "named_relative")] == 1.5 and e2[("A", "B2", "named_relative")] == 1.5


def test_father_named_needs_age_gap_and_male(tmp_path):
    kid = {"full_name": "Aarav Shah", "father_name": "Raj Shah", "dob": "01/05/2005"}
    ok = {"full_name": "Raj Shah", "dob": "01/05/1975", "gender": "M"}
    too_young = {"full_name": "Raj Shah", "dob": "01/05/1999", "gender": "M"}
    female = {"full_name": "Raj Shah", "dob": "01/05/1975", "gender": "F"}
    assert world(tmp_path, [("K", kid, None), ("F", ok, None)])[("F", "K", "father_named")] == 2.0
    assert not any(k[2] == "father_named" for k in world(tmp_path, [("K", kid, None), ("F", too_young, None)]))
    assert not any(k[2] == "father_named" for k in world(tmp_path, [("K", kid, None), ("F", female, None)]))
    unknown = {"full_name": "Raj Shah"}  # no birth year: allowed but at half weight
    assert world(tmp_path, [("K", kid, None), ("F", unknown, None)])[("F", "K", "father_named")] == 1.0


def test_surname_alone_creates_no_link(tmp_path):
    assert world(tmp_path, [("A", {"full_name": "Raj Shah"}, None), ("B", {"full_name": "Anita Shah"}, None)]) == {}


def test_group_threshold_and_size_guard():
    edges = [("a", "b", 6.0), ("b", "c", 4.5), ("c", "d", 2.0)]
    hh, info = group_households("abcde", edges, threshold=4.0)
    assert hh["a"] == hh["b"] == hh["c"] and hh["d"] != hh["a"] and hh["e"] == "e" and info["households"] == 3
    # chain of 6 with one weak bridge: guard re-splits at a stricter threshold
    chain = [(i, i + 1, 6.0) for i in range(5)]
    chain[2] = (2, 3, 4.2)
    hh, info = group_households(range(6), chain, threshold=4.0, max_size=3)
    assert len({hh[0], hh[1], hh[2]}) == 1 and len({hh[3], hh[4], hh[5]}) == 1 and hh[2] != hh[3]
    assert info["escalated_components"] == 1 and info["oversize_households"] == 0
    # a fully strong blob cannot be split: kept but flagged
    blob = [(i, j, 20.0) for i in range(5) for j in range(i + 1, 5)]
    hh, info = group_households(range(5), blob, threshold=4.0, max_size=3)
    assert info["oversize_households"] == 1 and len(set(hh.values())) == 1


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    d = tmp_path_factory.mktemp("hh")
    gen_data(150, seed=55, out=d / "syn")
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
    nodes, edges, cannot, ids = load_inputs(con, d / "std.parquet", d / "cand.parquet", d / "dec.parquet", dcfg)
    write_outputs(con, cluster(nodes, edges, ids, cannot), d / "clusters")
    return d


def test_end_to_end_households(pipeline):
    from dedupe.households.run import evaluate, truth_of_clusters
    d = pipeline
    con = duckdb.connect()
    ev.build(con, str(d / "std.parquet"), str(d / "clusters" / "clusters.parquet"), str(d / "canon.parquet"))
    scores = ev.pair_scores(con)
    people = [r[0] for r in con.execute("SELECT DISTINCT cluster_id FROM rc").fetchall()]
    hh, info = group_households(people, [(a, b, s) for a, b, s, _ in scores], 4.0, 10)
    assert set(hh) == set(people) and info["largest"] <= 10  # every person in exactly one household
    th, tp = truth_of_clusters(con, d / "syn")
    rep = evaluate(hh, th, [(a, b, s) for a, b, s, _ in scores], {(a, b): e for a, b, _, e in scores}, tp)
    assert rep["family_only"]["precision"] > 0.8 and rep["family_only"]["recall"] > 0.4
    assert rep["evidence_precision"]["shared_phone"]["precision"] > 0.9
