"""Scoring of a deduplication result against ground truth. Everything here is O(records)."""
from collections import Counter, defaultdict


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def pairwise_scores(pred: dict, truth: dict) -> dict:
    """Pairwise precision/recall/F1. Truth records missing from `pred` count as singletons."""
    clusters: dict = defaultdict(Counter)
    for key, label in truth.items():
        clusters[pred.get(key, ("__single__", key))][label] += 1
    pred_pairs = sum(_pairs(sum(c.values())) for c in clusters.values())
    tp = sum(_pairs(n) for c in clusters.values() for n in c.values())
    true_pairs = sum(_pairs(n) for n in Counter(truth.values()).values())
    precision = tp / pred_pairs if pred_pairs else 1.0
    recall = tp / true_pairs if true_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp_pairs": tp,
            "fp_pairs": pred_pairs - tp, "fn_pairs": true_pairs - tp}


def evaluate(pred: dict, truth_person: dict, record_household: dict, person_traps: dict) -> dict:
    """
    pred             record_key -> cluster_id
    truth_person     record_key -> true person_id
    record_household record_key -> true household_id
    person_traps     person_id -> set of hard-case tags
    """
    out = {"records": len(truth_person), "true_persons": len(set(truth_person.values())),
           "unscored_predictions": len(set(pred) - set(truth_person))}
    out.update(pairwise_scores(pred, truth_person))

    clusters: dict = defaultdict(list)
    for key in truth_person:
        clusters[pred.get(key, ("__single__", key))].append(key)
    out["predicted_clusters"] = len(clusters)
    out["largest_cluster"] = max((len(v) for v in clusters.values()), default=0)

    person_clusters: dict = defaultdict(set)
    fp_same_hh = 0
    impure = 0
    trap_stats = defaultdict(lambda: Counter())
    person_total = Counter(truth_person.values())

    for cid, keys in clusters.items():
        size = len(keys)
        per_person = Counter(truth_person[k] for k in keys)
        for person in per_person:
            person_clusters[person].add(cid)
        if len(per_person) > 1:
            impure += 1
            per_hh = Counter(record_household[k] for k in keys)
            # same-person pairs are a subset of same-household pairs, so this isolates family-confusion errors
            fp_same_hh += sum(_pairs(n) for n in per_hh.values()) - sum(_pairs(n) for n in per_person.values())
        for person, n in per_person.items():
            for trap in person_traps.get(person, ()):
                trap_stats[trap]["tp_pairs"] += _pairs(n)
                trap_stats[trap]["fp_pairs_touching"] += n * (size - n)
    out["impure_clusters"] = impure
    out["fp_pairs_same_household"] = fp_same_hh
    out["fp_pairs_cross_household"] = out["fp_pairs"] - fp_same_hh
    out["split_persons"] = sum(1 for s in person_clusters.values() if len(s) > 1)

    for person, m in person_total.items():
        for trap in person_traps.get(person, ()):
            trap_stats[trap]["persons"] += 1
            trap_stats[trap]["true_pairs"] += _pairs(m)
    out["by_trap"] = {}
    for trap, s in sorted(trap_stats.items()):
        out["by_trap"][trap] = {
            "persons": s["persons"], "true_pairs": s["true_pairs"],
            "recall": (s["tp_pairs"] / s["true_pairs"]) if s["true_pairs"] else None,
            "fp_pairs_touching": s["fp_pairs_touching"],
        }
    return out


def render(report: dict, title: str = "") -> str:
    r = report
    lines = [f"== {title or 'evaluation'} (SYNTHETIC data) ==",
             f"records {r['records']:,} | true persons {r['true_persons']:,} | predicted clusters {r['predicted_clusters']:,} | largest cluster {r['largest_cluster']}",
             f"precision {r['precision']:.4f} | recall {r['recall']:.4f} | F1 {r['f1']:.4f}",
             f"false-merge pairs {r['fp_pairs']:,} (same family {r['fp_pairs_same_household']:,}, different family {r['fp_pairs_cross_household']:,}) | missed pairs {r['fn_pairs']:,}",
             f"impure clusters (>1 real person) {r['impure_clusters']:,} | persons split across clusters {r['split_persons']:,}",
             "by hard case:   persons  recall  false-merge pairs touching"]
    for trap, s in r["by_trap"].items():
        rec = "  n/a " if s["recall"] is None else f"{s['recall']:.3f}"
        lines.append(f"  {trap:<16}{s['persons']:>6}   {rec:>6}   {s['fp_pairs_touching']:>10,}")
    return "\n".join(lines)
