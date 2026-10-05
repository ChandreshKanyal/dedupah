"""
    python -m dedupe.evaluation baseline --data data/synthetic
    python -m dedupe.evaluation score --data data/synthetic --pred data/runs/my_run.csv
"""
import argparse
import json
from pathlib import Path

from . import baseline
from .loaders import load_predictions, load_truth, write_predictions
from .metrics import evaluate, render


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baseline", "score"])
    ap.add_argument("--data", type=Path, default=Path("data/synthetic"))
    ap.add_argument("--pred", type=Path, help="predictions CSV (score only)")
    ap.add_argument("--runs", type=Path, default=Path("data/runs"))
    a = ap.parse_args()

    rec_person, rec_hh, traps = load_truth(a.data)
    if a.command == "baseline":
        pred = baseline.run(a.data)
        name = "baseline_exact_pan_mobile"
        write_predictions(a.runs / f"{name}.csv", pred)
    else:
        pred = load_predictions(a.pred)
        name = a.pred.stem
    report = evaluate(pred, rec_person, rec_hh, traps)
    a.runs.mkdir(parents=True, exist_ok=True)
    (a.runs / f"{name}.report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(render(report, name))


if __name__ == "__main__":
    main()
