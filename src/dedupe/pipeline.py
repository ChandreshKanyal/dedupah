"""One command, whole pipeline: sources -> canonical -> standardised -> candidates -> scored -> decisions
-> person clusters -> households, with per-stage time and memory, and (dev only) ground-truth evaluation.

    python -m dedupe.pipeline --sources configs/synthetic_sources.json --work data/staging --truth data/synthetic

Privacy: after standardisation the raw Aadhaar column of the canonical file is erased (only the keyed token and the
last four digits survive downstream). Set DEDUPE_TOKEN_KEY (and DEDUPE_ENV=production to enforce it).
"""
import argparse
import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import psutil

from .blocking import generate as block
from .clustering import ClusterConfig, cluster
from .clustering.run import load_inputs, predictions, write_outputs
from .decision import DecisionConfig, decide
from .households.group import group_households  # noqa: F401  (re-exported for convenience)
from .households.run import build_households, truth_of_clusters
from .households.run import evaluate as evaluate_households
from .ingest import ingest
from .review import build_queue
from .scoring import score, train
from .scoring.__main__ import load_tables
from .standardise.run import run as standardise


@dataclass
class PipelineConfig:
    sources: Path
    work: Path = Path("data/staging")
    decision_config: Path = Path("configs/decision_default.json")
    truth: Path | None = None  # ground-truth folder: development/synthetic evaluation only
    blocking_cap: int = 50
    household_threshold: float = 5.0
    max_cluster_size: int = 15
    redact_aadhaar: bool = True
    review_min_probability: float = 0.0  # drop queue items whose best pair scores below this (workload control)
    review_cap: int | None = None  # keep only the top N queue items
    cluster_guard: bool = True  # contradiction checks in clustering (off only for ablation studies)
    batch_size: int = 50_000
    memory_limit: str = "4GB"  # DuckDB working-memory cap; larger intermediate data spills to <work>/.duckdb_tmp on disk
    stages: list = field(default_factory=list)


class _Meter:
    """Wall time per stage plus process memory (sampled): peak within each stage, memory still held after it, overall peak."""

    def __init__(self):
        self.proc = psutil.Process()
        self.peak = 0
        self.stage_peak = 0
        self._stop = threading.Event()
        self.rows = []
        threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self):
        while not self._stop.is_set():
            rss = self.proc.memory_info().rss
            self.peak = max(self.peak, rss)
            self.stage_peak = max(self.stage_peak, rss)
            time.sleep(0.2)

    def stage(self, name):
        meter = self

        class _Ctx:
            def __enter__(self):
                self.t = time.perf_counter()
                meter.stage_peak = meter.proc.memory_info().rss

            def __exit__(self, *exc):
                rss = meter.proc.memory_info().rss
                meter.peak = max(meter.peak, rss)
                meter.rows.append({"stage": name, "seconds": round(time.perf_counter() - self.t, 2),
                                   "peak_memory_mb": round(max(meter.stage_peak, rss) / 2**20), "memory_after_mb": round(rss / 2**20)})
        return _Ctx()

    def stop(self):
        self._stop.set()


def redact_aadhaar(canonical: Path) -> None:
    """Erase raw Aadhaar numbers from the canonical file (data minimisation). Irreversible: re-ingest to recover."""
    tmp = canonical.with_suffix(".redacted.parquet")
    duckdb.sql(f"COPY (SELECT * REPLACE (NULL::VARCHAR AS aadhaar) FROM read_parquet('{canonical}')) TO '{tmp}' (FORMAT PARQUET)")
    tmp.replace(canonical)


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def run_pipeline(cfg: PipelineConfig) -> dict:
    meter = _Meter()
    work = cfg.work
    work.mkdir(parents=True, exist_ok=True)
    p = {k: work / v for k, v in {"canonical": "canonical.parquet", "std": "standardised.parquet", "cand": "candidates.parquet",
                                  "scored": "scored_pairs.parquet", "decisions": "decisions.parquet", "model": "../models/fs_model.json"}.items()}
    clusters_dir, hh_dir = work / "clusters", work / "households"
    summary: dict = {"git_commit": _git_commit(), "sources_config": str(cfg.sources)}
    dcfg = DecisionConfig.load(cfg.decision_config)

    with meter.stage("ingest"):
        summary["ingest"] = ingest(cfg.sources, p["canonical"], cfg.batch_size)
    with meter.stage("standardise"):
        summary["standardise"] = standardise(p["canonical"], p["std"], cfg.batch_size)
    if cfg.redact_aadhaar:
        with meter.stage("redact_aadhaar"):
            redact_aadhaar(p["canonical"])
    tmp = work / ".duckdb_tmp"
    con = duckdb.connect(config={"memory_limit": cfg.memory_limit, "temp_directory": str(tmp)})
    with meter.stage("blocking"):
        gen = block(p["std"], p["cand"], cap=cfg.blocking_cap, con=con)
        summary["blocking"] = {"records": gen["records"], "candidate_pairs": gen["candidate_pairs"], "all_pairs": gen["all_pairs"]}
        _drop(con, "rule_pairs", "k", "blocks")
    with meter.stage("scoring"):
        load_tables(con, p["std"], p["cand"])
        model = train(con)
        model.save(p["model"])
        score(con, model, p["scored"])
        summary["scoring"] = {"seed_pairs": model.meta["seed_pairs"], "bias_bits": round(model.bias / 0.6931, 2)}
        _drop(con, "cand_levels", "candidates", "rand_pairs", *_tables_like(con, "tf_"))
    with meter.stage("decisions"):
        summary["decisions"] = decide(con, p["scored"], dcfg, p["decisions"])
        _drop(con, "scored", "wide", "flagged")
    with meter.stage("clustering"):
        nodes, edges, cannot, ids = load_inputs(con, p["std"], p["cand"], p["decisions"], dcfg)
        res = cluster(nodes, edges, ids, cannot, ClusterConfig(max_cluster_size=cfg.max_cluster_size), guard=cfg.cluster_guard)
        write_outputs(con, res, clusters_dir)
        summary["clustering"] = res.stats
        del nodes, edges, cannot, ids
        _drop(con, "cluster_flags")
    with meter.stage("review_queue"):
        summary["review_queue"] = build_queue(con, work / "review_queue.parquet", cfg.review_min_probability, cfg.review_cap)
    with meter.stage("households"):
        people, scores, hh, info, _ = build_households(con, p["std"], clusters_dir / "clusters.parquet", p["canonical"],
                                                       hh_dir, cfg.household_threshold)
        summary["households"] = {"people": len(people), "related_pairs": len(scores), **{k: v for k, v in info.items() if k != "flags"}}

    if cfg.truth:
        with meter.stage("evaluation"):
            from .decision.report import impact
            from .evaluation.loaders import load_truth
            from .evaluation.metrics import evaluate
            truth, hh_truth, traps = load_truth(cfg.truth)
            summary["eval_clusters"] = {k: v for k, v in evaluate(predictions(con, res), truth, hh_truth, traps).items() if k != "by_trap"}
            imp = impact(con, cfg.truth, dcfg)
            summary["eval_decisions"] = {"zones": imp["zones"], "auto_merge_quality": imp["auto_merge_quality"]}
            thh, tper = truth_of_clusters(con, cfg.truth)
            e = [(a, b, s) for a, b, s, _ in scores]
            fam = evaluate_households(hh, thh, e, {(a, b): ev for a, b, _, ev in scores}, tper)
            summary["eval_households"] = {"family_only": fam["family_only"], "exact_rate": fam["exact_rate"]}
    meter.stop()
    con.close()
    shutil.rmtree(tmp, ignore_errors=True)
    summary["stages"] = meter.rows
    summary["total_seconds"] = round(sum(r["seconds"] for r in meter.rows), 2)
    summary["peak_memory_mb"] = round(meter.peak / 2**20)
    (work / "pipeline_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def _drop(con, *tables: str) -> None:
    """Free a finished stage's working tables (they are already saved as parquet where needed)."""
    for t in tables:
        con.execute(f"DROP TABLE IF EXISTS {t}")


def _tables_like(con, prefix: str) -> list[str]:
    return [r[0] for r in con.execute("SELECT table_name FROM duckdb_tables() WHERE starts_with(table_name, ?)", [prefix]).fetchall()]


def _fmt(s: dict) -> str:
    lines = [f"records {s['ingest']['total']:,} | candidates {s['blocking']['candidate_pairs']:,} | decisions {s['decisions']['auto_merge']:,} auto / "
             f"{s['decisions']['review']:,} review / {s['decisions']['reject']:,} reject | clusters {s['clustering']['clusters']:,} | "
             f"households {s['households']['households']:,}"]
    lines.append("stage times: " + ", ".join(f"{r['stage']} {r['seconds']}s" for r in s["stages"]) + f" | total {s['total_seconds']}s | peak memory {s['peak_memory_mb']} MB")
    if "eval_clusters" in s:
        c, d, h = s["eval_clusters"], s["eval_decisions"], s["eval_households"]["family_only"]
        lines.append(f"person clusters (pairwise): precision {c['precision']:.4f} recall {c['recall']:.4f} F1 {c['f1']:.4f} | "
                     f"auto-merge precision {d['auto_merge_quality']['precision']:.4f} ({d['zones']['auto_merge']['false']} false merges), review queue {d['zones']['review']['pairs']:,} pairs")
        lines.append(f"households (family links only): precision {h['precision']:.4f} recall {h['recall']:.4f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=Path("data/staging"))
    ap.add_argument("--decision-config", type=Path, default=Path("configs/decision_default.json"))
    ap.add_argument("--truth", type=Path, default=None, help="ground-truth folder (dev/synthetic only)")
    ap.add_argument("--cap", type=int, default=50)
    ap.add_argument("--household-threshold", type=float, default=5.0)
    ap.add_argument("--keep-raw-aadhaar", action="store_true", help="do NOT erase raw Aadhaar from the canonical file")
    a = ap.parse_args()
    s = run_pipeline(PipelineConfig(a.sources, a.work, a.decision_config, a.truth, a.cap, a.household_threshold,
                                    redact_aadhaar=not a.keep_raw_aadhaar))
    print(_fmt(s))


if __name__ == "__main__":
    main()
