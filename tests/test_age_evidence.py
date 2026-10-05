"""Age (band) as evidence for records without a birth date (leads): standardisation, scoring levels, blocking and decisions."""
import importlib
from datetime import date, datetime

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_decision import RAW, _decide

from dedupe.blocking import generate
from dedupe.decision import DecisionConfig
from dedupe.decision.conditions import compile_condition
from dedupe.scoring.comparisons import DOB
from dedupe.scoring.levels import materialize
from dedupe.standardise.dates import birth_year_range, parse_age, parse_dob, parse_record_date, reference_date
from dedupe.standardise.run import SCHEMA, standardise_row

standardise_run = importlib.import_module("dedupe.standardise.run")   # the package re-exports a function named `run`
DEFAULT = DecisionConfig.load("configs/decision_default.json")
LABELS = {v: k for k, v in DOB.labels().items()}


def test_age_parsing_and_birth_year_range():
    assert parse_age("35") == (35, 35) and parse_age("50-59") == (50, 59) and parse_age(" 50 – 59 ") == (50, 59) and parse_age("60+") == (60, 85)
    for bad in (None, "", "abc", "0", "130", "59-50", "-5", "1999"):
        assert parse_age(bad) is None
    assert birth_year_range(1990, "30", "2025-01-01") == (1990, 1990)           # an exact birth year always wins
    assert birth_year_range(None, "35", "07/08/2024") == (1988, 1989)           # 2024 - 35 - 1 .. 2024 - 35
    assert birth_year_range(None, "50-59", "07/08/2024") == (1964, 1974)
    # no record date: counted back from today, with 3 extra years on the early side because the age may be stale
    assert birth_year_range(None, "35", None, today=date(2026, 10, 1)) == (1987, 1991)
    assert birth_year_range(None, None, "2024-01-01") == (None, None)


def test_real_world_age_and_record_date_formats():
    assert parse_age("35 yrs") == parse_age("35.0") == parse_age("35 years old") == parse_age(35) == (35, 35)
    assert parse_age("50 to 59") == (50, 59) and parse_age("90+") == (90, 110)        # open bands are capped, not dropped
    # timestamps, as databases and spreadsheet cells deliver them: the date part is used, not today's year
    for ts in ("2023-04-01 00:00:00", "2023-04-01T10:22:00", "2023-04-01T10:22:00Z", "01/04/2023 10:22", "2023-04-01 10:22:00.123+05:30",
               datetime(2023, 4, 1, 10, 22), date(2023, 4, 1)):
        assert parse_record_date(ts) == date(2023, 4, 1), ts
        assert birth_year_range(None, "35", ts) == (1987, 1988), ts
    assert parse_record_date("2023") == date(2023, 7, 1) and parse_record_date("garbage") is None


def test_reference_date_follows_the_clock_unless_pinned(monkeypatch):
    monkeypatch.delenv("DEDUPE_REFERENCE_DATE", raising=False)
    assert reference_date() == date.today()
    monkeypatch.setenv("DEDUPE_REFERENCE_DATE", "2030-06-01")
    assert reference_date() == date(2030, 6, 1)
    assert birth_year_range(None, "40", None) == (2030 - 40 - 1 - 3, 1990)                  # a 2030 run does not count back from 2026
    assert parse_dob("15/03/2029")[1] == 2029                                                 # and 2029 is no longer a future date
    twins = compile_condition({"field": "dob_year", "op": "both_born_within_last", "value": 18})
    assert ">= 2012" in twins


def test_synthetic_lead_ages_are_measured_on_the_lead_date(tmp_path):
    from dedupe.synthetic import generate as gen_data
    syn = tmp_path / "syn"
    gen_data(150, seed=7, out=syn)
    con = duckdb.connect()
    rows = con.execute(f"""SELECT l.age, l.created_at, p.dob FROM read_csv_auto('{syn}/marketing_leads.csv', all_varchar=true) l
        JOIN read_csv_auto('{syn}/ground_truth/record_truth.csv', all_varchar=true) t ON t.source = 'leads' AND t.source_record_id = l.lead_id
        JOIN read_csv_auto('{syn}/ground_truth/persons_truth.csv', all_varchar=true) p USING (person_id) WHERE l.age IS NOT NULL""").fetchall()
    assert len(rows) > 50
    ok = 0
    for age, created, dob in rows:
        lo, hi = birth_year_range(None, age, created)
        ok += lo - 2 <= int(dob[:4]) <= hi + 2            # the scoring tolerance; the generator adds at most one year of noise
    assert ok == len(rows)


def _row(**kw):
    base = {k: None for k in RAW} | {"age": None, "record_date": None, "source_id": "s", "source_record_id": "x"}
    return standardise_row(base | kw)


def test_standardised_row_carries_the_birth_year_range():
    lead = _row(full_name="Rina Mukherjee", age="50-59", record_date="26/08/2025", city="Kolkata")
    kyc = _row(full_name="Rina Mukherjee", dob="12/05/1970")
    assert (lead["birth_year_min"], lead["birth_year_max"], lead["dob_year"]) == (1965, 1975, None)
    assert (kyc["birth_year_min"], kyc["birth_year_max"], kyc["dob_year"]) == (1970, 1970, 1970)


def _levels(tmp_path, a, b):
    con = duckdb.connect()
    con.register("t", pa.Table.from_pylist([_row(**a, source_record_id="0"), _row(**b, source_record_id="1")], schema=SCHEMA))
    con.execute("CREATE TABLE records AS SELECT row_number() OVER (ORDER BY source_record_id) - 1 AS rid, * FROM t")
    con.execute("CREATE TABLE candidates AS SELECT 0 AS l_rid, 1 AS r_rid, ['x'] AS rules")
    materialize(con, "candidates", "lv", with_tf=False)
    return con.execute("SELECT lv_dob FROM lv").fetchone()[0]


def test_dob_ladder_has_age_levels_only_when_a_birth_year_is_missing(tmp_path):
    lead = {"full_name": "A B", "age": "50-59", "record_date": "2025-06-01"}                   # born 1965-1975
    assert _levels(tmp_path, lead, {"full_name": "A B", "dob": "01/03/1970"}) == LABELS["age-based: compatible"]
    assert _levels(tmp_path, lead, {"full_name": "A B", "dob": "01/03/1999"}) == LABELS["age-based: incompatible"]
    assert _levels(tmp_path, lead, {"full_name": "A B", "dob": "01/03/1977"}) == LABELS["age-based: compatible"]      # two years of slack
    assert _levels(tmp_path, lead, {"full_name": "A B", "dob": "01/03/1980"}) == LABELS["age-based: incompatible"]
    assert _levels(tmp_path, {"full_name": "A B", "age": "35", "record_date": "2025-06-01"}, lead) == LABELS["age-based: incompatible"]
    assert _levels(tmp_path, lead, {"full_name": "A B"}) == -1                                  # nothing to compare on one side
    assert _levels(tmp_path, {"full_name": "A B", "dob": "01/03/1970"}, {"full_name": "A B", "dob": "01/03/1970"}) == LABELS["exact date"]   # unchanged


def test_blocking_links_an_age_only_record_to_a_document_with_the_same_name_and_city(tmp_path):
    def rec(i, name_first, name_last, city, lo, hi, dated=True):
        return {"source_id": "s", "source_record_id": f"{i:04d}", "name_first_phon": name_first, "name_last_phon": name_last, "city": city,
                "birth_year_min": lo, "birth_year_max": hi, "dob_year": lo if dated else None}
    rows = [rec(0, "RN", "MKRJ", "kolkata", 1965, 1975, dated=False),  # lead: age band 50-59
            rec(1, "RN", "MKRJ", "kolkata", 1970, 1970),        # document: born 1970 -> compatible
            rec(2, "RN", "MKRJ", "kolkata", 1999, 1999),        # same name and city but born 1999 -> not a candidate
            rec(3, "RN", "MKRJ", "delhi", 1970, 1970),          # other city -> not a candidate
            rec(4, "RN", "MKRJ", "kolkata", None, None),        # no age at all -> no key
            rec(5, "RN", "MKRJ", "kolkata", 1972, 1972)]        # another document born 1972: compatible with the lead, but two
    std = tmp_path / "std.parquet"                               # dated documents are left to the DOB rules (namesakes)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), std)
    generate(std, tmp_path / "cand.parquet")
    pairs = {(r[0], r[1]): r[2] for r in duckdb.sql(f"select l_rid, r_rid, rules from '{tmp_path / 'cand.parquet'}'").fetchall()}
    assert pairs == {(0, 1): ["name_city_age"], (0, 5): ["name_city_age"]}


def test_age_compatible_name_and_city_is_worth_a_look_but_never_an_automatic_merge(tmp_path):
    lead = {"full_name": "Rina Mukherjee", "age": "50-59", "record_date": "2025-06-01", "city": "Kolkata"}
    doc = {"full_name": "Rina Mukherjee", "dob": "01/03/1970", "address": "Salt Lake", "city": "Kolkata"}
    other = {"full_name": "Rina Mukherjee", "dob": "01/03/1999", "address": "Salt Lake", "city": "Kolkata"}
    with_phone_l, with_phone_d = {**lead, "mobile": "9812345678"}, {**doc, "mobile": "9812345678"}
    got = _decide([(lead, doc, 0.85), (lead, doc, 0.95), (lead, other, 0.85), (with_phone_l, with_phone_d, 0.95), (lead, doc, 0.5)], DEFAULT, tmp_path)
    assert got[0] == ("review", "score")                                       # enough evidence to ask a person (score 0.85)
    assert got[4] == ("reject", "not_enough_evidence")                          # but below a score of 0.8 such pairs are almost never the same person
    assert got[1] == ("review", "review_age_only_evidence")                     # a high score alone may not merge it automatically
    assert got[2] == ("reject", "not_enough_evidence")                          # an incompatible age is not evidence
    assert got[3] == ("auto_merge", "score"), got[3]                                            # a shared phone is real corroboration


def test_age_only_review_never_overrides_the_minimum_evidence_reject(tmp_path):
    lead = {"full_name": "Rina Mukherjee", "age": "50-59", "record_date": "2025-06-01", "city": "Kolkata", "gender": "F"}
    doc_elsewhere = {"full_name": "Rina Mukherjee", "dob": "01/03/1970", "city": "Pune", "gender": "F"}
    doc_no_place = {"full_name": "Rina Mukherjee", "dob": "01/03/1970", "gender": "F"}
    got = _decide([(lead, doc_elsewhere, 0.95), (lead, doc_no_place, 0.95)], DEFAULT, tmp_path)
    assert got == [("reject", "not_enough_evidence")] * 2


def test_priority_offset_is_only_accepted_on_force_review_rules():
    when = {"comparison": "pan", "is": "same PAN"}
    DecisionConfig.model_validate({"rules": [{"id": "r", "type": "force_review", "when": when, "priority_offset": -0.3}]})
    with pytest.raises(ValueError, match="priority_offset"):
        DecisionConfig.model_validate({"rules": [{"id": "r", "type": "not_enough_evidence", "when": when, "priority_offset": -0.3}]})


def test_canonical_file_without_the_age_fields_still_standardises(tmp_path):
    cols = [c for c in standardise_run._RAW_FIELDS if c not in ("age", "record_date")]
    row = {c: None for c in cols} | {"source_id": "s", "source_record_id": "1", "full_name": "Rina Mukherjee", "dob": "01/03/1970"}
    pq.write_table(pa.Table.from_pylist([row], schema=pa.schema([(c, pa.string()) for c in cols])), tmp_path / "canonical.parquet")
    out = standardise_run.run(tmp_path / "canonical.parquet", tmp_path / "std.parquet")
    assert out["records"] == {"s": 1}
    assert pq.read_table(tmp_path / "std.parquet").column("birth_year_min").to_pylist() == [1970]


def test_quality_report_flags_unreadable_ages_and_record_dates(tmp_path):
    rows = [{c: None for c in standardise_run._RAW_FIELDS} | {"source_id": "leads", "source_record_id": str(i), "full_name": "A B",
                                                               "age": age, "record_date": rd}
            for i, (age, rd) in enumerate([("35", "2024-01-01 10:00:00"), ("thirty", "yesterday"), ("40", None)])]
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema([(c, pa.string()) for c in standardise_run._RAW_FIELDS])), tmp_path / "c.parquet")
    q = standardise_run.run(tmp_path / "c.parquet", tmp_path / "s.parquet")["validation"]["leads"]
    assert q["age"] == "2/3 valid" and q["record_date"] == "1/2 valid"


def test_trained_model_gives_age_compatibility_a_positive_and_incompatibility_a_negative_weight(tmp_path):
    import json
    import os

    from dedupe.pipeline import PipelineConfig, run_pipeline
    from dedupe.synthetic import generate as gen_data
    syn = tmp_path / "syn"
    gen_data(60, seed=96, out=syn)
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for src in cfg["sources"]:
        src["connector"]["path"] = str(syn / os.path.basename(src["connector"]["path"]))
    (tmp_path / "sources.json").write_text(json.dumps(cfg), encoding="utf-8")
    run_pipeline(PipelineConfig(tmp_path / "sources.json", tmp_path / "work"))
    w = json.loads((tmp_path / "models" / "fs_model.json").read_text(encoding="utf-8"))["weights"]["dob"]
    assert w[LABELS["age-based: compatible"]] > 0.5 > -0.5 > w[LABELS["age-based: incompatible"]]
    assert abs(w[LABELS["age-based: incompatible"]]) > w[LABELS["age-based: compatible"]]      # contradicting an age is stronger evidence than agreeing with it
