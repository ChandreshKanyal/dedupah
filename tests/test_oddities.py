"""Data in awkward shapes: several values in one cell, placeholders, companies, joint names, odd dates, repeated headers, duplicate ids."""
import csv
import json

import duckdb
import pytest

from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.standardise import oddities as o
from dedupe.standardise.run import standardise_row


@pytest.mark.parametrize("raw, want, flag", [
    ("9812345670 / 9123456780", ["9812345670", "9123456780"], "multi_phone"),
    ("9812345670 or 9123456780", ["9812345670", "9123456780"], "multi_phone"),
    ("+91-98123 45670", ["9812345670"], None),
    ("9812345670.0", ["9812345670"], None),                 # a number column read as decimals
    ("9.81234567E9", ["9812345670"], None),                 # scientific notation with all the digits
    ("9.81235E+09", [], "phone_digits_lost"),               # rounded by a spreadsheet: never guess the missing digits
])
def test_phones_in_any_shape(raw, want, flag):
    got, flags = o.phones(raw)
    assert got == want and (flag in flags if flag else not flags)


def test_placeholders_companies_joint_names_and_relations():
    assert o.email("test@test.com") == (None, {"placeholder_email"})
    assert o.email("na@na.com, real@gmail.com")[0] == "real@gmail.com"
    assert o.email("asha.rao@example. com") == ("asha.rao@example.com", set())      # a stray space is repaired; example.com alone is no placeholder
    assert o.name("Unknown")[2] == {"placeholder_name"} and o.name("NA")[0] is None
    assert o.name("Sharma Traders Pvt Ltd")[2] == {"organisation"} and o.name("Anand Kumar")[2] == set()
    assert o.name("Rahul & Anita Sharma")[:2] == ("Rahul Sharma", None)
    assert o.name("Rahul S/O Ramesh Kumar")[:2] == ("Rahul", "Ramesh Kumar")
    assert o.name("Customer Name")[2] == {"header_row"}


@pytest.mark.parametrize("raw, iso", [("17-May-90", "1990-05-17"), ("1990-05-17 00:00:00", "1990-05-17"), ("May 17, 1990", "1990-05-17"),
                                      ("33010", "1990-05-17"), ("17/05/1990", "1990-05-17")])
def test_dates_in_any_shape(raw, iso):
    r = standardise_row({**dict.fromkeys(["full_name", "father_name", "gender", "mobile", "mobile_alt", "email", "address", "address_alt", "city", "state",
                                          "pincode", "pan", "aadhaar", "aadhaar_last4", "aadhaar_reference_key", "passport_no", "dl_no", "voter_id",
                                          "related_name", "party_role", "relation_to_proposer", "age", "record_date"]),
                         "source_id": "s", "source_record_id": "1", "dob": raw})
    assert r["dob"] == iso


def test_a_messy_file_runs_and_the_oddities_are_counted_and_handled(tmp_path):
    f = tmp_path / "messy.csv"
    rows = [
        ("1", "Rahul Sharma", "9812345670 / 9123456780", "rahul@gmail.com", "17-May-90"),
        ("2", "Rahul Sharma", "9123456780", "rahul@gmail.com", "1990-05-17"),            # same person: matches through the second phone
        ("3", "Sharma Traders Pvt Ltd", "9812345670", "accounts@sharmatraders.in", ""),  # a company sharing the number: not a person
        ("4", "Unknown", "9800000011", "test@test.com", ""),
        ("5", "Name", "Mobile", "Email", "DOB"),                                          # header repeated inside the data
        ("5", "Anita Rao", "9811122233", "anita@x.in", "02/03/1985"),                      # duplicate record id
        ("7", "Anita Rao", "9811122233", "anita@x.in", "02/03/1985"),
        ("8", "Rahul S/O Ramesh", "9812345670.0", "", ""),
    ]
    with f.open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["id", "name", "phone", "mail", "dob"])
        w.writerows(rows)
    cfg = {"sources": [{"source_id": "messy", "connector": {"type": "csv", "path": str(f)}, "record_id": "id",
                        "fields": {"full_name": "name", "mobile": "phone", "email": "mail", "dob": "dob"}}]}
    (tmp_path / "s.json").write_text(json.dumps(cfg), encoding="utf-8")
    s = run_pipeline(PipelineConfig(tmp_path / "s.json", tmp_path / "work"))
    odd = s["standardise"]["profile"]["messy"]["oddities"]
    assert odd["multi_phone"] == 1 and odd["organisation"] == 1 and odd["placeholder_name"] == 1 and odd["placeholder_email"] == 1
    assert odd["header_row"] == 1 and odd["relation_in_name"] == 1
    assert s["ingest"]["duplicate_source_record_ids"] == {"messy": 1} and s["ingest"]["records"]["messy"] == 8
    cl = dict(duckdb.sql(f"SELECT source_record_id, cluster_id FROM '{tmp_path / 'work' / 'clusters' / 'clusters.parquet'}'").fetchall())
    assert len(cl) == 8                                                       # nothing lost: the duplicate id was made unique ('5#2')
    assert "5#2" in cl
    assert cl["3"] not in (cl["1"], cl["2"], cl["8"])                          # the company never joins a person
    std = duckdb.sql(f"SELECT source_record_id, mobile, mobile_alt, name_clean, father_name_clean FROM '{tmp_path / 'work' / 'standardised.parquet'}'").fetchall()
    by = {r[0]: r[1:] for r in std}
    assert by["1"][:2] == ("9812345670", "9123456780") and by["8"][0] == "9812345670"
    assert by["8"][2:] == ("rahul", "ramesh") and by["4"][2] is None
