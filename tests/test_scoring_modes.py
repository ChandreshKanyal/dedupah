"""How the match scores are learned when the data does or does not carry identifiers, and that odd data never breaks a run."""
import csv
import json
import os
from pathlib import Path

import numpy as np
import pytest

from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.scoring import COMPARISONS
from dedupe.scoring import unsupervised as un
from dedupe.synthetic import generate as gen_data

IDS = {"pan", "aadhaar", "aadhaar_last4", "passport_no", "dl_no", "voter_id"}
IX = {c.name: i for i, c in enumerate(COMPARISONS)}


def _synthetic(tmp_path, strip_ids: bool, households=80, seed=73):
    syn = tmp_path / "syn"
    gen_data(households, seed=seed, out=syn)
    cfg = json.loads(Path("configs/synthetic_sources.json").read_text(encoding="utf-8"))
    for s in cfg["sources"]:
        s["connector"]["path"] = str(syn / os.path.basename(s["connector"]["path"]))
    if strip_ids:
        cfg["sources"] = [s for s in cfg["sources"] if s["source_id"] != "kyc_documents"]
        for s in cfg["sources"]:
            s["fields"] = {k: v for k, v in s["fields"].items() if k not in IDS}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return syn, path


def test_ids_present_learns_from_identifiers(tmp_path):
    syn, path = _synthetic(tmp_path, strip_ids=False)
    s = run_pipeline(PipelineConfig(path, tmp_path / "work", truth=syn))
    assert s["scoring"]["mode"] == "id_anchored"
    assert s["scoring"]["seed_pairs"]["same_person"] >= un.MIN_SEEDS


def test_no_identifiers_still_scores_sensibly(tmp_path):
    syn, path = _synthetic(tmp_path, strip_ids=True)
    s = run_pipeline(PipelineConfig(path, tmp_path / "work", truth=syn))
    sc = s["scoring"]
    assert sc["mode"] == "unsupervised_em" and sc["seed_pairs"] == {"same_person": 0, "different_people": 0}
    assert sc["em"]["likely_matches"] > 0
    zones = s["eval_decisions"]["zones"]
    assert zones["auto_merge"]["pairs"] > 0                                      # obvious matches are no longer all sent to review
    assert zones["auto_merge"]["false"] <= max(2, 0.02 * zones["auto_merge"]["pairs"])
    assert s["eval_clusters"]["precision"] >= 0.97                               # synthetic data only: this is not real-world accuracy


def _csv_run(tmp_path, rows, fields, cols):
    f = tmp_path / "in.csv"
    with f.open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(cols)
        w.writerows(rows)
    cfg = {"sources": [{"source_id": "odd", "connector": {"type": "csv", "path": str(f)}, "record_id": "id", "fields": fields}]}
    (tmp_path / "s.json").write_text(json.dumps(cfg), encoding="utf-8")
    return run_pipeline(PipelineConfig(tmp_path / "s.json", tmp_path / "work"))


FULL = {"full_name": "name", "mobile": "phone", "email": "mail", "city": "city"}
COLS = ["id", "name", "phone", "mail", "city"]


def test_tiny_file_says_scores_are_not_meaningful_and_sends_everything_to_review(tmp_path):
    rows = [(1, "Rahul Sharma", "9876543210", "r@x.com", "Pune"), (2, "Rahul Sharma", "9876543210", "r@x.com", "Pune"), (3, "Anita Rao", "9811111111", "a@x.com", "Delhi")]
    s = _csv_run(tmp_path, rows, FULL, COLS)
    assert s["scoring"]["mode"] == "insufficient" and "too small" in s["scoring"]["mode_reason"]
    assert s["decisions"]["auto_merge"] == 0                                     # nothing is merged on scores that mean nothing


@pytest.mark.parametrize("rows", [[], [(1, "Only One", "9876543210", "o@x.com", "Pune")], [(1, "", "", "", ""), (2, "", "", "", "")]])
def test_degenerate_files_do_not_break_a_run(tmp_path, rows):
    try:
        s = _csv_run(tmp_path, rows, FULL, COLS)
    except Exception as e:                                                       # a refusal must be a clear message, never a crash dump
        assert str(e)
        return
    assert s["scoring"]["mode"] == "insufficient"


def test_names_only_file_completes(tmp_path):
    rows = [(i, f"Person Number{i % 40}", "", "", "") for i in range(120)]
    s = _csv_run(tmp_path, rows, {"full_name": "name"}, COLS)
    assert s["scoring"]["mode"] in ("insufficient", "unsupervised_em") and s["ingest"]["total"] == 120


def test_em_learns_that_agreeing_fields_mean_a_match():
    """A direct check of the learning step on made-up levels: pairs that agree on name and phone are matches, others are not."""
    rng = np.random.default_rng(1)
    n = 3000
    L = np.full((n, len(COMPARISONS)), -1, dtype=np.int16)
    match = rng.random(n) < 0.2
    for name, hi, lo in (("name", 7, 0), ("phone", 2, 0), ("email", 1, 0), ("gender", 1, 0)):
        agree = np.where(match, rng.random(n) < 0.9, rng.random(n) < 0.1)
        L[:, IX[name]] = np.where(agree, hi, lo)
    u = {c.name: [0.9] + [0.1 / (c.n_levels - 1)] * (c.n_levels - 1) for c in COMPARISONS}   # agreeing by chance is rare
    weights, bias, info = un.fit_em(L, u, un.fixed_levels({}, {}))
    assert weights["name"][7] > 1.0 > -1.0 > weights["name"][0]
    assert weights["phone"][2] > 0 > weights["phone"][0]
    assert info["likely_matches"] > 0.1 * n
