"""Golden record: for every person (cluster) pick the best value per field from its source records, keeping lineage.

Outputs (parquet):
  golden_values.parquet   one row per person x field: value, source_id/source_record_id it came from, strategy, how many records
                          had a value (n_candidates) and how many different values (n_distinct): lineage + conflict evidence
  golden_records.parquet  one row per person: the golden fields side by side, record count, sources, and the fields with conflicts
"""
from pathlib import Path

import duckdb

from .config import GOLDEN_FIELDS, SurvivorshipConfig

_SEP = "\x1f"


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _rec_date_sql(cfg: SurvivorshipConfig) -> str:
    whens = []
    for src, attr in cfg.source_dates.items():
        v = f"json_extract_string(k.attributes, '$.{attr}')"
        whens.append(f"WHEN s.source_id = {_q(src)} THEN coalesce(try_strptime({v}, '%d/%m/%Y'), try_strptime({v}, '%Y-%m-%d'))::DATE")
    return f"CASE {' '.join(whens)} ELSE NULL END" if whens else "NULL::DATE"


def _trust_sql(cfg: SurvivorshipConfig) -> str:
    whens = " ".join(f"WHEN {_q(s)} THEN {i}" for i, s in enumerate(cfg.source_trust))
    return f"CASE s.source_id {whens} ELSE 999 END" if whens else "999"


_ORDER = {  # strategy -> ORDER BY inside the cluster; every strategy falls back to trust, then recency, then a stable key
    "most_trusted_source": "trust_rank ASC, rec_date DESC NULLS LAST",
    "most_recent": "rec_date DESC NULLS LAST, trust_rank ASC",
    "most_complete": "completeness DESC, trust_rank ASC, rec_date DESC NULLS LAST",
    "most_frequent": "freq DESC, completeness DESC, trust_rank ASC, rec_date DESC NULLS LAST",
}


def build_golden(con: duckdb.DuckDBPyConnection, std: Path, canonical: Path, clusters: Path, cfg: SurvivorshipConfig, out_dir: Path,
                 overrides: list | None = None) -> dict:
    """`overrides`: steward field overrides [{id, field, value, anchors: [(source_id, source_record_id), ...]}]. Each applies to the person
    holding most of its anchor records; a later one (higher id) replaces an earlier one for the same person and field; value None clears."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(f"({GOLDEN_FIELDS[f]})::VARCHAR AS {f}" for f in GOLDEN_FIELDS)
    con.execute(f"""CREATE OR REPLACE TABLE g_src AS
        SELECT c.cluster_id, s.source_id, s.source_record_id, {_trust_sql(cfg)} AS trust_rank, {_rec_date_sql(cfg)} AS rec_date, {cols}
        FROM read_parquet('{std}') s
        JOIN read_parquet('{clusters}') c USING (source_id, source_record_id)
        JOIN read_parquet('{canonical}') k USING (source_id, source_record_id)""")
    con.execute("DROP TABLE IF EXISTS g_values")
    first = True
    for _unit, members, strategy in cfg.units():
        lead = members[0]
        key = f"concat_ws('{_SEP}', " + ", ".join(f"coalesce({m}, '')" for m in members) + ")"
        completeness = " + ".join(f"({m} IS NOT NULL)::INT" for m in members) if len(members) > 1 else f"length({lead})"
        con.execute(f"""CREATE OR REPLACE TEMP TABLE g_unit AS
            WITH any_val AS (SELECT *, max(({lead} IS NOT NULL)::INT) OVER (PARTITION BY cluster_id) AS has_lead FROM g_src
                             WHERE {" OR ".join(f"{m} IS NOT NULL" for m in members)}),
                 cand AS (SELECT *, {key} AS vkey, {completeness} AS completeness FROM any_val WHERE has_lead = 0 OR {lead} IS NOT NULL),
                 stat AS (SELECT cluster_id, count(*) AS n_candidates, count(DISTINCT vkey) AS n_distinct FROM cand GROUP BY 1),
                 counted AS (SELECT *, count(*) OVER (PARTITION BY cluster_id, vkey) AS freq FROM cand),
                 ranked AS (SELECT *, row_number() OVER (PARTITION BY cluster_id ORDER BY {_ORDER[strategy]}, source_id, source_record_id) AS rn FROM counted)
            SELECT r.*, st.n_candidates, st.n_distinct FROM ranked r JOIN stat st USING (cluster_id) WHERE rn = 1""")
        for m in members:
            sel = (f"SELECT cluster_id, {_q(m)} AS field, {m} AS value, source_id, source_record_id, {_q(strategy)} AS strategy, "
                   f"n_candidates, n_distinct, rec_date FROM g_unit WHERE {m} IS NOT NULL")
            con.execute(f"CREATE TABLE g_values AS {sel}" if first else f"INSERT INTO g_values {sel}")
            first = False
    con.execute("DROP TABLE IF EXISTS g_unit")
    applied = 0
    for o in sorted(overrides or [], key=lambda o: o["id"]):
        anchors = [tuple(a) for a in o["anchors"]]
        row = con.execute("SELECT cluster_id, count(*) FROM g_src WHERE (source_id, source_record_id) IN (SELECT unnest(?), unnest(?)) "
                          "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1", [[a[0] for a in anchors], [a[1] for a in anchors]]).fetchone() if anchors else None
        if row is None:
            continue   # the anchor records are no longer in the data
        cid = row[0]
        con.execute("DELETE FROM g_values WHERE cluster_id = ? AND field = ?", [cid, o["field"]])
        if o["value"] is not None:
            con.execute("INSERT INTO g_values VALUES (?, ?, ?, 'steward', ?, 'steward_override', 1, 1, NULL)",
                        [cid, o["field"], o["value"], str(o["id"])])
        applied += 1
    con.execute(f"COPY (SELECT * EXCLUDE (rec_date) FROM g_values ORDER BY cluster_id, field) TO '{out_dir / 'golden_values.parquet'}' (FORMAT PARQUET)")
    fields = ", ".join(f"max(value) FILTER (WHERE field = '{f}') AS {f}" for f in GOLDEN_FIELDS)
    con.execute(f"""COPY (
        WITH w AS (SELECT cluster_id, {fields}, list(field ORDER BY field) FILTER (WHERE n_distinct > 1) AS conflict_fields FROM g_values GROUP BY 1),
             m AS (SELECT cluster_id, count(*) AS record_count, list(DISTINCT source_id ORDER BY source_id) AS sources FROM g_src GROUP BY 1)
        SELECT m.cluster_id, m.record_count, m.sources, w.* EXCLUDE (cluster_id, conflict_fields), coalesce(w.conflict_fields, []::VARCHAR[]) AS conflict_fields
        FROM m LEFT JOIN w USING (cluster_id) ORDER BY 1) TO '{out_dir / 'golden_records.parquet'}' (FORMAT PARQUET)""")
    n = con.execute("SELECT count(DISTINCT cluster_id) FROM g_src").fetchone()[0]
    nv = con.execute("SELECT count(*) FROM g_values").fetchone()[0]
    nc = con.execute("SELECT count(*) FROM g_values WHERE n_distinct > 1").fetchone()[0]
    con.execute("DROP TABLE IF EXISTS g_values")
    con.execute("DROP TABLE IF EXISTS g_src")
    return {"golden_records": n, "values": nv, "values_with_source_conflict": nc, "steward_overrides_applied": applied}

