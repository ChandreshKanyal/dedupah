"""Evaluate scored pairs against ground truth (dev/synthetic only)."""
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np

from dedupe import crypto

from .comparisons import COMPARISONS

THRESHOLDS = (0.5, 0.8, 0.9, 0.95, 0.99, 0.999)


def evaluate(con: duckdb.DuckDBPyConnection, scored: Path, data_dir: Path, report_threshold: float = 0.9) -> dict:
    gt = data_dir / "ground_truth"
    con.execute(f"""CREATE OR REPLACE TABLE truth AS
        SELECT r.rid, t.person_id, t.household_id FROM records r
        JOIN read_csv_auto('{gt / 'record_truth.csv'}', all_varchar=true) t
          ON t.source = r.source_id AND t.source_record_id = r.source_record_id WHERE t.role <> 'Co-applicant'""")
    con.execute(f"""CREATE OR REPLACE TABLE traps AS
        SELECT person_id, string_split(traps, '|') AS traps FROM read_csv_auto('{gt / 'persons_truth.csv'}', all_varchar=true)""")
    true_total = int(con.execute("SELECT coalesce(sum(n*(n-1)/2), 0) FROM (SELECT count(*) n FROM truth GROUP BY person_id)").fetchone()[0])
    con.execute(f"""CREATE OR REPLACE TABLE sp AS
        SELECT s.*, ta.person_id AS pa, tb.person_id AS pb, (ta.person_id = tb.person_id) AS is_true,
               (ta.household_id = tb.household_id) AS same_hh
        FROM {crypto.rp(scored)} s JOIN truth ta ON ta.rid = s.l_rid JOIN truth tb ON tb.rid = s.r_rid""")
    prob, is_true = [np.array(x) for x in zip(*con.execute("SELECT probability, is_true FROM sp").fetchall(), strict=True)]
    in_cand = int(is_true.sum())
    out = {"true_pairs_total": true_total, "true_pairs_in_candidates": in_cand, "candidate_pairs": len(prob), "table": []}

    def prf(mask):
        tp, fp = int((mask & is_true).sum()), int((mask & ~is_true).sum())
        p = tp / (tp + fp) if tp + fp else 1.0
        r = tp / true_total if true_total else 1.0
        return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "tp": tp, "fp": fp}

    for t in THRESHOLDS:
        out["table"].append({"threshold": t, **prf(prob >= t)})
    order = np.argsort(-prob)
    tp_c = np.cumsum(is_true[order])
    k = np.arange(1, len(prob) + 1)
    prec, rec = tp_c / k, tp_c / true_total
    f1 = np.where(prec + rec > 0, 2 * prec * rec / (prec + rec), 0)
    b = int(np.argmax(f1))
    out["best_f1"] = {"f1": float(f1[b]), "precision": float(prec[b]), "recall": float(rec[b]), "threshold": float(prob[order][b])}
    edges = [0, 0.01, 0.1, 0.5, 0.9, 0.99, 1.0000001]
    out["calibration"] = [{"range": f"{lo:g}-{min(hi, 1):g}", "pairs": int(((prob >= lo) & (prob < hi)).sum()),
                           "actual_match_rate": float(is_true[(prob >= lo) & (prob < hi)].mean()) if ((prob >= lo) & (prob < hi)).any() else None}
                          for lo, hi in zip(edges, edges[1:], strict=False)]
    # error anatomy at the report threshold
    fp_rows = con.execute(f"SELECT same_hh, pa, pb FROM sp WHERE NOT is_true AND probability >= {report_threshold}").fetchall()
    trap_map = {pid: set(t or []) - {""} for pid, t in con.execute("SELECT person_id, traps FROM traps").fetchall()}
    fp_trap = Counter()
    for _, pa_, pb_ in fp_rows:
        for t in trap_map.get(pa_, set()) | trap_map.get(pb_, set()):
            fp_trap[t] += 1
    names = [c.name for c in COMPARISONS]
    W = np.array(con.execute(f"SELECT {', '.join('w_' + n for n in names)} FROM sp WHERE is_true AND probability < {report_threshold}").fetchall() or np.zeros((0, len(names))))
    fn_weakest = Counter(names[i] for i in W.argmin(axis=1)) if len(W) else Counter()
    Wf = np.array(con.execute(f"SELECT {', '.join('w_' + n for n in names)} FROM sp WHERE NOT is_true AND probability >= {report_threshold}").fetchall() or np.zeros((0, len(names))))
    fp_strongest = Counter(names[i] for i in Wf.argmax(axis=1)) if len(Wf) else Counter()
    out["errors_at"] = {"threshold": report_threshold, "false_pairs": len(fp_rows),
                        "false_pairs_same_household": sum(1 for r in fp_rows if r[0]),
                        "false_pairs_by_trap": dict(fp_trap.most_common()), "missed_pairs_in_candidates": int(len(W)),
                        "missed_weakest_field": dict(fn_weakest.most_common()), "false_strongest_field": dict(fp_strongest.most_common())}
    return out


def render(r: dict) -> str:
    L = [f"candidate pairs {r['candidate_pairs']:,} | true pairs {r['true_pairs_total']:,} (of which in candidates {r['true_pairs_in_candidates']:,})",
         "threshold  precision  recall     F1      TP     FP"]
    for t in r["table"]:
        L.append(f"{t['threshold']:>9g}  {t['precision']:>9.4f}  {t['recall']:>6.4f}  {t['f1']:>6.4f}  {t['tp']:>6,}  {t['fp']:>5,}")
    b = r["best_f1"]
    L.append(f"best F1 {b['f1']:.4f} (precision {b['precision']:.4f}, recall {b['recall']:.4f}) at probability >= {b['threshold']:.4f}")
    L.append("calibration (predicted range -> actual match rate): " + "; ".join(
        f"{c['range']}: {c['pairs']:,} pairs -> {('n/a' if c['actual_match_rate'] is None else format(c['actual_match_rate'], '.1%'))}" for c in r["calibration"]))
    e = r["errors_at"]
    L.append(f"at {e['threshold']}: false merges {e['false_pairs']:,} ({e['false_pairs_same_household']:,} same family) | missed (in candidates) {e['missed_pairs_in_candidates']:,}")
    L.append(f"  false merges by hard case: {e['false_pairs_by_trap']}")
    L.append(f"  missed pairs - weakest field: {e['missed_weakest_field']}")
    L.append(f"  false merges - strongest field: {e['false_strongest_field']}")
    return "\n".join(L)
