"""python -m dedupe.survivorship --work data/staging   (needs the pipeline outputs: standardised, canonical, clusters)"""
import argparse
from pathlib import Path

import duckdb

from . import SurvivorshipConfig, build_golden


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, default=Path("data/staging"))
    ap.add_argument("--config", type=Path, default=Path("configs/survivorship_default.json"))
    a = ap.parse_args()
    info = build_golden(duckdb.connect(), a.work / "standardised.parquet", a.work / "canonical.parquet",
                        a.work / "clusters" / "clusters.parquet", SurvivorshipConfig.load(a.config), a.work / "golden")
    print(info)


if __name__ == "__main__":
    main()
