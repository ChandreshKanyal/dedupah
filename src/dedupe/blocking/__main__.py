"""
    python -m dedupe.blocking --data data/synthetic [--cap 50]
"""
import argparse
import json
from pathlib import Path

import duckdb

from .generate import generate
from .report import blocking_report, render


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--std", type=Path, default=Path("data/staging/standardised.parquet"))
    ap.add_argument("--out", type=Path, default=Path("data/staging/candidates.parquet"))
    ap.add_argument("--data", type=Path, default=Path("data/synthetic"), help="for ground-truth evaluation (dev only)")
    ap.add_argument("--cap", type=int, default=50)
    a = ap.parse_args()
    con = duckdb.connect()
    gen = generate(a.std, a.out, cap=a.cap, con=con)
    rep = blocking_report(con, a.data, gen)
    a.out.with_suffix(".report.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print(render(rep))


if __name__ == "__main__":
    main()
