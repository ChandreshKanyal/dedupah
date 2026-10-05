"""Age (band) as evidence for records without a birth date (leads): standardisation, scoring levels, blocking and decisions."""
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from test_decision import RAW, _decide

from dedupe.blocking import generate
from dedupe.decision import DecisionConfig
from dedupe.scoring.comparisons import DOB
from dedupe.scoring.levels import materialize
from dedupe.standardise.dates import birth_year_range, parse_age
from dedupe.standardise.run import SCHEMA, standardise_row

DEFAULT = DecisionConfig.load("configs/decision_default.json")
LABELS = {v: k for k, v in DOB.labels().items()}


def test_age_parsing_and_birth_year_range():
    assert parse_age("35") == (35, 35) and parse_age("50-59") == (50, 59) and parse_age(" 50 – 59 ") == (50, 59) and parse_age("60+") == (60, 85)
    for bad in (None, "", "abc", "0", "130", "59-50", "-5", "1999"):
        assert parse_age(bad) is None
    assert birth_year_range(1990, "30", "2025-01-01") == (1990, 1990)           # an exact birth year always wins
    assert birth_year_range(None, "35", "07/08/2024") == (1988, 1989)           # 2024 - 35 - 1 .. 2024 - 35
    assert birth_year_range(None, "50-59", "07/08/2024") == (1964, 1974)
    assert birth_year_range(None, "35", None) == (1990, 1991)                   # no record date: the 2026 reference year
    assert birth_year_range(None, None, "2024-01-01") == (None, None)


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
    def rec(i, name_first, name_last, city, lo, hi):
        return {"source_id": "s", "source_record_id": f"{i:04d}", "name_first_phon": name_first, "name_last_phon": name_last, "city": city,
                "birth_year_min": lo, "birth_year_max": hi}
    rows = [rec(0, "RN", "MKRJ", "kolkata", 1965, 1975),        # lead: age band 50-59
            rec(1, "RN", "MKRJ", "kolkata", 1970, 1970),        # document: born 1970 -> compatible
            rec(2, "RN", "MKRJ", "kolkata", 1999, 1999),        # same name and city but born 1999 -> not a candidate
            rec(3, "RN", "MKRJ", "delhi", 1970, 1970),          # other city -> not a candidate
            rec(4, "RN", "MKRJ", "kolkata", None, None)]        # no age at all -> no key
    std = tmp_path / "std.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), std)
    generate(std, tmp_path / "cand.parquet")
    pairs = {(r[0], r[1]): r[2] for r in duckdb.sql(f"select l_rid, r_rid, rules from '{tmp_path / 'cand.parquet'}'").fetchall()}
    assert pairs == {(0, 1): ["name_city_age"]}


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
