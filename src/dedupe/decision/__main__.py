"""
    python -m dedupe.decision --config configs/decision_default.json --data data/synthetic
"""
import argparse
import json
from pathlib import Path

import duckdb

from .apply import decide
from .config import DecisionConfig
from .report import impact, render


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=Path("configs/decision_default.json"))
    ap.add_argument("--std", type=Path, default=Path("data/staging/standardised.parquet"))
    ap.add_argument("--cand", type=Path, default=Path("data/staging/candidates.parquet"))
    ap.add_argument("--scored", type=Path, default=Path("data/staging/scored_pairs.parquet"))
    ap.add_argument("--out", type=Path, default=Path("data/staging/decisions.parquet"))
    ap.add_argument("--data", type=Path, default=Path("data/synthetic"), help="ground truth for the impact report (dev only)")
    a = ap.parse_args()
    cfg = DecisionConfig.load(a.config)
    con = duckdb.connect()
    index = a.cand.with_suffix(".index.parquet")
    con.execute(f"""CREATE TABLE records AS SELECT i.rid, s.* FROM read_parquet('{a.std}') s
                    JOIN read_parquet('{index}') i USING (source_id, source_record_id)""")
    summary = decide(con, a.scored, cfg, a.out)
    rep = impact(con, a.data, cfg)
    a.out.with_suffix(".report.json").write_text(json.dumps({"summary": summary, **rep}, indent=2), encoding="utf-8")
    print(render(rep))


if __name__ == "__main__":
    main()
