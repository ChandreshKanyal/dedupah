"""Incremental runs: reuse what the previous run of the same project already worked out for records that did not change.

Every run stores a fingerprint of each record as read (`record_hashes.parquet`, before Aadhaar is erased). The next run compares:
  unchanged records   their standardised rows are copied from the previous run (standardisation is the slowest stage)
  new / changed       standardised again
  removed             simply absent
Blocking, clustering, golden records and households always run over ALL records, so people are formed exactly as in a full run.
Scoring reuses the previous model and the previous scores of pairs whose two records are both unchanged; only pairs touching a new or
changed record are scored. When too much changed (`MAX_CHANGE_FOR_MODEL`) or the previous model could not score meaningfully, the model is
learned again on all pairs (standardised rows are still reused).

Reuse is refused, and the run is a full one, when the previous run is missing a file, was made by a different standardisation or
comparison logic (fingerprints below), or used different run settings. Reused scores keep the term frequencies of the run that made
them; a full run (portal: "Full reload", on the New run page or a schedule) refreshes everything.
"""
import hashlib
import json
from pathlib import Path

import duckdb

from dedupe import crypto

from .mapping import OUTPUT_COLUMNS
from .scoring.comparisons import COMPARISONS, level_case_sql
from .standardise.run import SCHEMA as STD_SCHEMA
from .standardise.run import STANDARDISE_VERSION

MAX_CHANGE_FOR_MODEL = 0.3          # more than this share of records new, changed or removed: learn the model again
HASHES = "record_hashes.parquet"
NEEDED = ("standardised.parquet", HASHES, "candidates.parquet", "candidates.index.parquet", "scored_pairs.parquet", "pipeline_summary.json")


def fingerprints() -> dict:
    """What must match for an earlier run's work to be reusable."""
    comps = json.dumps([[c.name, c.n_levels, level_case_sql(c)] for c in COMPARISONS])
    return {"standardise": f"{STANDARDISE_VERSION}:{hashlib.sha256(str(STD_SCHEMA).encode()).hexdigest()[:16]}",
            "comparisons": hashlib.sha256(comps.encode()).hexdigest()[:16]}


def write_hashes(con: duckdb.DuckDBPyConnection, canonical: Path, out: Path) -> None:
    cols = ", ".join(f"coalesce({c}, '\x1e')" for c in OUTPUT_COLUMNS)
    con.execute(f"""COPY (SELECT source_id, source_record_id, md5(concat_ws('\x1f', {cols})) AS rec_hash FROM {crypto.rp(canonical)})
                    TO '{out}' ({crypto.fmt()})""")


def check(prev: Path | None, model: Path | None, settings_hash: str) -> tuple[bool, str]:
    """(usable, reason in plain words)."""
    if prev is None:
        return False, "no earlier successful run"
    prev = Path(prev)
    missing = [f for f in NEEDED if not (prev / f).exists()]
    if missing or model is None or not Path(model).exists():
        return False, "the earlier run predates incremental runs or is incomplete"
    s = json.loads((prev / "pipeline_summary.json").read_text(encoding="utf-8"))
    if s.get("fingerprints") != fingerprints():
        return False, "the earlier run was made by a different version of the cleaning or comparison logic"
    if ((s.get("configs") or {}).get("effective_settings") or {}).get("hash") != settings_hash:
        return False, "the run settings changed since the earlier run"
    return True, ""


def delta(con: duckdb.DuckDBPyConnection, prev: Path, hashes: Path) -> dict:
    """Creates table `changed_keys` (source_id, source_record_id) of new or changed records; returns the counts."""
    con.execute(f"CREATE OR REPLACE TABLE h_new AS SELECT * FROM {crypto.rp(hashes)}")
    con.execute(f"CREATE OR REPLACE TABLE h_old AS SELECT * FROM {crypto.rp(prev / HASHES)}")
    con.execute("""CREATE OR REPLACE TABLE changed_keys AS SELECT n.source_id, n.source_record_id FROM h_new n
                   LEFT JOIN h_old o USING (source_id, source_record_id) WHERE o.rec_hash IS DISTINCT FROM n.rec_hash""")
    total, changed = con.execute("SELECT (SELECT count(*) FROM h_new), (SELECT count(*) FROM changed_keys)").fetchone()
    new = con.execute("SELECT count(*) FROM changed_keys c ANTI JOIN h_old o USING (source_id, source_record_id)").fetchone()[0]
    removed = con.execute("SELECT count(*) FROM h_old o ANTI JOIN h_new n USING (source_id, source_record_id)").fetchone()[0]
    return {"records": total, "unchanged": total - changed, "new": new, "changed": changed - new, "removed": removed}


def split_canonical(con: duckdb.DuckDBPyConnection, canonical: Path, out: Path) -> None:
    """The canonical rows of new or changed records, for standardisation."""
    con.execute(f"""COPY (SELECT k.* FROM {crypto.rp(canonical)} k SEMI JOIN changed_keys c USING (source_id, source_record_id))
                    TO '{out}' ({crypto.fmt()})""")


def merge_standardised(con: duckdb.DuckDBPyConnection, prev: Path, fresh: Path, hashes: Path, out: Path) -> None:
    """All records' standardised rows: the previous run's for unchanged records, freshly standardised ones for the rest."""
    cols = ", ".join(f.name for f in STD_SCHEMA)
    con.execute(f"""COPY (
        SELECT {cols} FROM {crypto.rp(prev / 'standardised.parquet')} o
          SEMI JOIN (SELECT source_id, source_record_id FROM {crypto.rp(hashes)} ANTI JOIN changed_keys USING (source_id, source_record_id)) u
          USING (source_id, source_record_id)
        UNION ALL SELECT {cols} FROM {crypto.rp(fresh)}
        ORDER BY source_id, source_record_id) TO '{out}' ({crypto.fmt()})""")


def reusable_pairs(con: duckdb.DuckDBPyConnection, prev: Path, index: Path) -> int:
    """Creates table `reused` (the previous run's scored rows, renumbered to this run, for candidate pairs whose two records are both
    unchanged) and table `todo` (the candidate pairs still to score). Needs tables `candidates` and `changed_keys`."""
    con.execute(f"CREATE OR REPLACE TABLE idx_new AS SELECT * FROM {crypto.rp(index)}")
    con.execute(f"CREATE OR REPLACE TABLE idx_old AS SELECT * FROM {crypto.rp(prev / 'candidates.index.parquet')}")
    con.execute("""CREATE OR REPLACE TABLE rid_map AS SELECT o.rid AS old_rid, n.rid AS new_rid FROM idx_old o
                   JOIN idx_new n USING (source_id, source_record_id) ANTI JOIN changed_keys c USING (source_id, source_record_id)""")
    score_cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {crypto.rp(prev / 'scored_pairs.parquet')}").fetchall()
                  if r[0] not in ("l_rid", "r_rid", "rules")]
    keep = ", ".join(f"s.{c}" for c in score_cols)
    con.execute(f"""CREATE OR REPLACE TABLE reused AS
        SELECT c.l_rid, c.r_rid, c.rules, {keep}
        FROM {crypto.rp(prev / 'scored_pairs.parquet')} s
        JOIN rid_map a ON a.old_rid = s.l_rid JOIN rid_map b ON b.old_rid = s.r_rid
        JOIN candidates c ON c.l_rid = least(a.new_rid, b.new_rid) AND c.r_rid = greatest(a.new_rid, b.new_rid)""")
    con.execute("CREATE OR REPLACE TABLE todo AS SELECT * FROM candidates ANTI JOIN reused USING (l_rid, r_rid)")
    for t in ("idx_new", "idx_old", "rid_map"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    return con.execute("SELECT count(*) FROM reused").fetchone()[0]


def combine_scores(con: duckdb.DuckDBPyConnection, fresh: Path, out: Path) -> None:
    cols = ", ".join(r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {crypto.rp(fresh)}").fetchall())
    con.execute(f"""COPY (SELECT {cols} FROM reused UNION ALL SELECT {cols} FROM {crypto.rp(fresh)} ORDER BY l_rid, r_rid)
                    TO '{out}' ({crypto.fmt()})""")
