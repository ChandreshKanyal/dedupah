"""User review round 2: a preview of result tables (masked, searchable, unmask with a reason), real sample values while mapping (with a
reason), which field comes from which connection, the field guide for the mapping screen, and a customer number shared across sources."""
import csv
import json

import duckdb
import pytest
from api_world import PROJECT, app_with_run, csv_connection, hdr, make_live, pipeline_output, small_csv

from dedupe.api.routes_connections import FIELD_GROUPS
from dedupe.mapping.canonical import CANONICAL_FIELDS
from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.standardise.identifiers import customer_number

WHY = "checking the merged customers"


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    return pipeline_output(tmp_path_factory.mktemp("round2"), seed=83, households=40)


@pytest.fixture
def api(output, tmp_path):
    return app_with_run(tmp_path, output, ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6)


def test_golden_records_can_be_looked_at_masked_searched_and_unmasked_with_a_reason(api, output):
    c, s, sv, run = api
    url = run + "/datasets/golden_records/rows"
    page = c.get(url + "?limit=10", headers=hdr(s, "viewer")).json()
    cols = page["columns"]
    assert cols[0] == "person_id" and len(page["rows"]) == 10 and page["total"] > 10 and not page["unmasked"]
    assert {"pan", "mobile", "email"} <= set(page["masked"])
    raw = duckdb.sql(f"SELECT pan FROM '{output / 'golden' / 'golden_records.parquet'}' WHERE pan IS NOT NULL").fetchall()
    shown = {r[cols.index("pan")] for r in page["rows"]}
    assert not shown & {x[0] for x in raw}                                        # no full PAN on a masked page
    name = next(r[cols.index("full_name")] for r in page["rows"] if r[cols.index("full_name")])
    found = c.get(url + "?q=" + name.split()[0].lower(), headers=hdr(s, "viewer")).json()
    assert found["total"] >= 1 and all(name.split()[0].lower() in (r[cols.index("full_name")] or "").lower() or name.split()[0].lower() in r[0].lower()
                                       for r in found["rows"])
    assert c.get(url + "?unmask=true&reason=" + WHY, headers=hdr(s, "viewer")).status_code == 403         # needs pii.unmask
    assert c.get(url + "?unmask=true&reason=short", headers=hdr(s, "steward")).status_code == 422
    open_page = c.get(url + "?limit=100&unmask=true&reason=" + WHY, headers=hdr(s, "steward"))
    assert open_page.status_code == 200 and open_page.json()["unmasked"] and open_page.json()["masked"] == []
    assert {r[cols.index("pan")] for r in open_page.json()["rows"]} & {x[0] for x in raw}
    logged = [e for e in sv.seclog.events(event_name="pii_unmasked") if e["object_ref"].endswith("golden_records")]
    assert logged and logged[-1]["detail"]["reason"] == WHY and logged[-1]["detail"]["view"] == "results preview"
    hh = c.get(run + "/datasets/households/rows?limit=5", headers=hdr(s, "viewer")).json()
    assert hh["columns"][0] == "person_id" and hh["rows"]


def test_the_mapping_screen_gets_a_field_guide_real_samples_with_a_reason_and_field_sources(api, tmp_path):
    c, s, sv, _ = api
    assert sorted(f for g in FIELD_GROUPS for f in g["fields"]) == sorted(CANONICAL_FIELDS)        # every field has a place on the screen
    guide = c.get("/connectors", headers=hdr(s, "platform_admin")).json()
    assert guide["field_help"]["customer_number"] and guide["field_groups"][0]["title"]
    src = small_csv(tmp_path / "crm.csv")
    base = f"/projects/{PROJECT}/connections"
    masked = c.post(base + "/inspect", json={"connector": {"type": "csv", "path": str(src)}}, headers=hdr(s, "platform_admin")).json()
    assert not masked["unmasked"] and masked["sample"][0]["pan"] != "ABCDE1234F"
    shown = c.post(base + "/inspect?unmask=true&reason=" + WHY.replace(" ", "%20"), json={"connector": {"type": "csv", "path": str(src)}},
                   headers=hdr(s, "admin")).json()
    assert shown["unmasked"] and shown["sample"][0]["pan"] == "ABCDE1234F"
    make_live(c, s, csv_connection(src))
    row = next(x for x in c.get(base, headers=hdr(s, "viewer")).json() if x["id"] == "crm")
    assert row["fields"]["pan"] == "pan" and row["record_id"] == "cust_id" and row["extra_columns"] == ["internal_note"]


def test_customer_numbers_are_cleaned_and_link_records_across_sources(tmp_path):
    assert customer_number(" cif-000123 ") == "CIF000123" and customer_number("000123") == "123" == customer_number("123.0")
    assert customer_number("NA") is None and customer_number("0000") is None
    rows = {"crm.csv": [("A1", "Rahul Sharma", "CIF-00077", "9811111111"), ("A2", "Anita Rao", "CIF-00088", "9822222222")],
            "loans.csv": [("L1", "R Sharma", "cif 00077", ""), ("L2", "Anita Rao", "CIF00099", "9833333333")]}
    sources = []
    for name, data in rows.items():
        with (tmp_path / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "name", "cif", "phone"])
            w.writerows(data)
        sources.append({"source_id": name[:-4], "connector": {"type": "csv", "path": str(tmp_path / name)}, "record_id": "id",
                        "fields": {"full_name": "name", "customer_number": "cif", "mobile": "phone"}})
    (tmp_path / "s.json").write_text(json.dumps({"sources": sources}), encoding="utf-8")
    rules = json.loads(open("configs/decision_default.json", encoding="utf-8").read())
    for r in rules["rules"]:
        if r["id"] in ("auto_same_customer_number", "veto_customer_number_conflict"):
            assert r["enabled"] is False                                          # off until a project chooses it
            r["enabled"] = True
    (tmp_path / "d.json").write_text(json.dumps(rules), encoding="utf-8")
    run_pipeline(PipelineConfig(tmp_path / "s.json", tmp_path / "w", decision_config=tmp_path / "d.json"))
    cl = dict(duckdb.sql(f"SELECT source_id || '/' || source_record_id, cluster_id FROM '{tmp_path / 'w' / 'clusters' / 'clusters.parquet'}'").fetchall())
    assert cl["crm/A1"] == cl["loans/L1"]                                         # same customer number, compatible name
    assert cl["crm/A2"] != cl["loans/L2"]                                         # same name, different customer numbers: never merged
    gold = duckdb.sql(f"SELECT customer_number FROM '{tmp_path / 'w' / 'golden' / 'golden_records.parquet'}' WHERE cluster_id = '{cl['crm/A1']}'").fetchone()
    assert gold == ("CIF00077",)


def test_the_rule_builder_catalogue_and_a_custom_rule_built_like_the_form_does(api):
    c, s, _, _ = api
    cat = c.get("/rules-catalogue", headers=hdr(s, "analyst")).json()
    names = {x["name"]: x for x in cat["comparisons"]}
    assert names["pan"]["levels"] == ["same PAN", "different PANs"] and names["customer_number"]["title"] == "Customer number"
    assert set(cat["rule_types"]) == {"never_merge", "force_review", "auto_merge", "not_enough_evidence"}
    base = f"/projects/{PROJECT}/config/decision"
    content = c.get(base + "/default", headers=hdr(s, "analyst")).json()["content"]
    content["rules"].append({"id": "custom_different_employer_city", "type": "force_review", "reason": "Ask when the cities differ and only a phone agrees",
                             "when": {"all": [{"comparison": "phone", "is": "same primary number"}, {"comparison": "address", "is_not": "same PIN"},
                                              {"score": "probability", "op": ">=", "value": 0.9}, {"field": "dob_year", "op": "abs_diff_gt", "value": 5}]}})
    for r in content["rules"]:
        if r["id"] in ("auto_same_customer_number", "veto_customer_number_conflict"):
            r["enabled"] = True
    ok = c.post(base + "/propose", json={"content": content, "reason": "employer rule from the business team"}, headers=hdr(s, "analyst"))
    assert ok.status_code == 200, ok.text
    content["rules"][-1]["when"] = {"comparison": "phone", "is": "a level that does not exist"}
    assert c.post(base + "/propose", json={"content": content, "reason": "this one must be refused"}, headers=hdr(s, "analyst")).status_code == 422
