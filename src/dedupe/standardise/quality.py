"""Does standardisation help? For each field: among record pairs of the SAME person that both have the field,
how often is the value equal (agreement), and how many pairs of DIFFERENT people share a value (collisions)?
Uses ground truth, so synthetic/dev data only.

    python -m dedupe.standardise.quality --data data/synthetic
"""
import argparse
from pathlib import Path

import duckdb

# (label, raw expression on canonical `c`, standardised expression on `s`)
FIELDS = [
    ("mobile", "trim(c.mobile)", "s.mobile"),
    ("email", "lower(trim(c.email))", "s.email_key"),
    ("pan", "upper(trim(c.pan))", "s.pan"),
    ("aadhaar", "replace(c.aadhaar, ' ', '')", "s.aadhaar_token"),
    ("dob", "trim(c.dob)", "s.dob"),
    ("pincode", "trim(c.pincode)", "s.address_pin"),
    ("city", "lower(trim(c.city))", "s.city"),
    ("name (whole)", "lower(trim(c.full_name))", "s.name_tokens_phon"),
    ("surname", "lower(trim(split_part(c.full_name, ' ', -1)))", "s.name_last_phon"),
]


def _pairs_query(expr_col: str) -> str:
    return """
    SELECT
      (SELECT coalesce(sum(n*(n-1)/2), 0) FROM (SELECT person_id, v, count(*) n FROM t WHERE v IS NOT NULL GROUP BY 1, 2)),
      (SELECT coalesce(sum(k*(k-1)/2), 0) FROM (SELECT person_id, count(*) k FROM t WHERE v IS NOT NULL GROUP BY 1)),
      (SELECT coalesce(sum(n*(n-1)/2), 0) FROM (SELECT v, count(*) n FROM t WHERE v IS NOT NULL GROUP BY 1))
    """


def field_report(canonical: Path, standardised: Path, data_dir: Path) -> list[dict]:
    con = duckdb.connect()
    con.execute(f"CREATE VIEW canon AS SELECT * FROM read_parquet('{canonical}')")
    con.execute(f"CREATE VIEW std AS SELECT * FROM read_parquet('{standardised}')")
    con.execute(f"CREATE VIEW truth AS SELECT * FROM read_csv_auto('{data_dir / 'ground_truth' / 'record_truth.csv'}', all_varchar=true) "
                "WHERE role <> 'Co-applicant'")
    out = []
    for label, raw, std in FIELDS:
        res = {}
        for kind, expr in (("raw", raw), ("std", std)):
            con.execute(f"""CREATE OR REPLACE TEMP VIEW t AS
                SELECT {expr} AS v, r.person_id FROM canon c
                JOIN std s ON s.source_id = c.source_id AND s.source_record_id = c.source_record_id
                JOIN truth r ON r.source = c.source_id AND r.source_record_id = c.source_record_id""")
            agree, same_person, same_value = con.execute(_pairs_query("v")).fetchone()
            res[kind] = {"agree": agree / same_person if same_person else None, "collisions": int(same_value - agree)}
        out.append({"field": label, **{f"{k}_{m}": v for k, d in res.items() for m, v in d.items()}})
    return out


def render(rows: list[dict]) -> str:
    def a(x):
        return "  n/a " if x is None else f"{x:6.1%}"

    lines = ["field            same-person agreement (raw -> cleaned)   cross-person collisions (raw -> cleaned)"]
    for r in rows:
        lines.append(f"{r['field']:<16} {a(r['raw_agree'])} -> {a(r['std_agree'])}"
                     f"{'':<18} {r['raw_collisions']:>8,} -> {r['std_collisions']:>8,}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/synthetic"))
    ap.add_argument("--canonical", type=Path, default=Path("data/staging/canonical.parquet"))
    ap.add_argument("--standardised", type=Path, default=Path("data/staging/standardised.parquet"))
    a = ap.parse_args()
    print(render(field_report(a.canonical, a.standardised, a.data)))


if __name__ == "__main__":
    main()
