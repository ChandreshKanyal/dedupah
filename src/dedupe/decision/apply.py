"""Turn scored pairs into decisions: auto_merge / review / reject, with the rule (or score zone) that decided."""
from pathlib import Path

import duckdb

from dedupe import crypto
from dedupe.scoring.comparisons import pairs_view_sql

from .conditions import compile_condition
from .config import PRECEDENCE, DecisionConfig

ZONE_REASON = {"auto_merge": "score at or above the auto-merge threshold", "review": "score between the review and auto-merge thresholds",
               "reject": "score below the review threshold"}


def decide(con: duckdb.DuckDBPyConnection, scored: Path, cfg: DecisionConfig, out: Path) -> dict:
    """Needs `records` (rid + standardised columns) in `con`. Writes `out` with, per pair:
    l_rid, r_rid, probability, decision, decided_by ('score' or a rule id), reason, fired (ids of every rule that matched)."""
    con.execute(f"CREATE OR REPLACE TABLE scored AS SELECT * FROM {crypto.rp(scored)}")
    con.execute(f"CREATE OR REPLACE TEMP TABLE wide AS {pairs_view_sql('scored')}")
    rules = [r for r in cfg.rules if r.enabled]
    fire = {r.id: compile_condition(r.when) for r in rules}
    flags = ", ".join(f"coalesce({sql}, false) AS f_{rid}" for rid, sql in fire.items())
    con.execute(f"CREATE OR REPLACE TEMP TABLE flagged AS SELECT l_rid, r_rid, probability, {flags} FROM wide" if flags
                else "CREATE OR REPLACE TEMP TABLE flagged AS SELECT l_rid, r_rid, probability FROM wide")
    # decision by precedence: first rule type that has a fired rule wins; within a type the first listed rule is credited
    whens, by = [], []
    zone = {"never_merge": "reject", "force_review": "review", "auto_merge": "auto_merge", "not_enough_evidence": "reject"}
    for rtype in PRECEDENCE:
        for r in (x for x in rules if x.type == rtype):
            whens.append(f"WHEN f_{r.id} THEN '{zone[rtype]}'")
            by.append(f"WHEN f_{r.id} THEN '{r.id}'")
    t_auto, t_rev = cfg.auto_merge_at, cfg.review_at
    dec = f"CASE {' '.join(whens)} WHEN probability >= {t_auto} THEN 'auto_merge' WHEN probability >= {t_rev} THEN 'review' ELSE 'reject' END"
    who = f"CASE {' '.join(by)} ELSE 'score' END" if by else "'score'"
    fired = "list_filter([" + ", ".join(f"CASE WHEN f_{rid} THEN '{rid}' END" for rid in fire) + "], x -> x IS NOT NULL)" if fire else "[]::VARCHAR[]"
    reasons = " ".join(f"WHEN decided_by = '{r.id}' THEN {_q(r.reason or r.id)}" for r in rules)
    offsets = " ".join(f"WHEN decided_by = '{r.id}' THEN {float(r.priority_offset)}" for r in rules if r.type == "force_review" and r.priority_offset)
    offset_sql = f"CASE {offsets} ELSE 0.0 END" if offsets else "0.0"
    con.execute(f"""CREATE OR REPLACE TABLE decisions AS
        SELECT l_rid, r_rid, probability, decision, decided_by, ({offset_sql})::FLOAT AS rank_offset,
               CASE {reasons} WHEN decision = 'auto_merge' THEN {_q(ZONE_REASON['auto_merge'])}
                    WHEN decision = 'review' THEN {_q(ZONE_REASON['review'])} ELSE {_q(ZONE_REASON['reject'])} END AS reason, fired
        FROM (SELECT *, {dec} AS decision, {who} AS decided_by, {fired} AS fired FROM flagged)""")
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY decisions TO '{out}' ({crypto.fmt()})")
    counts = dict(con.execute("SELECT decision, count(*) FROM decisions GROUP BY 1").fetchall())
    return {"pairs": sum(counts.values()), **{k: counts.get(k, 0) for k in ("auto_merge", "review", "reject")}}


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
