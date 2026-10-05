import csv
from pathlib import Path


def _read(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def record_key(source: str, record_id: str) -> str:
    return f"{source}:{record_id}"


def load_truth(data_dir: Path):
    """Returns (record->person, record->household, person->traps, person->household)."""
    gt = data_dir / "ground_truth"
    rec_person, rec_hh = {}, {}
    for r in _read(gt / "record_truth.csv"):
        if r["role"] == "Co-applicant":  # extra mention on a loan row, not a record of its own
            continue
        k = record_key(r["source"], r["source_record_id"])
        rec_person[k], rec_hh[k] = r["person_id"], r["household_id"]
    traps = {}
    for p in _read(gt / "persons_truth.csv"):
        traps[p["person_id"]] = set(filter(None, p["traps"].split("|")))
    return rec_person, rec_hh, traps


def load_predictions(path: Path) -> dict:
    """CSV with columns: source, source_record_id, cluster_id."""
    return {record_key(r["source"], r["source_record_id"]): r["cluster_id"] for r in _read(path)}


def write_predictions(path: Path, pred: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "source_record_id", "cluster_id"])
        for k, cid in pred.items():
            source, rid = k.split(":", 1)
            w.writerow([source, rid, cid])
