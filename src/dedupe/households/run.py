"""People (clusters) -> households.

    python -m dedupe.households --data data/synthetic
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import pyarrow as pa

from dedupe.evaluation.metrics import pairwise_scores

from . import evidence as ev
from .group import group_households


def truth_of_clusters(con, data_dir: Path):
    """Dev only: majority true household and majority true person of each predicted person-cluster."""
    with (data_dir / "ground_truth" / "record_truth.csv").open(encoding="utf-8", newline="") as f:
        t = {(r["source"], r["source_record_id"]): (r["household_id"], r["person_id"]) for r in csv.DictReader(f) if r["role"] != "Co-applicant"}
    hv, pv = defaultdict(Counter), defaultdict(Counter)
    for cid, src, rid in con.execute("SELECT cluster_id, source_id, source_record_id FROM rc").fetchall():
        if (src, rid) in t:
            hv[cid][t[(src, rid)][0]] += 1
            pv[cid][t[(src, rid)][1]] += 1
    return ({c: v.most_common(1)[0][0] for c, v in hv.items()}, {c: v.most_common(1)[0][0] for c, v in pv.items()})


def evaluate(household_of: dict, truth_hh: dict, edges: list, edge_evidence: dict, truth_person: dict | None = None) -> dict:
    nodes = set(truth_hh)
    pred = {c: household_of.get(c, ("single", c)) for c in nodes}
    out = dict(pairwise_scores(pred, truth_hh))
    members = defaultdict(set)
    for c, h in truth_hh.items():
        members[h].add(c)
    multi = [m for m in members.values() if len(m) > 1]
    pred_members = defaultdict(set)
    for c, h in pred.items():
        pred_members[h].add(c)
    perfect = sum(1 for m in multi if len({pred[c] for c in m}) == 1 and len(pred_members[pred[next(iter(m))]]) == len(m))
    out.update({"true_households_with_2plus_people": len(multi), "households_reproduced_exactly": perfect,
                "exact_rate": perfect / len(multi) if multi else 1.0})
    if truth_person:  # family links only: ignore pairs that are really two fragments of one person
        by_true = defaultdict(list)
        for c, h in truth_hh.items():
            by_true[h].append(c)
        true_fam = {(min(a, b), max(a, b)) for cs in by_true.values() for i, a in enumerate(cs) for b in cs[i + 1:]
                    if truth_person[a] != truth_person[b]}
        pred_fam = set()
        for cs in pred_members.values():
            cs = sorted(cs)
            pred_fam |= {(a, b) for i, a in enumerate(cs) for b in cs[i + 1:] if truth_person[a] != truth_person[b]}
        tp = len(true_fam & pred_fam)
        p = tp / len(pred_fam) if pred_fam else 1.0
        r = tp / len(true_fam) if true_fam else 1.0
        out["family_only"] = {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0,
                              "true_family_pairs": len(true_fam), "found": tp}
    # per evidence type: share of links that join two people of the same true household
    per = defaultdict(lambda: [0, 0])
    for a, b, _ in edges:
        same = truth_hh.get(a) is not None and truth_hh.get(a) == truth_hh.get(b)
        for e in edge_evidence[(a, b)].split("+"):
            per[e][0] += 1
            per[e][1] += same
    out["evidence_precision"] = {e: {"links": n, "same_household": s, "precision": s / n} for e, (n, s) in sorted(per.items())}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--std", type=Path, default=Path("data/staging/standardised.parquet"))
    ap.add_argument("--clusters", type=Path, default=Path("data/staging/clusters/clusters.parquet"))
    ap.add_argument("--canonical", type=Path, default=Path("data/staging/canonical.parquet"))
    ap.add_argument("--out", type=Path, default=Path("data/staging/households"))
    ap.add_argument("--data", type=Path, default=Path("data/synthetic"), help="ground truth for evaluation (dev only)")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--max-size", type=int, default=10)
    a = ap.parse_args()

    con = duckdb.connect()
    n_ev = ev.build(con, str(a.std), str(a.clusters), str(a.canonical))
    scores = ev.pair_scores(con)
    edges = [(x, y, s) for x, y, s, _ in scores]
    edge_ev = {(x, y): e for x, y, _, e in scores}
    people = [r[0] for r in con.execute("SELECT DISTINCT cluster_id FROM rc").fetchall()]
    hh, info = group_households(people, edges, a.threshold, a.max_size)

    a.out.mkdir(parents=True, exist_ok=True)
    con.register("hm", pa.table({"cluster_id": list(hh), "household_id": [f"hh_{hh[c]}" for c in hh]}))
    con.execute(f"COPY (SELECT m.cluster_id, m.household_id, count(*) OVER (PARTITION BY m.household_id) AS household_size FROM hm m ORDER BY 2, 1) "
                f"TO '{a.out / 'household_members.parquet'}' (FORMAT PARQUET)")
    con.register("rel", pa.table({"person_a": [e[0] for e in scores], "person_b": [e[1] for e in scores],
                                  "score": [e[2] for e in scores], "evidence": [e[3] for e in scores]}))
    con.execute(f"COPY rel TO '{a.out / 'relationships.parquet'}' (FORMAT PARQUET)")

    truth_hh, truth_person = truth_of_clusters(con, a.data)
    report = {"evidence_rows": n_ev, "related_pairs": len(scores), "grouping": info | {"flags": None},
              "by_threshold": {}}
    for t in (3, 4, 5, 6, 8):
        h_t, i_t = group_households(people, edges, t, a.max_size)
        report["by_threshold"][t] = evaluate(h_t, truth_hh, edges, edge_ev, truth_person) | {"households": i_t["households"], "largest": i_t["largest"]}
    report["chosen"] = {"threshold": a.threshold, **evaluate(hh, truth_hh, edges, edge_ev, truth_person)}
    (a.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"people {len(people):,} | related pairs {len(scores):,} | households {info['households']:,} ({info['singletons']:,} single-person) | "
          f"largest {info['largest']} | size-guard escalations {info['escalated_components']}, oversize {info['oversize_households']}")
    print("threshold  precision  recall     F1   exact-households  households  largest")
    for t, r in report["by_threshold"].items():
        mark = " <- default" if float(t) == a.threshold else ""
        print(f"{t:>9}  {r['precision']:>9.4f}  {r['recall']:>6.4f}  {r['f1']:>6.4f}  {r['households_reproduced_exactly']:>6,}/{r['true_households_with_2plus_people']:<6,}  {r['households']:>10,}  {r['largest']:>7}{mark}")
    fo = report["chosen"]["family_only"]
    print(f"family links only (ignoring fragments of one person): precision {fo['precision']:.4f} recall {fo['recall']:.4f} F1 {fo['f1']:.4f} "
          f"({fo['found']:,} of {fo['true_family_pairs']:,} family pairs found)")
    print("evidence precision (share of links inside one true household): " + "; ".join(
        f"{k} {v['precision']:.1%} ({v['links']:,})" for k, v in report["chosen"]["evidence_precision"].items()))


if __name__ == "__main__":
    main()
