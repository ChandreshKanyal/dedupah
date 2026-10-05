import csv
import random

import pytest

from dedupe.synthetic import generate
from dedupe.synthetic.identifiers import aadhaar, verhoeff_valid


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("syn")
    manifest = generate(150, seed=7, out=out)
    return out, manifest


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_verhoeff_known_value_and_generated_aadhaar():
    assert verhoeff_valid("2363")  # classic Verhoeff test vector
    assert not verhoeff_valid("2364")
    rng = random.Random(1)
    assert all(verhoeff_valid(aadhaar(rng)) and len(aadhaar(rng)) == 12 for _ in range(200))


def test_deterministic_for_same_seed(tmp_path):
    a = generate(40, seed=3, out=tmp_path / "a")
    b = generate(40, seed=3, out=tmp_path / "b")
    assert a == b
    assert (tmp_path / "a" / "bank_crm.csv").read_text(encoding="utf-8") == (tmp_path / "b" / "bank_crm.csv").read_text(encoding="utf-8")


def test_all_files_written(dataset):
    out, _ = dataset
    for f in ["bank_crm.csv", "loans.csv", "marketing_leads.csv", "kyc_documents.csv", "insurance_policies.xlsx",
              "manifest.json", "ground_truth/persons_truth.csv", "ground_truth/record_truth.csv"]:
        assert (out / f).exists(), f


def test_every_record_has_truth_and_ids_are_unique(dataset):
    out, _ = dataset
    persons = {r["person_id"] for r in read(out / "ground_truth/persons_truth.csv")}
    truth = read(out / "ground_truth/record_truth.csv")
    assert truth and all(t["person_id"] in persons for t in truth)
    id_cols = {"bank_crm": ("bank_crm.csv", "cust_id"), "loans": ("loans.csv", "loan_id"),
               "leads": ("marketing_leads.csv", "lead_id"), "kyc_documents": ("kyc_documents.csv", "doc_id")}
    for source, (fname, col) in id_cols.items():
        ids = [r[col] for r in read(out / fname)]
        assert len(ids) == len(set(ids))
        linked = {t["source_record_id"] for t in truth if t["source"] == source}
        assert set(ids) <= linked


def test_hard_cases_present(dataset):
    _, manifest = dataset
    tc = manifest["trap_counts"]
    for trap in ("shared_mobile", "maiden_name", "twin"):
        assert tc.get(trap, 0) > 0, trap


def test_families_and_in_source_duplicates_exist(dataset):
    out, _ = dataset
    people = read(out / "ground_truth/persons_truth.csv")
    sizes = {}
    for p in people:
        sizes[p["household_id"]] = sizes.get(p["household_id"], 0) + 1
    assert max(sizes.values()) >= 4 and min(sizes.values()) >= 1
    per_person = {}
    for t in read(out / "ground_truth/record_truth.csv"):
        if t["source"] == "bank_crm":
            per_person[t["person_id"]] = per_person.get(t["person_id"], 0) + 1
    assert any(n > 1 for n in per_person.values())


def test_row_order_does_not_leak_households(dataset):
    out, _ = dataset
    truth = {t["source_record_id"]: t["household_id"] for t in read(out / "ground_truth/record_truth.csv")}
    ids = [r["cust_id"] for r in read(out / "bank_crm.csv")]
    same = sum(truth[a] == truth[b] for a, b in zip(ids, ids[1:], strict=False))
    assert same / (len(ids) - 1) < 0.1
