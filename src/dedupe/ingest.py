"""Read every configured source, map it onto the canonical schema, write one Parquet file.

    python -m dedupe.ingest --config configs/synthetic_sources.json --out data/staging/canonical.parquet
"""
import argparse
import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

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
    con = duckdb.connect()
    with pq.ParquetWriter(out_path, SCHEMA) as writer:
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
        "SELECT source_id, count(*) - count(DISTINCT source_record_id) AS d FROM read_parquet(?) "
        "GROUP BY 1 HAVING d > 0", [str(out_path)]).fetchall()
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
