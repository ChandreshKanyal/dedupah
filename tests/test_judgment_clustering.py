import json
import os

import duckdb
import pyarrow as pa

from dedupe.clustering import cluster
from dedupe.clustering.judged import PART_GLUE_WEIGHT, STEWARD_WEIGHT, audit, constraints_from_store, drop_vetoes_overridden_by_stewards
from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.store import JudgmentStore
from dedupe.synthetic import generate as gen_data


def _groups(res):
    out = {}
    for n, lab in res.labels.items():
        out.setdefault(lab, set()).add(n)
    return sorted(map(sorted, out.values()))


# ---- clustering core ------------------------------------------------------------------------------------------------
def test_a_steward_link_joins_records_automation_left_apart():
    res = cluster(range(4), [(0, 1, 0.99), (2, 3, STEWARD_WEIGHT)])      # a pair that has no automatic edge at all
    assert _groups(res) == [[0, 1], [2, 3]]


def test_steward_different_person_splits_an_automatic_group_at_its_weakest_link():
    edges = [(0, 1, 0.99), (1, 2, 0.95), (2, 3, 0.60)]
    res = cluster(range(4), edges, cannot_links=[(1, 3)])               # steward: records 1 and 3 are different people
    assert _groups(res) == [[0, 1, 2], [3]]                                # the weakest link on the path is the one cut


def test_a_split_keeps_every_part_apart_even_with_strong_links_between_them():
    edges = [(0, 1, 0.99), (1, 2, 0.99), (2, 3, 0.99), (3, 4, 0.99)]
    glue = [(0, 1, PART_GLUE_WEIGHT), (3, 4, PART_GLUE_WEIGHT)]        # what constraints_from_store adds inside each part
    res = cluster(range(5), edges + glue, separations={0: [(7, 0)], 1: [(7, 0)], 3: [(7, 1)], 4: [(7, 1)]})   # part 0 = {0,1}, part 1 = {3,4}
    got = _groups(res)
    assert not any({0, 1} & set(g) and {3, 4} & set(g) for g in got)
    assert any({0, 1} <= set(g) for g in got) and any({3, 4} <= set(g) for g in got)   # each part stays whole, the cut falls between them


def test_steward_links_outweigh_automatic_ones_when_a_conflict_must_be_resolved():
    # automatic chain 0-1-2 plus a steward link 0-2 ... and a veto between 1 and 2: the automatic edge 1-2 goes, the steward link stays
    res = cluster(range(3), [(0, 1, 0.99), (1, 2, 0.99), (0, 2, STEWARD_WEIGHT)], cannot_links=[(1, 2)])
    assert _groups(res) == [[0, 2], [1]]                                    # the automatic edges were cut, the steward link survived


def test_contradicting_steward_decisions_the_newer_one_wins():
    # two steward links 0=1 (older) and 1=2 (newer) and a steward 'different' on 0,2: one link must give way
    old, newer = STEWARD_WEIGHT + 1e-9, STEWARD_WEIGHT + 2e-9
    res = cluster(range(3), [(0, 1, old), (1, 2, newer)], cannot_links=[(0, 2)])
    assert _groups(res) == [[0], [1, 2]]                                   # the OLDER link (0-1) was sacrificed


def test_identifier_conflict_blocks_a_steward_link_unless_confirmed():
    ids = {0: {"pan": "AAAAA1111A"}, 1: {"pan": "BBBBB2222B"}}
    res = cluster(range(2), [(0, 1, STEWARD_WEIGHT)], ids=ids)
    assert _groups(res) == [[0], [1]]                                       # refused
    res2 = cluster(range(2), [(0, 1, STEWARD_WEIGHT)], ids={0: {}, 1: {}})  # confirmed: the pipeline blanks both records' identifiers
    assert _groups(res2) == [[0, 1]]


def test_clustering_without_judgments_is_unchanged():
    edges = [(0, 1, 0.9), (2, 3, 0.8), (3, 4, 0.7)]
    assert _groups(cluster(range(6), edges)) == [[0, 1], [2, 3, 4], [5]]


# ---- store -> constraints -> audit ----------------------------------------------------------------------------------
def _records(con, keys):
    con.register("r", pa.table({"rid": list(range(len(keys))), "source_id": [k[0] for k in keys], "source_record_id": [k[1] for k in keys]}))
    con.execute("CREATE TABLE records AS SELECT * FROM r")


def test_constraints_from_the_store_and_the_audit(tmp_path):
    keys = [("s", f"r{i}") for i in range(6)]
    con = duckdb.connect()
    _records(con, keys)
    store = JudgmentStore.open(f"sqlite:///{tmp_path / 'j.db'}")
    store.same_person(keys[0], keys[1], "asha")
    store.different_person(keys[2], keys[3], "asha")
    store.unsure(keys[4], keys[5], "asha")
    store.split([[keys[0]], [keys[2]]], "asha", "two people")
    store.same_person(keys[0], ("gone", "x"), "asha")                        # a record that is no longer in the data
    store.override_field("email", "a@b.in", [keys[0]], "asha", "confirmed")   # irrelevant to clustering
    cons = constraints_from_store(con, store)
    assert [(a, b) for a, b, w in cons.must if w >= STEWARD_WEIGHT] == [(0, 1)] and cons.cannot == [(2, 3)] and cons.judged_pairs == {(0, 1), (2, 3), (4, 5)}
    assert cons.missing[0]["kind"] == "same_person" and cons.summary()["by_kind"] == {"same_person": 1, "different_person": 1, "split": 1}
    res = cluster(range(6), [(2, 3, 0.99)] + cons.must, cannot_links=cons.cannot, separations=cons.separations)
    rep = audit(cons, res.labels, {})
    assert rep["held"] == 3 and rep["not_held"] == 0
    bad = audit(cons, {i: 0 for i in range(6)}, {})                           # pretend everything ended up together
    assert {u["kind"] for u in bad["unsatisfied"]} == {"different_person", "split"}


def test_a_steward_same_person_overrides_a_rule_veto_on_the_same_pair(tmp_path):
    keys = [("s", "a"), ("s", "b"), ("s", "c")]
    con = duckdb.connect()
    _records(con, keys)
    store = JudgmentStore.open(f"sqlite:///{tmp_path / 'j.db'}")
    store.same_person(keys[0], keys[1], "asha", "confirmed by phone")
    cons = constraints_from_store(con, store)
    assert drop_vetoes_overridden_by_stewards([(0, 1), (1, 2)], cons) == [(1, 2)]


# ---- the whole pipeline ---------------------------------------------------------------------------------------------
def _setup(tmp_path, seed=81, households=60):
    syn = tmp_path / "syn"
    gen_data(households, seed=seed, out=syn)
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(syn / os.path.basename(s["connector"]["path"]))
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_pipeline_applies_steward_decisions_and_reverts_when_they_are_revoked(tmp_path):
    sources = _setup(tmp_path)
    work, url = tmp_path / "work", f"sqlite:///{tmp_path / 'j.db'}"
    store = JudgmentStore.open(url)
    run = lambda: run_pipeline(PipelineConfig(sources, work, judgments_url=url))   # noqa: E731
    clusters = lambda: {(r[0], r[1]): r[2] for r in duckdb.sql(  # noqa: E731
        f"select source_id, source_record_id, cluster_id from '{work / 'clusters' / 'clusters.parquet'}'").fetchall()}
    base_summary = run()
    base = clusters()
    assert base_summary["judgments"]["judgments_applied_to_run"] == 0
    people = {}
    for k, pid in base.items():
        people.setdefault(pid, []).append(k)
    # (1) steward joins two people the machine kept apart (any two singletons)
    a, b = [ks[0] for ks in people.values() if len(ks) == 1 and ks[0][0] == "leads"][:2]   # leads carry no PAN/Aadhaar, so no identifier conflict
    j_same = store.same_person(a, b, "asha", "verified with the customer")
    # (2) steward splits a multi-record person into two parts
    big = next(ks for ks in people.values() if len(ks) >= 3 and a not in ks and b not in ks)
    j_split = store.split([[big[0]], big[1:]], "asha", "two different people share these records")
    s1 = run()
    after = clusters()
    assert after[a] == after[b]                                           # joined
    assert after[big[0]] != after[big[1]] and len({after[k] for k in big[1:]}) == 1   # split as asked
    assert s1["judgments"]["held"] == 2 and s1["judgments"]["not_held"] == 0
    assert after[a] in {base[a], base[b]}                                 # the merged person keeps one of the existing IDs
    others = [k for k in base if k not in (a, b, *big)]
    assert sum(after[k] == base[k] for k in others) / len(others) > 0.99  # everyone else keeps their person ID
    # (3) undo both decisions: the clustering returns exactly to the baseline
    store.revoke(j_same, "asha", "mistake")
    store.revoke(j_split, "asha", "mistake")
    s2 = run()
    assert clusters() == base                                             # exactly the baseline again: same groups AND the same person IDs
    assert s2["identity"]["reactivated"] >= 1
    assert s2["judgments"]["judgments_applied_to_run"] == 0


def test_a_steward_decided_pair_leaves_the_review_queue(tmp_path):
    sources = _setup(tmp_path, seed=82)
    work, url = tmp_path / "work", f"sqlite:///{tmp_path / 'j.db'}"
    store = JudgmentStore.open(url)
    run_pipeline(PipelineConfig(sources, work, judgments_url=url))
    pm = duckdb.sql(f"""select s1.source_id, s1.source_record_id, s2.source_id, s2.source_record_id from '{work / 'clusters' / 'possible_matches.parquet'}' p
        join '{work / 'clusters' / 'clusters.parquet'}' s1 on s1.rid = p.l_rid join '{work / 'clusters' / 'clusters.parquet'}' s2 on s2.rid = p.r_rid
        order by p.probability desc limit 1""").fetchone()
    before = duckdb.sql(f"select count(*) from '{work / 'review_queue.parquet'}'").fetchone()[0]
    store.different_person((pm[0], pm[1]), (pm[2], pm[3]), "asha", "checked: different people")
    s = run_pipeline(PipelineConfig(sources, work, judgments_url=url))
    after = duckdb.sql(f"select count(*) from '{work / 'review_queue.parquet'}'").fetchone()[0]
    assert s["judgments"]["held"] == 1 and after <= before


def test_a_steward_join_across_different_pans_is_refused_until_explicitly_confirmed(tmp_path):
    sources = _setup(tmp_path, seed=83)
    work, url = tmp_path / "work", f"sqlite:///{tmp_path / 'j.db'}"
    store = JudgmentStore.open(url)
    run_pipeline(PipelineConfig(sources, work, judgments_url=url))
    pans = duckdb.sql(f"""select s.source_id, s.source_record_id, s.pan, c.cluster_id from '{work / 'standardised.parquet'}' s
        join '{work / 'clusters' / 'clusters.parquet'}' c using (source_id, source_record_id) where s.pan is not null""").fetchall()
    (s1, r1, *_), (s2, r2, *_) = pans[0], next(x for x in pans if x[2] != pans[0][2] and x[3] != pans[0][3])
    j = store.same_person((s1, r1), (s2, r2), "asha", "customer says it is the same person")
    summary = run_pipeline(PipelineConfig(sources, work, judgments_url=url))
    rep = json.loads((work / "clusters" / "judgment_report.json").read_text(encoding="utf-8"))
    assert summary["judgments"]["not_held"] == 1 and rep["unsatisfied"][0]["reason"] == "identifier_conflict_not_confirmed"
    store.revoke(j, "asha")
    store.same_person((s1, r1), (s2, r2), "asha", "KYC officer verified original documents", confirm_id_conflict=True)
    summary = run_pipeline(PipelineConfig(sources, work, judgments_url=url))
    got = dict(duckdb.sql(f"select source_id || '|' || source_record_id, cluster_id from '{work / 'clusters' / 'clusters.parquet'}'").fetchall())
    assert summary["judgments"]["held"] == 1 and got[f"{s1}|{r1}"] == got[f"{s2}|{r2}"]
