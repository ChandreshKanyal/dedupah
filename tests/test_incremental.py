"""Incremental runs: unchanged records reuse the previous run's work; people are still formed from all records; reuse is refused when unsafe."""
import csv
import json
import os
import shutil
from pathlib import Path

import duckdb
import pytest
from api_world import PROJECT
from test_runs_queue_health import start, world, writing_summary

from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.synthetic import generate as gen_data
from dedupe.workspace.runs import PREVIOUS_RUN, pipeline_command


def _sources(tmp_path, syn):
    cfg = json.loads(Path("configs/synthetic_sources.json").read_text(encoding="utf-8"))
    for s in cfg["sources"]:
        s["connector"]["path"] = str(syn / os.path.basename(s["connector"]["path"]))
    (tmp_path / "s.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp_path / "s.json"


def _run(tmp_path, src, name, prev=None, **kw):
    w = tmp_path / name
    return run_pipeline(PipelineConfig(src, w, model_path=w / "model.json", registry_dir=tmp_path / f"reg_{name}", previous_run=prev, **kw))


def _together(work: Path) -> set:
    rows = duckdb.sql(f"""SELECT a.source_id || '/' || a.source_record_id, b.source_id || '/' || b.source_record_id
        FROM '{work / 'clusters' / 'clusters.parquet'}' a JOIN '{work / 'clusters' / 'clusters.parquet'}' b USING (cluster_id)
        WHERE (a.source_id, a.source_record_id) < (b.source_id, b.source_record_id)""").fetchall()
    return set(rows)


@pytest.fixture(scope="module")
def first(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("incr")
    syn = tmp / "syn"
    gen_data(60, seed=77, out=syn)
    src = _sources(tmp, syn)
    s = _run(tmp, src, "r1", truth=syn)
    return tmp, syn, src, s


def test_nothing_changed_reuses_everything_and_gives_the_same_people(first):
    tmp, syn, src, s1 = first
    s2 = _run(tmp, src, "same", prev=tmp / "r1", truth=syn)
    inc = s2["incremental"]
    assert inc["used"] and inc["model"] == "reused" and inc["pairs_scored"] == 0
    assert inc["records"]["unchanged"] == s1["ingest"]["total"] and inc["records"]["new"] == inc["records"]["changed"] == inc["records"]["removed"] == 0
    assert _together(tmp / "same") == _together(tmp / "r1")
    assert s2["standardise"]["profile"] == s1["standardise"]["profile"]          # the data-quality report covers reused rows too


def test_changed_new_and_removed_records_are_handled(first, tmp_path):
    tmp, syn, _, _ = first
    syn2 = tmp_path / "syn"
    shutil.copytree(syn, syn2)
    crm = syn2 / "bank_crm.csv"
    rows = list(csv.DictReader(crm.open(encoding="utf-8")))
    rows[0]["mobile"] = "9000011111"                                          # changed
    new = dict(rows[1], cust_id="NEW-1")                                      # a copy of an existing customer under a new id
    with crm.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows([r for r in rows if r is not rows[2]] + [new])            # one removed
    src = _sources(tmp_path, syn2)
    s = _run(tmp_path, src, "r2", prev=tmp / "r1", truth=syn2)
    inc = s["incremental"]
    assert inc["used"] and inc["records"]["new"] == 1 and inc["records"]["changed"] == 1 and inc["records"]["removed"] == 1
    assert inc["pairs_scored"] > 0 and inc["pairs_reused"] > 0
    together = _together(tmp_path / "r2")
    assert any("bank_crm/NEW-1" in pair and f"bank_crm/{rows[1]['cust_id']}" in pair for pair in together)   # the new copy joins its person
    assert not any(f"bank_crm/{rows[2]['cust_id']}" in pair for pair in together)                          # the removed record is gone
    full = _run(tmp_path, src, "r2full", truth=syn2)
    # as good as processing everything again (a full run also re-learns the model, so the two are not identical); synthetic data only
    for k in ("precision", "recall"):
        assert s["eval_clusters"][k] >= full["eval_clusters"][k] - 0.03, (k, s["eval_clusters"][k], full["eval_clusters"][k])


def test_reuse_is_refused_when_unsafe(first, tmp_path):
    tmp, _, src, _ = first
    other = _run(tmp_path, src, "cap", prev=tmp / "r1", blocking_cap=7)
    assert not other["incremental"]["used"] and "settings" in other["incremental"]["reason"]
    broken = tmp_path / "old"
    shutil.copytree(tmp / "r1", broken)
    (broken / "record_hashes.parquet").unlink()                                # a run from before incremental runs existed
    s = _run(tmp_path, src, "r3", prev=broken)
    assert not s["incremental"]["used"] and "predates" in s["incremental"]["reason"]


def test_the_portal_hands_the_latest_successful_run_over_unless_a_full_rerun_is_asked(tmp_path):
    c, s, sv = world(tmp_path, writing_summary({"crm": 3}))
    a = start(c, s).json()
    assert sv.jobs.wait(PROJECT, a["id"], 30).status == "succeeded"
    b = start(c, s).json()
    sv.jobs.wait(PROJECT, b["id"], 30)
    handed = sv.workspace.run_dir(PROJECT, b["id"]) / PREVIOUS_RUN
    assert handed.read_text(encoding="utf-8") == str(sv.workspace.run_dir(PROJECT, a["id"]))
    assert pipeline_command(handed.parent, handed.parent / "sources.json", tmp_path)[-2:] == ["--previous-run", handed.read_text(encoding="utf-8")]
    f = start(c, s, full_rerun=True).json()
    sv.jobs.wait(PROJECT, f["id"], 30)
    assert not (sv.workspace.run_dir(PROJECT, f["id"]) / PREVIOUS_RUN).exists()
