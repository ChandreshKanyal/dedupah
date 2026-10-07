"""Read every configured source, map it onto the canonical schema, write one Parquet file.

    python -m dedupe.ingest --config configs/synthetic_sources.json --out data/staging/canonical.parquet
"""
import argparse
import json
from pathlib import Path

import pyarrow as pa

from dedupe import crypto

from .connectors import create
from .mapping import OUTPUT_COLUMNS, IngestConfig, compile_select

SCHEMA = pa.schema([(c, pa.string()) for c in OUTPUT_COLUMNS])


def ingest(config_path: Path, out_path: Path, batch_size: int = 50_000) -> dict:
    cfg = IngestConfig.load(config_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate every source and mapping before reading any data (fail fast, fail clearly).
    plans = []
    for src in cfg.sources:
        conn = create(src.connector)
        conn.test_connection()
        compile_select(src, conn.columns())
        plans.append((src, conn))

    stats: dict = {}
    con = crypto.connect()
    with crypto.open_writer(out_path, SCHEMA) as writer:
        for src, conn in plans:
            offset = 0
            for batch in conn.read_batches(batch_size):
                con.register("batch", batch)
                res = con.execute(compile_select(src, batch.column_names, row_offset=offset)).to_arrow_table()
                con.unregister("batch")
                writer.write_table(res.cast(SCHEMA))
                offset += batch.num_rows
            stats[src.source_id] = offset

    dupes = con.execute(
        f"SELECT source_id, count(*) - count(DISTINCT source_record_id) AS d FROM {crypto.rp_param()} "
        "GROUP BY 1 HAVING d > 0", [str(out_path)]).fetchall()
    if dupes:
        # A record id used twice in one source (a re-export, a bad key column) would make every later join ambiguous. The first row
        # keeps the id, later ones become '<id>#2', '#3'... in file order; the count is reported so the source can be fixed. A new id
        # could itself already exist ('INV#1001' twice next to a real 'INV#1001#2'): the result is checked, and on a clash the suffix
        # changes ('#dup1-2', '#dup2-2', ...) until every id is unique.
        tmp = out_path.with_name(out_path.stem + ".dedup.parquet")
        for attempt in range(10):
            sep = "#" if attempt == 0 else f"#dup{attempt}-"
            con.execute(f"""COPY (SELECT * EXCLUDE (n, file_row_number) REPLACE (CASE WHEN n > 1 THEN source_record_id || '{sep}' || n
                                                                             ELSE source_record_id END AS source_record_id)
                FROM (SELECT *, row_number() OVER (PARTITION BY source_id, source_record_id ORDER BY file_row_number) AS n
                      FROM {crypto.rp(out_path, row_numbers=True)}) ORDER BY file_row_number) TO '{tmp}' ({crypto.fmt()})""")
            clash = con.execute(f"SELECT count(*) - count(DISTINCT (source_id, source_record_id)) FROM {crypto.rp_param()}", [str(tmp)]).fetchone()[0]
            if not clash:
                break
        else:
            tmp.unlink(missing_ok=True)
            raise ValueError("record ids repeat inside a source and could not be made unique: check the record id column")
        tmp.replace(out_path)
    return {"records": stats, "total": sum(stats.values()), "duplicate_source_record_ids": dict(dupes),
            "output": str(out_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("data/staging/canonical.parquet"))
    ap.add_argument("--batch-size", type=int, default=50_000)
    a = ap.parse_args()
    print(json.dumps(ingest(a.config, a.out, a.batch_size), indent=2))


if __name__ == "__main__":
    main()
