"""Dry-run impact of a decision config against ground truth (dev/synthetic only). In production the same numbers
come without truth: pairs affected and a sample for a human to eyeball before a rule goes live."""
from pathlib import Path

import duckdb

from .config import DecisionConfig


def impact(con: duckdb.DuckDBPyConnection, data_dir: Path, cfg: DecisionConfig) -> dict:
    gt = data_dir / "ground_truth"
    con.execute(f"""CREATE OR REPLACE TABLE truth AS
        SELECT r.rid, t.person_id, t.household_id FROM records r
        JOIN read_csv_auto('{gt / 'record_truth.csv'}', all_varchar=true) t
          ON t.source = r.source_id AND t.source_record_id = r.source_record_id WHERE t.role <> 'Co-applicant'""")
    true_total = int(con.execute("SELECT coalesce(sum(n*(n-1)/2), 0) FROM (SELECT count(*) n FROM truth GROUP BY person_id)").fetchone()[0])
    con.execute("""CREATE OR REPLACE TEMP TABLE dt AS
        SELECT d.*, (a.person_id = b.person_id) AS is_true, (a.household_id = b.household_id) AS same_hh
        FROM decisions d JOIN truth a ON a.rid = d.l_rid JOIN truth b ON b.rid = d.r_rid""")
    out = {"true_pairs_total": true_total, "zones": {}, "rules": {}}
    for zone in ("auto_merge", "review", "reject"):
        n, tp = con.execute(f"SELECT count(*), count(*) FILTER (is_true) FROM dt WHERE decision = '{zone}'").fetchone()
        out["zones"][zone] = {"pairs": n, "true": tp, "false": n - tp, "true_share": tp / n if n else None}
    a = out["zones"]["auto_merge"]
    out["auto_merge_quality"] = {"precision": a["true"] / a["pairs"] if a["pairs"] else 1.0, "recall": a["true"] / true_total if true_total else 1.0}
    # score-only zones (no rules) for comparison
    sa = con.execute(f"SELECT count(*), count(*) FILTER (is_true) FROM dt WHERE probability >= {cfg.auto_merge_at}").fetchone()
    out["score_only_auto_merge"] = {"pairs": sa[0], "true": sa[1], "false": sa[0] - sa[1], "precision": sa[1] / sa[0] if sa[0] else 1.0}
    trans = con.execute(f"""SELECT CASE WHEN probability >= {cfg.auto_merge_at} THEN 'auto_merge' WHEN probability >= {cfg.review_at} THEN 'review' ELSE 'reject' END AS by_score,
                                   decision, count(*), count(*) FILTER (is_true) FROM dt GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    out["moves_from_score_only_zone"] = [{"score_only": a_, "final": b_, "pairs": n, "real": t} for a_, b_, n, t in trans if a_ != b_]
    for r in cfg.rules:
        fired, tp, decided, d_tp = con.execute(f"""
            SELECT count(*) FILTER (list_contains(fired, '{r.id}')), count(*) FILTER (list_contains(fired, '{r.id}') AND is_true),
                   count(*) FILTER (decided_by = '{r.id}'), count(*) FILTER (decided_by = '{r.id}' AND is_true) FROM dt""").fetchone()
        score_dec = con.execute(f"""SELECT count(*) FILTER (list_contains(fired, '{r.id}') AND probability >= {cfg.auto_merge_at}),
            count(*) FILTER (list_contains(fired, '{r.id}') AND probability >= {cfg.auto_merge_at} AND NOT is_true) FROM dt""").fetchone()
        out["rules"][r.id] = {"type": r.type, "fired": fired, "fired_on_true_pairs": tp, "fired_on_false_pairs": fired - tp,
                              "decided": decided, "decided_true": d_tp,
                              "would_have_auto_merged_by_score": score_dec[0], "of_which_false": score_dec[1]}
    return out


def render(r: dict) -> str:
    z = r["zones"]
    L = [f"true pairs total {r['true_pairs_total']:,}",
         f"{'zone':<11}{'pairs':>8}{'real matches':>14}{'not matches':>13}{'real%':>8}"]
    for name in ("auto_merge", "review", "reject"):
        s = z[name]
        L.append(f"{name:<11}{s['pairs']:>8,}{s['true']:>14,}{s['false']:>13,}{('n/a' if s['true_share'] is None else format(s['true_share'], '.1%')):>8}")
    q, so = r["auto_merge_quality"], r["score_only_auto_merge"]
    L.append(f"auto-merge: precision {q['precision']:.4f}, recall {q['recall']:.3f} ({z['auto_merge']['false']} false merges) | "
             f"score-only auto-merge would be: precision {so['precision']:.4f} ({so['false']} false merges)")
    for m in r["moves_from_score_only_zone"]:
        L.append(f"rules moved {m['pairs']:,} pairs from {m['score_only']} to {m['final']} ({m['real']:,} of them real matches)")
    L.append(f"rule impact: {'rule':<36}{'type':<13}{'fired':>6}{'on real':>8}{'on false':>9}{'decided':>8}{'false merges stopped':>22}")
    for rid, s in r["rules"].items():
        stopped = s["of_which_false"] if s["type"] == "never_merge" else ""
        L.append(f"             {rid:<36}{s['type']:<13}{s['fired']:>6,}{s['fired_on_true_pairs']:>8,}{s['fired_on_false_pairs']:>9,}{s['decided']:>8,}{stopped:>22}")
    return "\n".join(L)
