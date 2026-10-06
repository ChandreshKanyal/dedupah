"""Dry run of a decision config: what would change on the pairs of the LAST run, before anything goes live. Needs no ground truth.

Decision rules and thresholds only act on scores that already exist, so the answer is exact for them: the same scored pairs are put
through the candidate config and compared with the decisions the last run actually made. (Clustering and the review queue follow from
the decisions but are not re-run here; a settings change that alters blocking or scoring needs a full run.)
"""
import tempfile
from pathlib import Path

from dedupe import crypto
from dedupe.decision.apply import decide
from dedupe.decision.config import DecisionConfig

ZONES = ("auto_merge", "review", "reject")


def dry_run(work: Path, candidate: DecisionConfig, samples: int = 5) -> dict:
    std, index = work / "standardised.parquet", work / "candidates.index.parquet"
    scored, before = work / "scored_pairs.parquet", work / "decisions.parquet"
    for f in (std, index, scored, before):
        if not f.exists():
            raise FileNotFoundError(f"{f} not found: a dry run compares against the last pipeline run in {work}")
    con = crypto.connect()
    con.execute(f"""CREATE TABLE records AS SELECT i.rid, s.* FROM {crypto.rp(std)} s
                    JOIN {crypto.rp(index)} i USING (source_id, source_record_id)""")
    with tempfile.TemporaryDirectory() as tmp:
        after = Path(tmp) / "decisions.parquet"
        decide(con, scored, candidate, after)
        con.execute(f"""CREATE TABLE cmp AS SELECT a.l_rid, a.r_rid, a.probability, b.decision AS before, a.decision AS after,
                        b.decided_by AS before_by, a.decided_by AS after_by
                        FROM {crypto.rp(before)} b JOIN {crypto.rp(after)} a USING (l_rid, r_rid)""")
        n = con.execute("SELECT count(*) FROM cmp").fetchone()[0]
        zone = {w: dict(con.execute(f"SELECT {w}, count(*) FROM cmp GROUP BY 1").fetchall()) for w in ("before", "after")}
        moves = con.execute("SELECT before, after, count(*) FROM cmp WHERE before <> after GROUP BY 1, 2 ORDER BY 3 DESC").fetchall()
        by_rule = {r[0]: {"before": r[1], "after": r[2]} for r in con.execute("""
            SELECT coalesce(b.k, a.k), coalesce(b.n, 0), coalesce(a.n, 0)
            FROM (SELECT before_by k, count(*) n FROM cmp GROUP BY 1) b FULL JOIN (SELECT after_by k, count(*) n FROM cmp GROUP BY 1) a ON a.k = b.k
            WHERE coalesce(b.n, 0) <> coalesce(a.n, 0) ORDER BY 1""").fetchall()}
        examples = {}
        for src, dst, _ in moves:
            examples[f"{src} -> {dst}"] = [
                {"left": f"{r[0]}/{r[1]} {r[2] or ''}".strip(), "right": f"{r[3]}/{r[4]} {r[5] or ''}".strip(), "probability": round(r[6], 4),
                 "decided_by_before": r[7], "decided_by_after": r[8]}
                for r in con.execute(f"""SELECT l.source_id, l.source_record_id, l.name_clean, r.source_id, r.source_record_id, r.name_clean,
                        c.probability, c.before_by, c.after_by
                        FROM cmp c JOIN records l ON l.rid = c.l_rid JOIN records r ON r.rid = c.r_rid
                        WHERE c.before = '{src}' AND c.after = '{dst}' ORDER BY c.probability DESC LIMIT {int(samples)}""").fetchall()]
    con.close()
    return {"pairs": n, "zones_before": {z: zone["before"].get(z, 0) for z in ZONES}, "zones_after": {z: zone["after"].get(z, 0) for z in ZONES},
            "moved": {f"{a} -> {b}": c for a, b, c in moves}, "changed_by_rule": by_rule, "examples": examples,
            "note": "decision zones only; the review queue and clusters follow from them but were not re-run"}


def render(r: dict) -> str:
    lines = [f"{r['pairs']:,} scored pairs; zones before -> after:"]
    for z in ZONES:
        lines.append(f"  {z:<11} {r['zones_before'][z]:>9,} -> {r['zones_after'][z]:>9,}  ({r['zones_after'][z] - r['zones_before'][z]:+,})")
    lines.append("moved: " + (", ".join(f"{k} {v:,}" for k, v in r["moved"].items()) or "nothing"))
    for rid, c in r["changed_by_rule"].items():
        lines.append(f"  decided by {rid}: {c['before']:,} -> {c['after']:,}")
    for k, ex in r["examples"].items():
        lines.append(f"examples {k}:")
        lines += [f"  {e['left']}  ~  {e['right']}  (p={e['probability']}, {e['decided_by_before']} -> {e['decided_by_after']})" for e in ex]
    return "\n".join(lines)
