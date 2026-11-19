"""Dry run of a decision config: what would change on the pairs of the LAST run, before anything goes live. Needs no ground truth.

Decision rules and thresholds only act on scores that already exist, so the answer is exact for them: the same scored pairs are put
through the candidate config and compared with the decisions the last run actually made. (Clustering and the review queue follow from
the decisions but are not re-run here; a settings change that alters blocking or scoring needs a full run.)
"""
import json
import tempfile
from pathlib import Path

from dedupe import crypto
from dedupe.custom import spec_of_run
from dedupe.decision.apply import decide
from dedupe.decision.conditions import NotInUse, compile_condition, cross_pairs
from dedupe.decision.config import DecisionConfig
from dedupe.scoring.comparisons import pairs_view_sql, spec, use

ZONES = ("auto_merge", "review", "reject")


def dry_run(work: Path, candidate: DecisionConfig, samples: int = 5) -> dict:
    with use(spec_of_run(work)):          # the fields the run compared: a rule on one of the project's own fields is tried too
        return _dry_run(work, candidate, samples)


def _dry_run(work: Path, candidate: DecisionConfig, samples: int) -> dict:
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
        stats = decide(con, scored, candidate, after)
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
            # rules on a field this run did not compare cannot be tried on it (a field added since): the try-out says so
            "rules_not_applied": (stats or {}).get("rules_not_applied", []),
            "note": "decision zones only; the review queue and clusters follow from them but were not re-run"}


def test_rule(work: Path, when: dict, samples: int = 5) -> dict:
    """Which pairs of the LAST run one rule's condition catches, and how that run decided them: the rule builder's live answer. Exact for
    the pairs the run compared; a two-column "same" condition also finds pairs at the next run that this run never compared (said in
    `finds_more`). Raises ConditionError for a condition that is not valid; a field the run did not have gives not_applied=True."""
    with use(spec_of_run(work)):
        return _test_rule(work, when, samples)


def _test_rule(work: Path, when: dict, samples: int) -> dict:
    std, index, decided = work / "standardised.parquet", work / "candidates.index.parquet", work / "decisions.parquet"
    for f in (std, index, decided):
        if not f.exists():
            raise FileNotFoundError(f"{f} not found")
    try:
        sql = compile_condition(when)
    except NotInUse:
        return {"pairs": 0, "caught": 0, "now": dict.fromkeys(ZONES, 0), "examples": [], "not_applied": True, "finds_more": False}
    con = crypto.connect()
    try:
        con.execute(f"""CREATE TABLE records AS SELECT i.rid, s.* FROM {crypto.rp(std)} s
                        JOIN {crypto.rp(index)} i USING (source_id, source_record_id)""")
        # the run's pairs with their comparison levels and decision (levels are kept in the scored pairs)
        con.execute(f"CREATE TABLE scored AS SELECT * FROM {crypto.rp(work / 'scored_pairs.parquet')}")
        con.execute(f"CREATE TABLE wide AS {pairs_view_sql('scored')}")
        con.execute(f"""CREATE TABLE hit AS SELECT w.l_rid, w.r_rid, w.probability, d.decision FROM wide w
                        JOIN {crypto.rp(decided)} d USING (l_rid, r_rid) WHERE coalesce({sql}, false)""")
        pairs = con.execute("SELECT count(*) FROM wide").fetchone()[0]
        now = dict(con.execute("SELECT decision, count(*) FROM hit GROUP BY 1").fetchall())
        have = {r[0] for r in con.execute("DESCRIBE records").fetchall()}
        label = next((f.col for f in spec().custom if f.kind in ("company", "text", "name") and f.col in have), None)
        def name(t: str) -> str:
            return f"coalesce({t}.name_clean, {t}.{label})" if label else f"{t}.name_clean"
        rows = con.execute(f"""SELECT l.source_id, l.source_record_id, {name('l')}, r.source_id, r.source_record_id, {name('r')},
                                      h.probability, h.decision
                               FROM hit h JOIN records l ON l.rid = h.l_rid JOIN records r ON r.rid = h.r_rid
                               ORDER BY h.probability DESC LIMIT {int(samples)}""").fetchall()
    finally:
        con.close()
    # pairs found by one column against another: only new when the run did not already look for them that way (a rule in use with them)
    try:
        used = json.loads((work / "config_used" / "decision.json").read_text(encoding="utf-8"))
        searched = {tuple(sorted(c)) for r in used.get("rules") or [] if r.get("enabled", True) and r.get("type") in ("auto_merge", "force_review")
                    for c in cross_pairs(r.get("when"))}
    except (OSError, ValueError):
        searched = set()
    return {"pairs": pairs, "caught": sum(now.values()), "now": {z: now.get(z, 0) for z in ZONES}, "not_applied": False,
            "finds_more": any(tuple(sorted(c)) not in searched for c in cross_pairs(when)),
            "examples": [{"left": {"source": x[0], "record": x[1], "name": x[2]}, "right": {"source": x[3], "record": x[4], "name": x[5]},
                          "probability": round(x[6], 4), "now": x[7]} for x in rows]}


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
