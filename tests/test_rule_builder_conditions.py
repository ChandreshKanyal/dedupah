"""The rule builder's conditions: values of a column (one of, contains, empty, within a number, a percentage or days), which data source a
record came from, and one record's column against another column of the other record (which also finds such pairs). Nothing typed reaches a
query unescaped."""
import csv
import json

import duckdb
import pytest

from dedupe import crypto
from dedupe.blocking import cross_rules
from dedupe.custom import build_spec
from dedupe.decision import ConditionError, compile_condition
from dedupe.decision.conditions import cross_pairs
from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.scoring.comparisons import use

FIELDS = [{"name": "segment", "kind": "category"}, {"name": "amount", "kind": "number"}, {"name": "opened", "kind": "date"},
          {"name": "gstin", "kind": "code"}, {"name": "parent_gstin", "kind": "code"}, {"name": "note", "kind": "text"}]


def _eval(node, rows, cols):
    """The condition on rows of (l_..., r_...) values: a list of True/False."""
    with use(build_spec(FIELDS)):
        sql = compile_condition(node)
    names = ", ".join(cols)
    lit = lambda v: "NULL" if v is None else "'" + v.replace("'", "''") + "'" if isinstance(v, str) else repr(v)   # noqa: E731
    values = ", ".join("(" + ", ".join(lit(v) for v in row) + ")" for row in rows)
    return [bool(x[0]) for x in duckdb.sql(f"SELECT coalesce({sql}, false) FROM (VALUES {values}) t({names})").fetchall()]


def test_values_of_a_column_both_at_least_one_or_neither_record():
    cols = ["l_x_segment", "r_x_segment"]
    rows = [("corporate", "corporate"), ("corporate", "retail"), ("retail", None), (None, None)]
    assert _eval({"field": "x_segment", "op": "both_in", "values": ["Corporate", "SME"]}, rows, cols) == [True, False, False, False]
    assert _eval({"field": "x_segment", "op": "either_in", "values": ["CORPORATE"]}, rows, cols) == [True, True, False, False]
    assert _eval({"field": "x_segment", "op": "neither_in", "values": ["corporate"]}, rows, cols) == [False, False, True, True]
    assert _eval({"field": "x_segment", "op": "either_empty"}, rows, cols) == [False, False, True, True]
    assert _eval({"field": "x_segment", "op": "both_empty"}, rows, cols) == [False, False, False, True]
    assert _eval({"field": "x_segment", "op": "both_filled"}, rows, cols) == [True, True, False, False]


def test_a_typed_value_is_cleaned_like_the_column_so_it_is_found_as_written():
    cols = ["l_x_gstin", "r_x_gstin"]
    rows = [("27AAPFU0939F1ZV", "27AAPFU0939F1ZV"), ("27AAPFU0939F1ZV", None)]
    assert _eval({"field": "x_gstin", "op": "either_in", "values": ["27-aapfu 0939f/1zv"]}, rows, cols) == [True, True]


def test_numbers_percentages_and_days():
    rows = [(1000.0, 1040.0), (1000.0, 1100.0), (None, 5.0)]
    cols = ["l_x_amount", "r_x_amount"]
    assert _eval({"field": "x_amount", "op": "within", "value": 50}, rows, cols) == [True, False, False]
    assert _eval({"field": "x_amount", "op": "within_pct", "value": 5}, rows, cols) == [True, False, False]
    days = [("2024-01-01", "2024-01-20"), ("2024-01-01", "2024-03-01"), ("2024-01-01", "not a date")]
    assert _eval({"field": "x_opened", "op": "within_days", "value": 30}, days, ["l_x_opened", "r_x_opened"]) == [True, False, False]


def test_text_containing_a_piece_and_quotes_in_what_is_typed():
    rows = [("o'brien trust", "smith trust"), ("o'brien trust", "smith and co")]
    cols = ["l_x_note", "r_x_note"]
    assert _eval({"field": "x_note", "op": "both_contain", "value": "TRUST"}, rows, cols) == [True, False]
    assert _eval({"field": "x_note", "op": "either_contains", "value": "O'Brien"}, rows, cols) == [True, True]


def test_which_data_source_each_record_came_from():
    rows = [("crm", "billing"), ("crm", "crm")]
    cols = ["l_source_id", "r_source_id"]
    assert _eval({"field": "source_id", "op": "differs"}, rows, cols) == [True, False]
    assert _eval({"field": "source_id", "op": "either_in", "values": ["billing"]}, rows, cols) == [True, False]


def test_one_records_column_against_another_column_of_the_other_record_either_way_round():
    cols = ["l_x_gstin", "l_x_parent_gstin", "r_x_gstin", "r_x_parent_gstin"]
    rows = [("A1", None, None, "A1"), (None, "B2", "B2", None), ("A1", None, "A1", None), ("A1", "B2", "C3", "D4")]
    assert _eval({"cross": ["x_gstin", "x_parent_gstin"], "op": "same"}, rows, cols) == [True, True, False, False]
    near = [("ACME TRADERS", None, None, "ACME TRADER"), ("ACME", None, None, "ZENITH")]
    assert _eval({"cross": ["x_gstin", "x_parent_gstin"], "op": "similar"}, near, cols) == [True, False]


def test_what_is_not_a_valid_condition_is_refused_and_never_reaches_a_query():
    bad = [{"field": "x_segment", "op": "both_in", "values": []}, {"field": "x_segment", "op": "both_in", "values": ["a"] * 51},
           {"field": "x_segment", "op": "both_in", "values": ["x" * 101]}, {"field": "x_segment", "op": "both_in", "values": ["a\x00b"]},
           {"field": "x_amount", "op": "within_pct", "value": 101}, {"field": "x_amount", "op": "within", "value": "1; DROP"},
           {"field": "x_opened", "op": "within_days", "value": 2.5}, {"cross": ["x_gstin", "x_gstin"], "op": "same"},
           {"cross": ["x_gstin", "pan; DROP TABLE t"], "op": "same"}, {"cross": ["x_gstin", "x_parent_gstin"], "op": "like"},
           {"field": "source_id; DROP", "op": "differs"}, {"field": "x_gstin", "op": "either_in", "values": ["---"]}]
    with use(build_spec(FIELDS)):
        for b in bad:
            with pytest.raises(ConditionError):
                compile_condition(b)


def test_only_a_two_column_same_condition_that_must_hold_finds_pairs():
    same = {"cross": ["x_gstin", "x_parent_gstin"], "op": "same"}
    assert cross_pairs({"all": [same, {"field": "source_id", "op": "differs"}]}) == [("x_gstin", "x_parent_gstin")]
    assert cross_pairs({"any": [same]}) == [("x_gstin", "x_parent_gstin")]
    assert cross_pairs({"not": same}) == [] and cross_pairs({"cross": ["x_gstin", "x_parent_gstin"], "op": "similar"}) == []
    con = duckdb.connect()
    con.execute("CREATE TABLE records AS SELECT * FROM (VALUES (0, 'A1', NULL), (1, NULL, 'A1'), (2, 'B2', NULL)) t(rid, x_gstin, x_parent_gstin)")
    rules = cross_rules([("x_gstin", "x_parent_gstin"), ("x_parent_gstin", "x_gstin")])
    assert len(rules) == 1
    keys = con.execute(f"SELECT key, list_sort(list(rid)) FROM ({rules[0].sql}) GROUP BY 1 ORDER BY 1").fetchall()
    assert keys == [("A1", [0, 1]), ("B2", [2])]


def _two_files(tmp_path):
    """A billing file naming each customer's parent GSTIN, and a GST register; the names are written completely differently, so only the
    parent GSTIN links them."""
    billing = [["b1", "Sunrise Retail Outlet", "27AAAAA1111A1Z1", "corporate"], ["b2", "Blue Harbour Shop", "27BBBBB2222B1Z2", "retail"],
               ["b3", "Green Valley Store", "27CCCCC3333C1Z3", "test"], ["b4", "Red Fort Mart", "", "retail"]]
    register = [["g1", "Zenith Holdings", "27AAAAA1111A1Z1"], ["g2", "Orient Group", "27BBBBB2222B1Z2"], ["g3", "Apex Corporation", "27CCCCC3333C1Z3"],
                ["g4", "Pioneer Limited", "27DDDDD4444D1Z4"]]
    for name, head, rows in (("billing", ["id", "shop", "parent", "segment"], billing), ("register", ["id", "legal name", "gstin"], register)):
        with (tmp_path / f"{name}.csv").open("w", encoding="utf-8", newline="") as h:
            w = csv.writer(h)
            w.writerow(head)
            w.writerows(rows)
    cfg = {"sources": [{"source_id": "billing", "connector": {"type": "csv", "path": str(tmp_path / "billing.csv")}, "record_id": "id",
                        "custom": {"name": "shop", "parent_gstin": "parent", "segment": "segment"}},
                       {"source_id": "register", "connector": {"type": "csv", "path": str(tmp_path / "register.csv")}, "record_id": "id",
                        "custom": {"name": "legal name", "gstin": "gstin"}}]}
    (tmp_path / "sources.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp_path / "sources.json"


def test_a_rule_on_two_different_columns_finds_and_merges_records_nothing_else_links(tmp_path):
    fields = [{"name": "name", "kind": "company"}, {"name": "gstin", "kind": "code"}, {"name": "parent_gstin", "kind": "code"},
              {"name": "segment", "kind": "category"}]
    dec = {"auto_merge_at": 0.9, "review_at": 0.1, "use_score": False, "rules": [
        {"id": "never_test_rows", "type": "never_merge", "when": {"field": "x_segment", "op": "either_in", "values": ["Test"]}},
        {"id": "parent_is_register", "type": "auto_merge", "when": {"all": [{"cross": ["x_parent_gstin", "x_gstin"], "op": "same"},
                                                                            {"field": "source_id", "op": "differs"}]}}]}
    (tmp_path / "decision.json").write_text(json.dumps(dec), encoding="utf-8")
    s = run_pipeline(PipelineConfig(_two_files(tmp_path), tmp_path / "work", custom_fields=fields, decision_config=tmp_path / "decision.json",
                                    build_households=False))
    assert s["decisions"]["auto_merge"] == 2 and "rules_not_applied" not in s["decisions"]          # b1+g1, b2+g2; b3+g3 is a test row
    got = dict(crypto.connect().execute(f"SELECT source_record_id, cluster_id FROM {crypto.rp(tmp_path / 'work' / 'clusters' / 'clusters.parquet')}").fetchall())
    assert got["b1"] == got["g1"] and got["b2"] == got["g2"] and got["b3"] != got["g3"] and len(set(got.values())) == 6
