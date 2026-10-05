import pytest

from dedupe.evaluation import baseline, evaluate, pairwise_scores
from dedupe.evaluation.loaders import load_truth
from dedupe.synthetic import generate


def test_pairwise_hand_computed():
    # truth: A has records 1,2,3 ; B has records 4,5.  true pairs = 3 + 1 = 4
    truth = {"1": "A", "2": "A", "3": "A", "4": "B", "5": "B"}
    # prediction merges 1,2 (correct) and 3,4 (wrong); 5 alone.  predicted pairs = 2, tp = 1
    pred = {"1": "x", "2": "x", "3": "y", "4": "y", "5": "z"}
    s = pairwise_scores(pred, truth)
    assert (s["tp_pairs"], s["fp_pairs"], s["fn_pairs"]) == (1, 1, 3)
    assert s["precision"] == pytest.approx(0.5) and s["recall"] == pytest.approx(0.25)


def test_perfect_and_all_singletons():
    truth = {"1": "A", "2": "A", "3": "B"}
    assert pairwise_scores({"1": "c", "2": "c", "3": "d"}, truth)["f1"] == 1.0
    s = pairwise_scores({}, truth)  # missing predictions -> singletons
    assert s["precision"] == 1.0 and s["recall"] == 0.0


def test_family_confusion_and_traps_attribution():
    # Persons A and B are in the same household H1; C in H2.
    truth = {"1": "A", "2": "A", "3": "B", "4": "C"}
    hh = {"1": "H1", "2": "H1", "3": "H1", "4": "H2"}
    traps = {"A": {"shared_mobile"}, "B": {"shared_mobile"}, "C": set()}
    pred = {"1": "x", "2": "x", "3": "x", "4": "x"}  # one giant cluster
    r = evaluate(pred, truth, hh, traps)
    assert r["impure_clusters"] == 1 and r["largest_cluster"] == 4
    assert r["fp_pairs"] == 5  # 6 predicted pairs - 1 true pair
    assert r["fp_pairs_same_household"] == 2  # (1,3) and (2,3)
    assert r["fp_pairs_cross_household"] == 3
    assert r["by_trap"]["shared_mobile"]["recall"] == 1.0


def test_baseline_end_to_end(tmp_path):
    generate(120, seed=11, out=tmp_path)
    pred = baseline.run(tmp_path)
    rec_person, rec_hh, traps = load_truth(tmp_path)
    assert set(rec_person) <= set(pred)  # every record got a cluster
    r = evaluate(pred, rec_person, rec_hh, traps)
    assert 0 < r["recall"] < 1  # finds some, misses others (leads/nominees lack PAN)
    assert r["fp_pairs"] > 0  # shared family mobiles cause false merges
    assert r["by_trap"]["shared_mobile"]["fp_pairs_touching"] > 0
