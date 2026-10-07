"""Candidate pair generation: union of blocking rules with a frequency cap on over-common keys."""
from pathlib import Path

import duckdb

from dedupe import crypto

from .rules import DEFAULT_RULES, Rule


def generate(std_path: Path, out_path: Path, rules: list[Rule] | None = None, cap: int = 50,
             con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """
    Writes `out_path` (l_rid, r_rid, rules[]) and `<out>.index.parquet` (rid, source_id, source_record_id).
    A key held by more than `cap` records is treated as generic (a shared agent phone, a very common name+year)
    and skipped: it would create a flood of pairs and carries almost no evidence.
    Returns per-rule stats. The connection keeps `records` and `rule_pairs` for reporting.
    """
    rules = rules or DEFAULT_RULES
    con = con or crypto.connect()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""CREATE OR REPLACE TABLE records AS
        SELECT row_number() OVER (ORDER BY source_id, source_record_id) - 1 AS rid, * FROM {crypto.rp(std_path)}""")
    con.execute("CREATE OR REPLACE TABLE rule_pairs (rule VARCHAR, l BIGINT, r BIGINT)")
    skip = "skip_matching" in [r[0] for r in con.execute("DESCRIBE records").fetchall()]
    stats = {}
    for rule in rules:
        con.execute(f"CREATE OR REPLACE TEMP TABLE k AS SELECT DISTINCT rid, key FROM ({rule.sql})")
        if skip:                          # companies and repeated header lines: in the output, but never paired with anyone
            con.execute("DELETE FROM k WHERE rid IN (SELECT rid FROM records WHERE skip_matching)")
        con.execute("CREATE OR REPLACE TEMP TABLE blocks AS SELECT key, count(*) n FROM k GROUP BY key HAVING count(*) > 1")
        capped = con.execute(f"SELECT count(*), coalesce(sum(n), 0) FROM blocks WHERE n > {cap}").fetchone()
        one_side = ""
        if rule.one_side:
            con.execute(f"CREATE OR REPLACE TEMP TABLE probe AS SELECT rid FROM records WHERE {rule.one_side}")
            one_side = "AND (a.rid IN (SELECT rid FROM probe) OR b.rid IN (SELECT rid FROM probe))"
        con.execute(f"""INSERT INTO rule_pairs
            SELECT '{rule.name}', a.rid, b.rid FROM k a JOIN k b ON a.key = b.key AND a.rid < b.rid
            WHERE a.key IN (SELECT key FROM blocks WHERE n <= {cap}) {one_side}
            GROUP BY 1, 2, 3""")
        stats[rule.name] = {"blocks": con.execute("SELECT count(*) FROM blocks").fetchone()[0],
                            "capped_blocks": capped[0], "records_in_capped_blocks": int(capped[1])}
    con.execute(f"""COPY (SELECT l AS l_rid, r AS r_rid, list(DISTINCT rule ORDER BY rule) AS rules
                    FROM rule_pairs GROUP BY l, r) TO '{out_path}' ({crypto.fmt()})""")
    con.execute(f"COPY (SELECT rid, source_id, source_record_id FROM records) TO '{out_path.with_suffix('.index.parquet')}' ({crypto.fmt()})")
    n = con.execute("SELECT count(*) FROM records").fetchone()[0]
    pairs = con.execute(f"SELECT count(*) FROM {crypto.rp(out_path)}").fetchone()[0]
    return {"records": n, "candidate_pairs": pairs, "all_pairs": n * (n - 1) // 2, "cap": cap, "rules": stats}
