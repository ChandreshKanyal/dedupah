import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from dedupe.survivorship import GOLDEN_FIELDS, SurvivorshipConfig, build_golden

STD_COLS = ["name_clean", "father_name_clean", "dob", "dob_placeholder", "dob_precision", "gender", "mobile", "mobile_alt", "email", "city", "state", "address_pin", "pan", "aadhaar_token", "aadhaar_last4",
            "passport_no", "dl_no", "voter_id"]
CAN_COLS = ["marital_status", "address", "occupation", "annual_income"]


def _build(tmp_path, recs, cfg):
    """recs: list of dicts with cluster, source, id, optional attrs (dict) and any standardised/canonical column (pincode for address_pin)."""
    key = lambda r: {"source_id": r["source"], "source_record_id": r["id"]}  # noqa: E731
    std = [{**key(r), **{c: r.get("pincode" if c == "address_pin" else c) for c in STD_COLS}} for r in recs]
    can = [{**key(r), **{c: r.get(c) for c in CAN_COLS}, "attributes": json.dumps(r.get("attrs", {}))} for r in recs]
    cl = [{**key(r), "cluster_id": r["cluster"]} for r in recs]
    paths = {}
    for name, rows in (("std", std), ("can", can), ("cl", cl)):
        paths[name] = tmp_path / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), paths[name])
    con = duckdb.connect()
    info = build_golden(con, paths["std"], paths["can"], paths["cl"], cfg, tmp_path / "golden")
    vals = {(r[0], r[1]): r[2:] for r in con.execute(
        f"select cluster_id, field, value, source_id, strategy, n_candidates, n_distinct from '{tmp_path / 'golden' / 'golden_values.parquet'}'").fetchall()}
    return con, info, vals   # vals[(cluster, field)] = (value, source_id, strategy, n_candidates, n_distinct)


def test_each_strategy_picks_what_it_promises(tmp_path):
    cfg = SurvivorshipConfig(source_trust=["kyc", "crm", "leads"], source_dates={"crm": "opened", "leads": "created"},
                             fields={"email": "most_recent", "occupation": "most_trusted_source", "full_name": "most_complete", "gender": "most_frequent", "dob": "most_frequent"})
    recs = [
        {"cluster": "c1", "source": "crm", "id": "1", "email": "old@x.in", "occupation": "Clerk", "name_clean": "raj s", "gender": "F",
         "attrs": {"opened": "01/01/2020"}},
        {"cluster": "c1", "source": "leads", "id": "2", "email": "new@x.in", "occupation": "Manager", "name_clean": "rajesh sharma", "gender": "M",
         "attrs": {"created": "2026-05-01"}},
        {"cluster": "c1", "source": "kyc", "id": "3", "occupation": "Engineer", "name_clean": "r sharma", "gender": "M"},
    ]
    _, info, v = _build(tmp_path, recs, cfg)
    assert v["c1", "email"][:2] == ("new@x.in", "leads")                  # most recent (2026 beats 2020; both date formats parsed)
    assert v["c1", "occupation"][:2] == ("Engineer", "kyc")               # most trusted source
    assert v["c1", "full_name"][0] == "Rajesh Sharma"                     # most complete (longest)
    assert v["c1", "gender"][0] == "M"                                    # most frequent (2 of 3)
    assert v["c1", "gender"][3:] == (3, 2)                                # 3 candidates, 2 distinct values: the conflict is recorded
    assert info["golden_records"] == 1


def test_group_fields_come_from_one_record_and_skip_records_without_the_key_field(tmp_path):
    cfg = SurvivorshipConfig(source_trust=["crm", "leads"], source_dates={"leads": "created", "crm": "opened"}, fields={"addr": "most_recent"},
                             groups={"addr": ["address", "city", "state", "pincode"]})
    recs = [
        {"cluster": "c1", "source": "crm", "id": "1", "address": "12 Park St", "city": "pune", "state": "maharashtra", "pincode": "411001",
         "attrs": {"opened": "01/01/2020"}},
        {"cluster": "c1", "source": "leads", "id": "2", "city": "mumbai", "attrs": {"created": "01/01/2026"}},   # newer but has only a city
        {"cluster": "c2", "source": "leads", "id": "3", "city": "delhi", "attrs": {"created": "01/01/2026"}},     # nobody has an address: city survives
    ]
    _, _, v = _build(tmp_path, recs, cfg)
    assert [v["c1", f][0] for f in ("address", "city", "state", "pincode")] == ["12 Park St", "Pune", "Maharashtra", "411001"]
    assert {v["c1", f][1] for f in ("address", "city", "state", "pincode")} == {"crm"}   # one source record for the whole block
    assert v["c2", "city"][0] == "Delhi" and ("c2", "address") not in v


def test_ties_fall_back_to_trust_and_results_are_stable(tmp_path):
    cfg = SurvivorshipConfig(source_trust=["b", "a"], default_strategy="most_recent")
    recs = [{"cluster": "c", "source": "a", "id": "1", "email": "a@x.in"}, {"cluster": "c", "source": "b", "id": "2", "email": "b@x.in"}]
    first = _build(tmp_path, recs, cfg)[2]
    second = _build(tmp_path, list(reversed(recs)), cfg)[2]
    assert first["c", "email"][0] == "b@x.in" == second["c", "email"][0]   # no dates anywhere: the more trusted source wins, in any row order


def test_names_are_shown_in_title_case_and_lineage_files_are_complete(tmp_path):
    cfg = SurvivorshipConfig(source_trust=["kyc"], fields={"full_name": "most_complete"})
    recs = [{"cluster": "c", "source": "kyc", "id": "1", "name_clean": "pooja kumar mehta", "pan": "ABCDE1234F"},
            {"cluster": "c", "source": "kyc", "id": "2", "name_clean": "pooja k mehta", "pan": "ABCDE1234F"}]
    con, _, v = _build(tmp_path, recs, cfg)
    assert v["c", "full_name"][0] == "Pooja Kumar Mehta"
    row = con.execute(f"select record_count, sources, pan, conflict_fields from '{tmp_path / 'golden' / 'golden_records.parquet'}'").fetchone()
    assert row[0] == 2 and row[1] == ["kyc"] and row[2] == "ABCDE1234F" and "full_name" in row[3] and "pan" not in row[3]
    assert all(val[1] == "kyc" for val in v.values())    # every golden value names the source it came from


def test_voting_outvotes_typos_and_placeholder_birth_dates_are_not_values(tmp_path):
    cfg = SurvivorshipConfig(source_trust=["a", "b", "c"], fields={"email": "most_frequent", "dob": "most_frequent", "full_name": "most_frequent"})
    recs = [{"cluster": "c1", "source": s, "id": s, "email": e, "name_clean": n, "dob": d, "dob_precision": pr, "dob_placeholder": ph}
            for s, e, n, d, pr, ph in [("a", "ritu@gmial.com", "ritu bansal", "1990-01-01", "full", True),     # typo domain, default 1-Jan
                                       ("b", "ritu@gmail.com", "ritu bansal", "1990-05-17", "full", False),
                                       ("c", "ritu@gmail.com", "mr ritu bansal x", "1990", "year", False)]]
    _, _, v = _build(tmp_path, recs, cfg)
    assert v["c1", "email"][0] == "ritu@gmail.com"      # two of three agree
    assert v["c1", "full_name"][0] == "Ritu Bansal"
    assert v["c1", "dob"][:2] == ("1990-05-17", "b")    # the placeholder and the bare year were never candidates
    assert v["c1", "dob"][3:] == (1, 1)


def test_config_validation():
    with pytest.raises(ValidationError):
        SurvivorshipConfig(fields={"nope": "most_recent"})
    with pytest.raises(ValidationError):
        SurvivorshipConfig(groups={"g": ["address", "city"], "h": ["city"]})                    # a field in two groups
    with pytest.raises(ValidationError):
        SurvivorshipConfig(groups={"g": ["address", "city"]}, fields={"city": "most_recent"})   # strategy must be set on the group
    with pytest.raises(ValidationError):
        SurvivorshipConfig(source_dates={"crm": "x'; DROP TABLE y; --"})                        # attribute names are identifiers only
    with pytest.raises(ValidationError):
        SurvivorshipConfig(default_strategy="whatever")
    assert len(SurvivorshipConfig.load("configs/survivorship_default.json").units()) == len(GOLDEN_FIELDS) - 3   # address block merges 4 fields into 1
