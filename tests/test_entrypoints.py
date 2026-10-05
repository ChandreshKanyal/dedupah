"""The command-line entry points have no other test: a changed config signature once broke both silently."""
import json
import os
import sys

from dedupe import benchmark, pipeline
from dedupe.synthetic import generate as gen_data


def test_pipeline_command_line_runs_end_to_end(tmp_path, monkeypatch, capsys):
    syn = tmp_path / "syn"
    gen_data(40, seed=97, out=syn)
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(syn / os.path.basename(s["connector"]["path"]))
    (tmp_path / "sources.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["dedupe.pipeline", "--sources", str(tmp_path / "sources.json"), "--work", str(tmp_path / "work"),
                                      "--truth", str(syn), "--judgments-url", f"sqlite:///{tmp_path / 'j.db'}"])
    pipeline.main()
    out = capsys.readouterr().out
    assert "person clusters (pairwise)" in out and (tmp_path / "work" / "golden" / "golden_records.parquet").exists()


def test_benchmark_runs_one_configuration():
    row = benchmark.run_one(40, 98, 1.0)
    assert 0.5 < row["f1"] <= 1.0 and row["records"] > 100
