"""Canonical Parquet -> standardised match-feature Parquet.

    python -m dedupe.standardise --in data/staging/canonical.parquet --out data/staging/standardised.parquet

Output holds cleaned/derived features keyed by (source_id, source_record_id). Raw Aadhaar never leaves this stage:
only a keyed token and the last four digits do. Set DEDUPE_TOKEN_KEY in production.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import dates
from . import identifiers as ids
from .addresses import parse_address
from .names import parse_name

S, B, I = pa.string(), pa.bool_(), pa.int16()
SCHEMA = pa.schema([
    ("source_id", S), ("source_record_id", S), ("party_role", S), ("relation_to_proposer", S),
    ("name_clean", S), ("name_first", S), ("name_middle", S), ("name_last", S), ("name_first_is_initial", B),
    ("name_first_phon", S), ("name_last_phon", S), ("name_tokens_phon", S),
    ("father_name_clean", S), ("related_name_clean", S), ("father_name_phon", S), ("related_name_phon", S),
    ("dob", S), ("dob_year", I), ("dob_month", I), ("dob_day", I), ("dob_precision", S), ("dob_placeholder", B),
    ("birth_year_min", I), ("birth_year_max", I),
    ("gender", S), ("mobile", S), ("mobile_alt", S),
    ("email", S), ("email_key", S), ("email_domain", S),
    ("pan", S), ("pan_repaired", B), ("pan_surname_match", B),
    ("aadhaar_token", S), ("aadhaar_last4", S),
    ("passport_no", S), ("dl_no", S), ("voter_id", S),
    ("address_norm", S), ("address_pin", S), ("address_house", S), ("address_alt_norm", S), ("city", S), ("state", S),
])
_GENDER = {"m": "M", "male": "M", "man": "M", "f": "F", "female": "F", "woman": "F", "o": "O", "other": "O"}
_RAW_FIELDS = ["full_name", "father_name", "dob", "gender", "mobile", "mobile_alt", "email", "address", "address_alt",
               "city", "state", "pincode", "pan", "aadhaar", "aadhaar_last4", "aadhaar_reference_key", "passport_no", "dl_no", "voter_id",
               "related_name", "party_role", "relation_to_proposer", "age", "record_date", "source_id", "source_record_id"]


# data-quality report: of the values a source supplied, which survived validation?
_VALIDATION = {
    **{f: (lambda raw, row, out=out: row[out] is not None) for f, out in (
        ("mobile", "mobile"), ("email", "email"), ("pan", "pan"), ("dob", "dob"), ("aadhaar", "aadhaar_token"), ("pincode", "address_pin"))},
    "age": lambda raw, row: dates.parse_age(raw["age"]) is not None,
    # an unreadable record date silently turns an age into a birth year counted from today: surface it
    "record_date": lambda raw, row: dates.parse_record_date(raw["record_date"]) is not None,
}


def standardise_row(r: dict) -> dict:
    name = parse_name(r["full_name"])
    father, related = parse_name(r["father_name"]), parse_name(r["related_name"])
    d = dates.parse_dob(r["dob"])
    by_min, by_max = dates.birth_year_range(d[1], r.get("age"), r.get("record_date"))
    em, em_key, em_dom = ids.email(r["email"])
    pan, pan_fixed = ids.pan(r["pan"])
    tok, last4 = ids.aadhaar(r["aadhaar"])
    if not tok:                       # the customer's vault reference key: same strength as the keyed token, separate namespace
        tok = ids.aadhaar_reference(r.get("aadhaar_reference_key"))
    if not last4:
        last4 = (r["aadhaar_last4"] or "").strip()
        last4 = last4 if len(last4) == 4 and last4.isdigit() else None
    addr = parse_address(r["address"], r["city"], r["state"], r["pincode"])
    alt = parse_address(r["address_alt"])
    return {
        "source_id": r["source_id"], "source_record_id": r["source_record_id"],
        "party_role": (r["party_role"] or "").strip().lower() or None,
        "relation_to_proposer": (r["relation_to_proposer"] or "").strip().lower() or None,
        "name_clean": name.clean, "name_first": name.first, "name_middle": name.middle, "name_last": name.last,
        "name_first_is_initial": name.first_is_initial if name.first else None,
        "name_first_phon": name.first_phon, "name_last_phon": name.last_phon, "name_tokens_phon": name.tokens_phon,
        "father_name_clean": father.clean, "related_name_clean": related.clean,
        "father_name_phon": father.tokens_phon, "related_name_phon": related.tokens_phon,
        "dob": d[0], "dob_year": d[1], "dob_month": d[2], "dob_day": d[3], "dob_precision": d[4], "dob_placeholder": d[5] if d[4] else None,
        "birth_year_min": by_min, "birth_year_max": by_max,
        "gender": _GENDER.get((r["gender"] or "").strip().lower()),
        "mobile": ids.phone(r["mobile"]), "mobile_alt": ids.phone(r["mobile_alt"]),
        "email": em, "email_key": em_key, "email_domain": em_dom,
        "pan": pan, "pan_repaired": pan_fixed if pan else None,
        # PAN's 5th character is the initial of the holder's surname *at issue time*: a soft signal only
        "pan_surname_match": (pan[4].lower() == name.last[0]) if pan and name.last else None,
        "aadhaar_token": tok, "aadhaar_last4": last4,
        "passport_no": ids.passport(r["passport_no"]), "dl_no": ids.driving_licence(r["dl_no"]), "voter_id": ids.voter_id(r["voter_id"]),
        "address_norm": addr.norm, "address_pin": addr.pin, "address_house": addr.house,
        "address_alt_norm": alt.norm, "city": addr.city, "state": addr.state,
    }


def run(in_path: Path, out_path: Path, batch_size: int = 50_000) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pf = pq.ParquetFile(in_path)
    seen, filled = Counter(), defaultdict(Counter)
    raw_present = defaultdict(Counter)
    with pq.ParquetWriter(out_path, SCHEMA) as w:
        for batch in pf.iter_batches(batch_size=batch_size):
            # a canonical file without some fields (older files, other producers) is treated as having them all empty
            cols = {c: batch.column(c).to_pylist() if c in batch.schema.names else [None] * batch.num_rows for c in _RAW_FIELDS}
            rows = []
            for i in range(batch.num_rows):
                raw = {c: cols[c][i] for c in _RAW_FIELDS}
                row = standardise_row(raw)
                rows.append(row)
                src = row["source_id"]
                seen[src] += 1
                for raw_f, valid in _VALIDATION.items():
                    if raw[raw_f] and str(raw[raw_f]).strip():
                        raw_present[src][raw_f] += 1
                        if valid(raw, row):
                            filled[src][raw_f] += 1
            w.write_table(pa.Table.from_pylist(rows, schema=SCHEMA))
    # data-quality report: of the values a source supplied, what share survived validation?
    quality = {src: {f: f"{filled[src][f]}/{n} valid" for f, n in raw_present[src].items()} for src in seen}
    return {"records": dict(seen), "validation": quality, "output": str(out_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", type=Path, default=Path("data/staging/canonical.parquet"))
    ap.add_argument("--out", type=Path, default=Path("data/staging/standardised.parquet"))
    a = ap.parse_args()
    print(json.dumps(run(a.inp, a.out), indent=2))


if __name__ == "__main__":
    main()
