"""
    python -m dedupe.scoring --data data/synthetic     (train -> score -> evaluate against ground truth)
"""
import argparse
import json
from pathlib import Path

import duckdb

from .evaluate import evaluate, render
from .predict import score
from .train import train


def load_tables(con, std: Path, cand: Path) -> None:
    index = cand.with_suffix(".index.parquet")
    con.execute(f"""CREATE OR REPLACE TABLE records AS
        SELECT i.rid, s.* FROM read_parquet('{std}') s JOIN read_parquet('{index}') i USING (source_id, source_record_id)""")
    con.execute(f"CREATE OR REPLACE TABLE candidates AS SELECT l_rid, r_rid, rules FROM read_parquet('{cand}')")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--std", type=Path, default=Path("data/staging/standardised.parquet"))
    ap.add_argument("--cand", type=Path, default=Path("data/staging/candidates.parquet"))
    ap.add_argument("--out", type=Path, default=Path("data/staging/scored_pairs.parquet"))
    ap.add_argument("--model", type=Path, default=Path("data/models/fs_model.json"))
    ap.add_argument("--data", type=Path, default=Path("data/synthetic"), help="ground truth for evaluation (dev only)")
    ap.add_argument("--threshold", type=float, default=0.9)
    a = ap.parse_args()
    con = duckdb.connect()
    load_tables(con, a.std, a.cand)
    model = train(con)
    model.save(a.model)
    info = score(con, model, a.out)
    rep = evaluate(con, a.out, a.data, a.threshold)
    a.out.with_suffix(".report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(f"bias {info['prior_bits']:.2f} bits | seeds {model.meta['seed_pairs']} | model: {a.model}")
    print(render(rep))


if __name__ == "__main__":
    main()
