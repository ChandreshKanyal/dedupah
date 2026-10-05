import random

import duckdb
import pyarrow as pa
import pytest
from pydantic import ValidationError

from dedupe.decision import ConditionError, DecisionConfig, Rule, compile_condition, decide
from dedupe.scoring import COMPARISONS
from dedupe.scoring.levels import materialize
from dedupe.standardise.run import SCHEMA, standardise_row
from dedupe.synthetic.identifiers import aadhaar

RAW = ["full_name", "father_name", "dob", "gender", "mobile", "mobile_alt", "email", "address", "address_alt", "city",
       "state", "pincode", "pan", "aadhaar", "aadhaar_last4", "passport_no", "dl_no", "voter_id", "related_name",
       "party_role", "relation_to_proposer"]


def _decide(cases, cfg, tmp_path):
    """cases: list of (left_raw, right_raw, probability). Returns [(decision, decided_by)]."""
    rows = []
    for i, raw in enumerate([r for c in cases for r in c[:2]]):
        base = {k: None for k in RAW}
        base.update(raw, source_id="s", source_record_id=f"{i:04d}")
        rows.append(standardise_row(base))
    con = duckdb.connect()
    con.register("t", pa.Table.from_pylist(rows, schema=SCHEMA))
    con.execute("CREATE TABLE records AS SELECT row_number() OVER (ORDER BY source_record_id) - 1 AS rid, * FROM t")
    con.execute("CREATE TABLE candidates AS SELECT 2*i AS l_rid, 2*i+1 AS r_rid, ['x'] AS rules FROM range(?) r(i)", [len(cases)])
    materialize(con, "candidates", "lv", with_tf=False)
    con.register("probs", pa.table({"i": list(range(len(cases))), "probability": [c[2] for c in cases]}))
    w = ", ".join(f"0.0::FLOAT AS w_{c.name}" for c in COMPARISONS)
    con.execute(f"CREATE TABLE sc AS SELECT lv.*, p.probability, 0.0::FLOAT AS match_weight, {w} FROM lv JOIN probs p ON p.i = lv.l_rid // 2")
    con.execute(f"COPY sc TO '{tmp_path / 'sc.parquet'}' (FORMAT PARQUET)")
    decide(con, tmp_path / "sc.parquet", cfg, tmp_path / "d.parquet")
    return con.execute("SELECT decision, decided_by FROM decisions ORDER BY l_rid").fetchall()


DEFAULT = DecisionConfig.load("configs/decision_default.json")
PAN_A, PAN_B = "ABCPD1234E", "ABCPD9999F"


RS = {"full_name": "Raj Shah", "email": "raj.shah@example.com"}  # name + one corroborating contact


def test_precedence_and_zones(tmp_path):
    cases = [
        ({"pan": PAN_A, "full_name": "Raj Shah"}, {"pan": PAN_B, "full_name": "Raj Shah"}, 0.99),   # PAN conflict -> reject
        ({"pan": PAN_A, "full_name": "Raj Shah"}, {"pan": PAN_A, "full_name": "R Shah"}, 0.20),      # same PAN, compatible -> auto
        ({"pan": PAN_A, "full_name": "Raj Shah"}, {"pan": PAN_A, "full_name": "Anita Verma"}, 0.99),  # same PAN, other name -> review
        (dict(RS), dict(RS), 0.95),                                                                 # score zones
        (dict(RS), dict(RS), 0.50),
        (dict(RS), dict(RS), 0.05),
        ({"gender": "M", "full_name": "Raj Shah"}, {"gender": "F", "full_name": "Raj Shah"}, 0.95),  # gender veto
        ({"full_name": "Raj Shah", "city": "Pune"}, {"full_name": "Raj Shah", "city": "Pune"}, 0.95),  # name + city only
    ]
    got = _decide(cases, DEFAULT, tmp_path)
    assert got[0] == ("reject", "veto_pan_conflict")
    assert got[1] == ("auto_merge", "auto_same_pan_compatible_name")
    assert got[2] == ("review", "review_identifier_with_different_name")
    assert [g[0] for g in got[3:6]] == ["auto_merge", "review", "reject"] and got[3][1] == "score"
    assert got[6] == ("reject", "veto_gender_conflict")
    assert got[7] == ("reject", "not_enough_evidence")  # a high score alone is not enough without corroborating evidence


def test_minor_twin_guard_blocks_auto_merge(tmp_path):
    kid = {"full_name": "Aarav Shah", "dob": "12/05/2015", "address": "H.No 5 Park Street Pune 411001"}
    adult = {"full_name": "Aarav Shah", "dob": "12/05/1985", "address": "H.No 5 Park Street Pune 411001"}
    got = _decide([(kid, dict(kid), 0.99), (adult, dict(adult), 0.99)], DEFAULT, tmp_path)
    assert got[0] == ("review", "review_possible_twins") and got[1][0] == "auto_merge"


def test_contradicting_identifiers_go_to_review_not_auto(tmp_path):
    a, b = aadhaar(random.Random(1)), aadhaar(random.Random(2))
    left = {"pan": PAN_A, "aadhaar": a, "full_name": "Raj Shah"}
    right = {"pan": PAN_A, "aadhaar": b, "full_name": "Raj Shah"}
    assert _decide([(left, right, 0.99)], DEFAULT, tmp_path)[0] == ("review", "review_identifiers_contradict")


def test_disabled_rule_is_ignored_and_thresholds_are_used(tmp_path):
    cfg = DecisionConfig(auto_merge_at=0.8, review_at=0.3, rules=[
        Rule(id="off", type="never_merge", when={"comparison": "gender", "is": "different gender"}, enabled=False)])
    cases = [({"gender": "M"}, {"gender": "F"}, 0.85), ({"gender": "M"}, {"gender": "M"}, 0.5), ({"gender": "M"}, {"gender": "M"}, 0.2)]
    assert [g[0] for g in _decide(cases, cfg, tmp_path)] == ["auto_merge", "review", "reject"]


def test_condition_compiler_forms_and_errors():
    assert compile_condition({"comparison": "pan", "is": "same PAN"}) == "(lv_pan = 1)"
    assert compile_condition({"comparison": "pan", "missing": True}) == "(lv_pan = -1)"
    assert "IN (" in compile_condition({"comparison": "name", "in": ["identical cleaned name", "different"]})
    assert "abs(l_dob_year - r_dob_year) > 10" in compile_condition({"field": "dob_year", "op": "abs_diff_gt", "value": 10})
    assert compile_condition({"score": "probability", "op": ">=", "value": 0.9}) == "(probability >= 0.9)"
    assert compile_condition({"not": {"any": [{"comparison": "pan", "is": "same PAN"}, {"field": "gender", "op": "same"}]}}).startswith("(NOT (")
    bad = [{"comparison": "nope", "is": "x"}, {"comparison": "pan", "is": "no such level"}, {"field": "dob; DROP TABLE x", "op": "same"},
           {"score": "probability", "op": "; DROP", "value": 1}, {"field": "dob_year", "op": "abs_diff_gt", "value": "1; DROP"},
           {"all": []}, {}, {"weird": 1}, {"comparison": "pan"}]
    for b in bad:
        with pytest.raises(ConditionError):
            compile_condition(b)


def test_config_validation():
    with pytest.raises(ValidationError):
        DecisionConfig(auto_merge_at=0.5, review_at=0.7)
    ok = {"id": "a", "type": "never_merge", "when": {"comparison": "pan", "is": "same PAN"}}
    with pytest.raises(ValidationError):
        DecisionConfig(rules=[ok, ok])  # duplicate ids
    with pytest.raises(ValidationError):
        DecisionConfig(rules=[{**ok, "when": {"comparison": "pan", "is": "bogus"}}])  # bad rule rejected at load time
    with pytest.raises(ValidationError):
        DecisionConfig(rules=[{**ok, "id": "Bad Id!"}])
    assert len(DEFAULT.rules) >= 8


def test_possible_namesake_goes_to_review_unless_corroborated(tmp_path):
    a = {"full_name": "Rahul Sharma", "dob": "12/05/1985"}
    b = {"full_name": "Rahul Varma", "dob": "12/05/1985"}                     # similar name + same birthday only
    c = {**b, "email": "rahul@example.com"}
    d = {**a, "email": "rahul@example.com"}                                   # same pair plus a shared email
    got = _decide([(a, b, 0.95), (d, c, 0.95), (a, b, 0.5)], DEFAULT, tmp_path)
    assert got[0] == ("review", "review_possible_namesake")
    assert got[1] == ("auto_merge", "score")
    assert got[2] == ("review", "score")   # below the auto threshold the rule does not fire; normal zones apply
