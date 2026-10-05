"""Turn pairs into per-comparison levels (+ term frequencies) with DuckDB, return numpy arrays.
Pairs are processed in chunks so memory stays flat as the number of pairs grows."""
import math

import duckdb
import numpy as np

from .comparisons import COMPARISONS, level_case_sql, pairs_view_sql, tf_key_case_sql

CHUNK_ROWS = 400_000


def prepare_tf_tables(con: duckdb.DuckDBPyConnection) -> None:
    for c in COMPARISONS:
        if c.tf_table_sql:
            con.execute(f"CREATE OR REPLACE TABLE tf_{c.name} AS {c.tf_table_sql}")


def _has_rules(con, table: str) -> bool:
    return "rules" in [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]


def _piece(con, src: str, out: str, with_tf: bool) -> None:
    """Levels (+TF) for one chunk of pairs held in table `src`; result in table `out`."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE _wide AS {pairs_view_sql(src)}")
    lv = ", ".join(f"({level_case_sql(c)})::TINYINT AS lv_{c.name}" for c in COMPARISONS)
    keep = "l_rid, r_rid" + (", rules" if _has_rules(con, src) else "")
    if not with_tf:
        con.execute(f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT {keep}, {lv} FROM _wide")
        return
    tfk = ", ".join(f"{tf_key_case_sql(c)} AS tfk_{c.name}" for c in COMPARISONS if c.tf_table_sql)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _lv AS SELECT {keep}, {lv}, {tfk} FROM _wide")
    joins = " ".join(f"LEFT JOIN tf_{c.name} t_{c.name} ON t_{c.name}.key = f.tfk_{c.name}" for c in COMPARISONS if c.tf_table_sql)
    tfsel = ", ".join(f"t_{c.name}.tf AS tf_{c.name}" for c in COMPARISONS if c.tf_table_sql)
    lvsel = ", ".join(f"f.lv_{c.name}" for c in COMPARISONS)
    con.execute(f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT {', '.join('f.' + k for k in keep.split(', '))}, {lvsel}, {tfsel} FROM _lv f {joins}")


def materialize(con: duckdb.DuckDBPyConnection, src_pairs: str, out: str, with_tf: bool, chunk: int = CHUNK_ROWS) -> None:
    """src_pairs has l_rid, r_rid (and optionally rules). Creates table `out` with lv_<comp> (and tf_<comp>)."""
    n = con.execute(f"SELECT count(*) FROM {src_pairs}").fetchone()[0]
    con.execute(f"CREATE OR REPLACE TEMP TABLE _src AS SELECT *, (row_number() OVER () - 1) // {chunk} AS _chunk FROM {src_pairs}")
    con.execute(f"DROP TABLE IF EXISTS {out}")
    first = True
    for i in range(max(1, math.ceil(n / chunk))):
        con.execute(f"CREATE OR REPLACE TEMP TABLE _chunk_src AS SELECT * EXCLUDE (_chunk) FROM _src WHERE _chunk = {i}")
        _piece(con, "_chunk_src", "_chunk_out", with_tf)
        con.execute(f"CREATE TABLE {out} AS SELECT * FROM _chunk_out" if first else f"INSERT INTO {out} SELECT * FROM _chunk_out")
        first = False
    for t in ("_src", "_chunk_src", "_chunk_out", "_wide", "_lv"):
        con.execute(f"DROP TABLE IF EXISTS {t}")


def level_counts(con: duckdb.DuckDBPyConnection, src_pairs: str, chunk: int = CHUNK_ROWS) -> dict:
    """{comparison: counts per level (-1 -> last slot)} without keeping the pair levels."""
    n = con.execute(f"SELECT count(*) FROM {src_pairs}").fetchone()[0]
    con.execute(f"CREATE OR REPLACE TEMP TABLE _src AS SELECT *, (row_number() OVER () - 1) // {chunk} AS _chunk FROM {src_pairs}")
    counts = {c.name: np.zeros(c.n_levels + 1) for c in COMPARISONS}
    for i in range(max(1, math.ceil(n / chunk))):
        con.execute(f"CREATE OR REPLACE TEMP TABLE _chunk_src AS SELECT * EXCLUDE (_chunk) FROM _src WHERE _chunk = {i}")
        _piece(con, "_chunk_src", "_chunk_out", with_tf=False)
        for c in COMPARISONS:
            for lv, k in con.execute(f"SELECT lv_{c.name}, count(*) FROM _chunk_out GROUP BY 1").fetchall():
                counts[c.name][c.n_levels if lv < 0 else lv] += k
    for t in ("_src", "_chunk_src", "_chunk_out", "_wide"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    return counts


def level_matrix(con, table: str) -> np.ndarray:
    t = con.execute(f"SELECT {', '.join('lv_' + c.name for c in COMPARISONS)} FROM {table}").to_arrow_table()
    return np.column_stack([t.column(i).to_numpy(zero_copy_only=False).astype(np.int16) for i in range(t.num_columns)]) \
        if t.num_rows else np.zeros((0, len(COMPARISONS)), dtype=np.int16)


def tf_matrix(con, table: str) -> np.ndarray:
    """Term frequency per pair/comparison; NaN where the level has no TF adjustment."""
    cols = []
    names = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    for c in COMPARISONS:
        if f"tf_{c.name}" in names:
            a = con.execute(f"SELECT tf_{c.name} FROM {table}").to_arrow_table().column(0).to_numpy(zero_copy_only=False)
            cols.append(a.astype(np.float64))
        else:
            cols.append(np.full(n, np.nan))
    return np.column_stack(cols) if n else np.zeros((0, len(COMPARISONS)))
