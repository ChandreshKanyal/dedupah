"""Generate a synthetic multi-source customer dataset with ground truth.

    python -m dedupe.synthetic --households 1200 --seed 42 --out data/synthetic
"""
import argparse
import csv
import json
import random
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from .households import Person, PopulationBuilder
from .sources import Sources

REF_DATE = date(2026, 10, 1)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_xlsx(path: Path, rows: list[dict], sheet: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    fields = list(rows[0])
    ws.append(fields)
    for r in rows:
        ws.append([r.get(k) for k in fields])
    wb.save(path)


def person_truth_row(p: Person, sources_seen: set[str]) -> dict:
    d = asdict(p)
    addr = d.pop("addr")
    prev = d.pop("prev_addr")
    d.update({f"addr_{k}": v for k, v in addr.items() if k not in ("aliases", "sc")})
    d["prev_addr_city"] = prev["city"] if prev else None
    d["prev_addr_locality"] = prev["locality"] if prev else None
    d["dob"] = p.dob.isoformat()
    d["traps"] = "|".join(p.traps)
    d["sources"] = "|".join(sorted(sources_seen))
    return d


def generate(n_households: int, seed: int, out: Path, noise: float = 1.0) -> dict:
    rng = random.Random(seed)
    people = PopulationBuilder(rng, REF_DATE).build(n_households)
    src = Sources(rng, people, REF_DATE, noise)
    tables = {"bank_crm": src.bank_crm(), "insurance": src.insurance(), "loans": src.loans(), "leads": src.leads()}
    tables["kyc_documents"] = src.kyc_documents()  # needs the others to know who has a bank/loan relationship

    out.mkdir(parents=True, exist_ok=True)
    gt = out / "ground_truth"
    gt.mkdir(exist_ok=True)
    write_csv(out / "bank_crm.csv", tables["bank_crm"])
    write_xlsx(out / "insurance_policies.xlsx", tables["insurance"], "policies")
    write_csv(out / "loans.csv", tables["loans"])
    write_csv(out / "marketing_leads.csv", tables["leads"])
    write_csv(out / "kyc_documents.csv", tables["kyc_documents"])
    write_csv(gt / "persons_truth.csv", [person_truth_row(p, src.seen_in[p.person_id]) for p in people])
    write_csv(gt / "record_truth.csv", src.truth)

    manifest = {
        "seed": seed, "households": n_households, "noise": noise,
        "persons": len(people),
        "persons_with_any_record": sum(1 for p in people if src.seen_in[p.person_id]),
        "records": {k: len(v) for k, v in tables.items()},
        "trap_counts": dict(Counter(t for p in people for t in p.traps)),
        "note": "SYNTHETIC data. Accuracy measured on it does not prove real-world accuracy.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--households", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--noise", type=float, default=1.0, help="corruption multiplier (0 = clean, 2 = very messy)")
    ap.add_argument("--out", type=Path, default=Path("data/synthetic"))
    a = ap.parse_args()
    print(json.dumps(generate(a.households, a.seed, a.out, a.noise), indent=2))


if __name__ == "__main__":
    main()
