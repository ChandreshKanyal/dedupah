"""Aadhaar reference keys (from the customer's own Aadhaar Data Vault) as an alternative to full numbers."""
import random

from test_decision import RAW

import dedupe.standardise.identifiers as ids
from dedupe.mapping.canonical import CANONICAL_FIELDS
from dedupe.standardise.run import standardise_row
from dedupe.synthetic.identifiers import aadhaar as make_aadhaar


def _row(**kw):
    base = {k: None for k in RAW} | {"age": None, "record_date": None, "aadhaar_reference_key": None, "source_id": "s", "source_record_id": "x"}
    return standardise_row(base | kw)


def test_a_reference_key_becomes_the_identifier_in_its_own_namespace():
    a = _row(full_name="Rina Mukherjee", aadhaar_reference_key="vault-ref-7F3A9C21")
    b = _row(full_name="R Mukherjee", aadhaar_reference_key="  vault-ref-7F3A9C21 ")
    c = _row(full_name="Rina Mukherjee", aadhaar_reference_key="vault-ref-OTHER999")
    assert a["aadhaar_token"] == b["aadhaar_token"] == "ref:vault-ref-7F3A9C21" and c["aadhaar_token"] != a["aadhaar_token"]


def test_a_full_number_wins_and_bad_keys_are_ignored(monkeypatch):
    monkeypatch.setenv("DEDUPE_TOKEN_KEY", "k" * 32)
    num = make_aadhaar(random.Random(5))
    both = _row(aadhaar=num, aadhaar_reference_key="vault-ref-7F3A9C21")
    assert both["aadhaar_token"] == ids.aadhaar(num)[0] and not both["aadhaar_token"].startswith("ref:")
    for bad in ("short", "has spaces in it", "<script>alert(1)</script>", "x" * 200, "", None):
        assert _row(aadhaar_reference_key=bad)["aadhaar_token"] is None


def test_the_canonical_schema_offers_the_field():
    assert "aadhaar_reference_key" in CANONICAL_FIELDS
