"""Properties that must hold for ANY steward decisions, checked with the simulated reviewer on a small synthetic run."""
import json
import os
import random

import duckdb
import pytest

from dedupe.clustering import ClusterConfig, cluster
from dedupe.clustering.judged import STEWARD_WEIGHT, constraints_from_store
from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.review.simulate import decisions, exact_person_share, hit_rate, load_run, order, partition, reviews_to_reach, score, simulate
from dedupe.store import JudgmentStore
from dedupe.synthetic import generate as gen_data


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    d = tmp_path_factory.mktemp("sim")
    syn = d / "syn"
    gen_data(150, seed=95, out=syn)
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(syn / os.path.basename(s["connector"]["path"]))
    (d / "sources.json").write_text(json.dumps(cfg), encoding="utf-8")
    run_pipeline(PipelineConfig(d / "sources.json", d / "work", truth=syn))
    run = load_run(d / "work", syn)
    assert len(run.items) >= 20
    return run, d


def _groups(labels: dict) -> set:
    out: dict = {}
    for rid, lab in labels.items():
        out.setdefault(lab, set()).add(rid)
    return {frozenset(g) for g in out.values()}


def _refines(fine: set, coarse: set) -> bool:
    """Every group of `fine` sits inside one group of `coarse`."""
    where = {r: i for i, g in enumerate(coarse) for r in g}
    return all(len({where[r] for r in g}) == 1 for g in fine)


def test_decisions_are_order_independent_and_deterministic(sim):
    run, _ = sim
    items = order(run, "priority")[:40]
    said = decisions(items, 0.0, 1)
    first = _groups(partition(run, items, said))
    idx = list(range(len(items)))
    random.Random(7).shuffle(idx)
    shuffled = _groups(partition(run, [items[i] for i in idx], [said[i] for i in idx]))
    assert first == shuffled == _groups(partition(run, items, said))


def test_a_different_person_decision_can_only_split_never_merge(sim):
    run, _ = sim
    base = _groups(partition(run, [], []))
    items = order(run, "random", 3)[:30]
    after = _groups(partition(run, items, [False] * len(items)))     # the reviewer says "different" to everything
    assert _refines(after, base)                                       # every new group lies inside an old one: nothing was merged


def test_a_same_person_decision_only_joins_when_nothing_conflicts(sim):
    run, _ = sim
    base = partition(run, [], [])
    members: dict = {}
    for rid, lab in base.items():
        members.setdefault(lab, []).append(rid)
    veto = {frozenset(v) for v in run.vetoes}
    safe = []
    for it in order(run, "priority"):
        a, b = it.pairs[0][0], it.pairs[0][1]
        ma, mb = members[base[a]], members[base[b]]
        pans = {run.ids.get(r, {}).get("pan") for r in ma} - {None}, {run.ids.get(r, {}).get("pan") for r in mb} - {None}
        auds = {run.ids.get(r, {}).get("aadhaar") for r in ma} - {None}, {run.ids.get(r, {}).get("aadhaar") for r in mb} - {None}
        clash = any(frozenset((x, y)) in veto for x in ma for y in mb) or (pans[0] and pans[1] and pans[0] != pans[1]) or (auds[0] and auds[1] and auds[0] != auds[1])
        if not clash and it.real and base[a] != base[b]:
            safe.append(it)
    assert len(safe) >= 5
    after = _groups(partition(run, safe, [True] * len(safe)))
    assert _refines(_groups(base), after)                              # every old group is inside a new one: nothing was split
    assert len(after) < len(_groups(base))


def test_a_perfect_reviewer_never_hurts_and_helps_more_with_more_reviews(sim):
    run, _ = sim
    items = order(run, "priority")
    f1 = [score(run, items[:n], decisions(items, 0.0, 0)[:n])["f1"] for n in (0, 10, 40, len(items))]
    assert all(b >= a - 1e-9 for a, b in zip(f1, f1[1:], strict=False)) and f1[-1] > f1[0]


def test_a_mistake_that_was_corrected_leaves_no_trace_in_the_clustering(sim, tmp_path):
    run, _ = sim
    it = next(i for i in order(run, "priority") if i.real)
    a, b = (tuple(run.keys[r].split(":", 1)) for r in it.pairs[0][:2])
    con = duckdb.connect()
    con.execute("CREATE TABLE records (rid BIGINT, source_id VARCHAR, source_record_id VARCHAR)")
    for rid, key in run.keys.items():
        s, r = key.split(":", 1)
        con.execute("INSERT INTO records VALUES (?, ?, ?)", [rid, s, r])
    wrong = JudgmentStore.open(f"sqlite:///{tmp_path / 'w.db'}")
    wrong.different_person(a, b, "asha", "mis-click")       # the reviewer got it wrong ...
    wrong.same_person(a, b, "asha", "corrected")            # ... and corrected themselves
    clean = JudgmentStore.open(f"sqlite:///{tmp_path / 'c.db'}")
    clean.same_person(a, b, "asha", "right first time")

    def labels(store):
        c = constraints_from_store(con, store)
        return _groups(cluster(run.nodes, run.edges + c.must, run.ids, list(run.vetoes) + c.cannot, ClusterConfig(), separations=c.separations).labels)
    assert labels(wrong) == labels(clean)
    assert [j.kind for j in wrong.active()] == ["same_person"] and len(wrong.history()) == 3   # but the audit trail keeps the correction


def test_review_orders_are_valid_permutations_and_the_ceiling_order_leads_with_real_matches(sim):
    run, _ = sim
    n = len(run.items)
    for strat in ("priority", "probability", "uncertainty", "expected_gain", "random", "best_possible", "blend_0.2"):
        o = order(run, strat, 1)
        assert len(o) == n and len({(i.cluster_l, i.cluster_r) for i in o}) == n
    assert hit_rate(run, "best_possible", 5) == 1.0 and order(run, "random", 1) != order(run, "random", 2)


def test_simulation_report_numbers_are_consistent(sim):
    run, _ = sim
    budgets = [0, 10, len(run.items)]
    res = simulate(run, budgets, error_rates=(0.0, 0.2), strategies=("priority", "random", "best_possible"), seeds=2)
    curve0 = res[0.0]["priority"]
    assert curve0[0][1]["f1"] <= curve0[-1][1]["f1"] and res[0.0]["priority"][0][1]["f1"] == res[0.2]["priority"][0][1]["f1"]   # no reviews: same start
    assert res[0.2]["priority"][-1][1]["f1"] <= res[0.0]["priority"][-1][1]["f1"] + 1e-9                                      # a noisy reviewer does no better
    assert reviews_to_reach(curve0, curve0[0][1]["f1"], curve0[-1][1]["f1"], 0.8) in budgets
    pred = {run.keys[r]: lab for r, lab in partition(run, [], []).items()}
    assert 0.0 <= exact_person_share(pred, run.truth) <= 1.0 and exact_person_share(run.truth, run.truth) == 1.0
    assert STEWARD_WEIGHT > 1.0
