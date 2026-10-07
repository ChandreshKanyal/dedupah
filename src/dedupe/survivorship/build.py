"""Golden record: for every person (cluster) pick the best value per field from its source records, keeping lineage.

How a value is chosen (per field, or per group of fields that must come from one record):
  1. values a person shares with many other people (an office phone, an agent's email: `shared_value_limit`) only win if nothing else exists
  2. the field's strategy: most recent, most trusted source, most frequent, most complete or best quality
  3. ties: quality, trust and recency in the order the strategy names, then a stable key, so the result never depends on row order
Trust order: `field_trust` for that field, else `source_trust`; sources in neither rank after the listed ones, ordered automatically by how
complete their records are. Record date: the `source_dates` attribute for that source, else the mapped `record_date` field.

Outputs (parquet):
  golden_values.parquet   one row per person x field: value, source_id/source_record_id it came from, strategy, how many records
                          had a value (n_candidates) and how many different values (n_distinct): lineage + conflict evidence
  golden_records.parquet  one row per person: the golden fields side by side, record count, sources, and the fields with conflicts
"""
from pathlib import Path

import duckdb

from dedupe import crypto

from .config import EXTRA_NAME, GOLDEN_FIELDS, MAX_EXTRA_COLUMNS, QUALITY, SHARED_FIELDS, SurvivorshipConfig, extra_column

_SEP = "\x1f"
# standardised / canonical columns the rules read; files written before a column existed are read as if it were empty
_STD_OPTIONAL = {"customer_number": "VARCHAR", "record_date": "VARCHAR", "name_first_is_initial": "BOOLEAN", "name_middle": "VARCHAR", "pan_repaired": "BOOLEAN",
                 "father_name_clean": "VARCHAR", "address_house": "VARCHAR", "dob_placeholder": "BOOLEAN", "dob_precision": "VARCHAR"}


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _rec_date_sql(cfg: SurvivorshipConfig) -> str:
    whens = []
    for src, attr in cfg.source_dates.items():
        v = f"json_extract_string(k.attributes, '$.{attr}')"
        whens.append(f"WHEN s.source_id = {_q(src)} THEN coalesce(try_strptime({v}, '%d/%m/%Y'), try_strptime({v}, '%Y-%m-%d'))::DATE")
    mapped = "try_cast(s.record_date AS DATE)"
    return f"CASE {' '.join(whens)} ELSE {mapped} END" if whens else mapped


def _trust_case(order: list[str]) -> str:
    whens = " ".join(f"WHEN {_q(s)} THEN {i}" for i, s in enumerate(order))
    return f"CASE source_id {whens} ELSE 999999 END" if whens else "999999"


_ORDER = {  # strategy -> ORDER BY inside the cluster, after the shared-value tier; every strategy ends with a stable key
    "most_trusted_source": "trust_rank ASC, quality DESC, rec_date DESC NULLS LAST",
    "most_recent": "rec_date DESC NULLS LAST, quality DESC, trust_rank ASC",
    "most_complete": "completeness DESC, trust_rank ASC, rec_date DESC NULLS LAST",
    "most_frequent": "freq DESC, completeness DESC, trust_rank ASC, rec_date DESC NULLS LAST",
    "best_quality": "quality DESC, freq DESC, completeness DESC, trust_rank ASC, rec_date DESC NULLS LAST",
}


def _source(con, path: Path, optional: dict) -> str:
    have = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {crypto.rp(path)}").fetchall()}
    extra = ", ".join(f"NULL::{t} AS {c}" for c, t in optional.items() if c not in have)
    return f"(SELECT *{', ' + extra if extra else ''} FROM {crypto.rp(path)})"


def _extras(con, canonical: Path, cfg: SurvivorshipConfig) -> tuple[dict[str, str], list[str]]:
    """{source column name: golden column} for the sources' extra columns, and the names that cannot be carried (odd characters, too many)."""
    if not cfg.carry_extra_columns:
        return {}, []
    names = [r[0] for r in con.execute(f"SELECT DISTINCT unnest(json_keys(attributes::JSON)) AS k FROM {crypto.rp(canonical)} "
                                       "WHERE attributes IS NOT NULL AND json_valid(attributes) ORDER BY 1").fetchall()]
    out, skipped = {}, []
    for n in names:
        col = extra_column(n)
        if not EXTRA_NAME.fullmatch(n) or len(out) >= MAX_EXTRA_COLUMNS:
            skipped.append(n)
            continue
        while col in out.values() or col in GOLDEN_FIELDS:      # 'Branch Code' and 'branch_code' are kept apart
            col += "_2"
        out[n] = col
    return out, skipped


def build_golden(con: duckdb.DuckDBPyConnection, std: Path, canonical: Path, clusters: Path, cfg: SurvivorshipConfig, out_dir: Path,
                 overrides: list | None = None) -> dict:
    """`overrides`: steward field overrides [{id, field, value, anchors: [(source_id, source_record_id), ...]}]. Each applies to the person
    holding most of its anchor records; a later one (higher id) replaces an earlier one for the same person and field; value None clears."""
    out_dir.mkdir(parents=True, exist_ok=True)
    extras, extras_skipped = _extras(con, canonical, cfg)
    exprs = {**GOLDEN_FIELDS, **{c: f"nullif(trim(json_extract_string(k.attributes, '$.\"{n}\"')), '')" for n, c in extras.items()}}
    cols = ", ".join(f"({exprs[f]})::VARCHAR AS {f}" for f in exprs)
    quals = ", ".join(f"({QUALITY.get(f, '1')})::INT AS q_{f}" for f in exprs)
    con.execute(f"""CREATE OR REPLACE TABLE g_src AS
        SELECT c.cluster_id, s.source_id, s.source_record_id, {_rec_date_sql(cfg)} AS rec_date, {cols}, {quals}
        FROM {_source(con, std, _STD_OPTIONAL)} s
        JOIN {crypto.rp(clusters)} c USING (source_id, source_record_id)
        JOIN {crypto.rp(canonical)} k USING (source_id, source_record_id)""")

    # trust: the configured order first, then every other source by how many golden fields its records fill on average
    filled = " + ".join(f"({f} IS NOT NULL)::INT" for f in GOLDEN_FIELDS)        # the standard fields only: comparable across sources
    profile = con.execute(f"SELECT source_id, avg({filled}) AS fill, count(rec_date) AS dated FROM g_src GROUP BY 1").fetchall()
    present = {r[0] for r in profile}
    auto = [s for s, _f, _d in sorted(profile, key=lambda r: (-r[1], r[0])) if s not in cfg.source_trust]
    trust = [s for s in cfg.source_trust if s in present] + auto

    con.execute("DROP TABLE IF EXISTS g_values")
    first, shared_skipped = True, 0
    units = cfg.units() + [(c, [c], cfg.extra_fields.get(n, cfg.extra_default_strategy)) for n, c in extras.items()]
    for unit, members, strategy in units:
        lead = members[0]
        order = [s for s in cfg.field_trust[unit] if s in present] + [s for s in trust if s not in cfg.field_trust[unit]] \
            if unit in cfg.field_trust else trust
        key = f"concat_ws('{_SEP}', " + ", ".join(f"coalesce({m}, '')" for m in members) + ")"
        completeness = " + ".join(f"({m} IS NOT NULL)::INT" for m in members) if len(members) > 1 else f"length({lead})"
        quality = " + ".join(f"q_{m}" for m in members)
        shared = (f"{lead} IN (SELECT {lead} FROM g_src WHERE {lead} IS NOT NULL GROUP BY 1 "
                  f"HAVING count(DISTINCT cluster_id) > {int(cfg.shared_value_limit)})") if lead in SHARED_FIELDS else "false"
        con.execute(f"""CREATE OR REPLACE TEMP TABLE g_unit AS
            WITH any_val AS (SELECT cluster_id, source_id, source_record_id, rec_date, {", ".join(members)}, {", ".join("q_" + m for m in members)},
                                    max(({lead} IS NOT NULL)::INT) OVER (PARTITION BY cluster_id) AS has_lead FROM g_src
                             WHERE {" OR ".join(f"{m} IS NOT NULL" for m in members)}),
                 cand AS (SELECT *, {key} AS vkey, {completeness} AS completeness, {quality} AS quality, {_trust_case(order)} AS trust_rank,
                                 coalesce({shared}, false) AS shared
                          FROM any_val WHERE has_lead = 0 OR {lead} IS NOT NULL),
                 stat AS (SELECT cluster_id, count(*) AS n_candidates, count(DISTINCT vkey) AS n_distinct FROM cand GROUP BY 1),
                 counted AS (SELECT *, count(*) OVER (PARTITION BY cluster_id, vkey) AS freq FROM cand),
                 ranked AS (SELECT *, row_number() OVER (PARTITION BY cluster_id ORDER BY shared ASC, {_ORDER[strategy]}, source_id, source_record_id) AS rn,
                                   bool_and(shared) OVER (PARTITION BY cluster_id) AS only_shared, bool_or(shared) OVER (PARTITION BY cluster_id) AS any_shared
                            FROM counted)
            SELECT r.*, st.n_candidates, st.n_distinct FROM ranked r JOIN stat st USING (cluster_id) WHERE rn = 1""")
        shared_skipped += con.execute("SELECT count(*) FROM g_unit WHERE any_shared AND NOT only_shared").fetchone()[0]
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
    con.execute(f"COPY (SELECT * EXCLUDE (rec_date) FROM g_values ORDER BY cluster_id, field) TO '{out_dir / 'golden_values.parquet'}' ({crypto.fmt()})")
    fields = ", ".join(f"max(value) FILTER (WHERE field = '{f}') AS {f}" for f in exprs)
    con.execute(f"""COPY (
        WITH w AS (SELECT cluster_id, {fields}, list(field ORDER BY field) FILTER (WHERE n_distinct > 1) AS conflict_fields FROM g_values GROUP BY 1),
             m AS (SELECT cluster_id, count(*) AS record_count, list(DISTINCT source_id ORDER BY source_id) AS sources FROM g_src GROUP BY 1)
        SELECT m.cluster_id, m.record_count, m.sources, w.* EXCLUDE (cluster_id, conflict_fields), coalesce(w.conflict_fields, []::VARCHAR[]) AS conflict_fields
        FROM m LEFT JOIN w USING (cluster_id) ORDER BY 1) TO '{out_dir / 'golden_records.parquet'}' ({crypto.fmt()})""")
    n = con.execute("SELECT count(DISTINCT cluster_id) FROM g_src").fetchone()[0]
    nv = con.execute("SELECT count(*) FROM g_values").fetchone()[0]
    nc = con.execute("SELECT count(*) FROM g_values WHERE n_distinct > 1").fetchone()[0]
    con.execute("DROP TABLE IF EXISTS g_values")
    con.execute("DROP TABLE IF EXISTS g_src")
    return {"golden_records": n, "values": nv, "values_with_source_conflict": nc, "steward_overrides_applied": applied,
            # how the choice was made, so a person can see it and correct it in the golden-record rules
            "trust_order": trust, "auto_ranked_sources": auto,
            "sources_without_dates": sorted(s for s, _f, d in profile if d == 0),
            "shared_values_passed_over": shared_skipped, "extra_columns": extras, "extra_columns_skipped": extras_skipped}
