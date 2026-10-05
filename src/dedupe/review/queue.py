"""Steward review queue: one row per PAIR OF PEOPLE (cluster pair), ranked, instead of one row per record pair.

Record pairs in the 'review' zone are collapsed per cluster pair (one decision settles all of them), pairs that clustering
already placed in the same person are dropped (nothing left to decide), and the rest is ranked so the most likely real
matches come first. Needs tables `decisions` and `cluster_map` (rid, cluster_id) in `con`.
"""
from pathlib import Path

import duckdb


def build_queue(con: duckdb.DuckDBPyConnection, out: Path, min_probability: float = 0.0, cap: int | None = None) -> dict:
    """Writes `out` (parquet) and returns counts. Columns: cluster_l, cluster_r, record_pairs, top_probability, mean_probability,
    forced (a business rule sent it to review), size_l, size_r, priority. Sorted by priority, highest first.
    `min_probability` drops weak cases; `cap` keeps only the top N (both optional workload controls)."""
    con.execute("""CREATE OR REPLACE TEMP TABLE _csize AS SELECT cluster_id, count(*) AS size FROM cluster_map GROUP BY 1""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _queue AS
        WITH p AS (SELECT least(a.cluster_id, b.cluster_id) AS cluster_l, greatest(a.cluster_id, b.cluster_id) AS cluster_r,
                          d.probability, d.decided_by
                   FROM decisions d JOIN cluster_map a ON a.rid = d.l_rid JOIN cluster_map b ON b.rid = d.r_rid
                   WHERE d.decision = 'review' AND a.cluster_id <> b.cluster_id),
             g AS (SELECT cluster_l, cluster_r, count(*) AS record_pairs, max(probability) AS top_probability,
                          avg(probability) AS mean_probability, bool_or(decided_by <> 'score') AS forced FROM p GROUP BY 1, 2)
        SELECT g.*, sl.size AS size_l, sr.size AS size_r,
               top_probability + CASE WHEN forced THEN 0.05 ELSE 0 END + 0.02 * ln(1 + record_pairs) AS priority
        FROM g JOIN _csize sl ON sl.cluster_id = g.cluster_l JOIN _csize sr ON sr.cluster_id = g.cluster_r
        WHERE top_probability >= {min_probability}""")
    limit = f"LIMIT {int(cap)}" if cap else ""
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM _queue ORDER BY priority DESC, cluster_l, cluster_r {limit}) TO '{out}' (FORMAT PARQUET)")
    total_pairs = con.execute("SELECT count(*) FROM decisions WHERE decision = 'review'").fetchone()[0]
    queue_all = con.execute("SELECT count(*) FROM _queue").fetchone()[0]
    return {"review_record_pairs": total_pairs, "queue_items": min(queue_all, cap) if cap else queue_all}
