"""One command, whole pipeline: sources -> canonical -> standardised -> candidates -> scored -> decisions
-> person clusters -> households, with per-stage time and memory, and (dev only) ground-truth evaluation.

    python -m dedupe.pipeline --sources configs/synthetic_sources.json --work data/staging --truth data/synthetic

Privacy: after standardisation the raw Aadhaar column of the canonical file is erased (only the keyed token and the
last four digits survive downstream). Set DEDUPE_TOKEN_KEY (and DEDUPE_ENV=production to enforce it).
"""
import argparse
import dataclasses
import json
import os
import shutil
import subprocess  # nosec B404
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import pyarrow as pa

from dedupe import crypto

from . import incremental as inc
from .blocking import DEFAULT_RULES, rules_by_name
from .blocking import generate as block
from .clustering import ClusterConfig, cluster
from .clustering.judged import audit, constraints_from_store, drop_vetoes_overridden_by_stewards
from .clustering.run import load_inputs, predictions, write_outputs
from .decision import decide
from .governance import MatchingSettings, resolve
from .governance.store import canonical as canonical_config
from .governance.store import content_hash
from .households.group import group_households  # noqa: F401  (re-exported for convenience)
from .households.run import build_households, truth_of_clusters
from .households.run import evaluate as evaluate_households
from .ingest import ingest
from .review import build_queue
from .scoring import score, train
from .scoring.__main__ import load_tables
from .scoring.levels import materialize, prepare_tf_tables
from .scoring.model import Model
from .standardise.run import run as standardise
from .standardise.run import source_report
from .store import JudgmentStore
from .survivorship import build_golden


@dataclass
class PipelineConfig:
    sources: Path | None   # the sources file; None when the config store holds an active `sources` version
    work: Path = Path("data/staging")
    decision_config: Path = Path("configs/decision_default.json")
    survivorship_config: Path = Path("configs/survivorship_default.json")
    judgments_url: str | None = None  # steward decision store (SQLAlchemy URL); None = ignore stewards (see dedupe.store)
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
    config_store_url: str | None = None  # versioned config store (SQLAlchemy URL): its ACTIVE versions override the config files and settings
    blocking_rules: list | None = None  # names of the blocking rules to use (None = the defaults)
    registry_dir: Path | None = None  # person-ID registry kept across runs (None = <work>/identity)
    model_path: Path | None = None  # where the fitted model is saved (None = <work>/../models/fs_model.json)
    previous_run: Path | None = None  # an earlier successful run of the same data: reuse its work for unchanged records (see dedupe.incremental)
    config_versions: dict | None = None  # name -> version chosen when the run was started (decision, survivorship, settings); else the active ones


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
    crypto.connect().execute(f"COPY (SELECT * REPLACE (NULL::VARCHAR AS aadhaar) FROM {crypto.rp(canonical)}) TO '{tmp}' ({crypto.fmt()})")
    tmp.replace(canonical)


def _git_commit() -> str | None:
    try:
        git = shutil.which("git")
        if not git:
            return None
        out = subprocess.run([git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)  # nosec B603
        return out.stdout.strip() or None
    except Exception:
        return None


def run_pipeline(cfg: PipelineConfig) -> dict:
    meter = _Meter()
    work = cfg.work
    crypto.require_in_production(os.environ.get("DEDUPE_ENV", "dev").strip().lower())
    work.mkdir(parents=True, exist_ok=True)
    registry = Path(cfg.registry_dir) if cfg.registry_dir else work / "identity"
    crypto.bind_work(work, extra=(registry,))       # refuses a missing, wrong or mixed key before anything is written
    p = {k: work / v for k, v in {"canonical": "canonical.parquet", "std": "standardised.parquet", "cand": "candidates.parquet",
                                  "scored": "scored_pairs.parquet", "decisions": "decisions.parquet", "model": "../models/fs_model.json"}.items()}
    if cfg.model_path:
        p["model"] = Path(cfg.model_path)
    clusters_dir, hh_dir = work / "clusters", work / "households"
    summary: dict = {"git_commit": _git_commit(), "sources_config": str(cfg.sources) if cfg.sources else None,
                     "encryption_at_rest": {"enabled": crypto.enabled(), "key_id": crypto.active().key_id if crypto.enabled() else None}}
    res_cfg = resolve(cfg.config_store_url, cfg.decision_config, cfg.survivorship_config, cfg.config_versions)
    dcfg, scfg = res_cfg.decision, res_cfg.survivorship
    if res_cfg.settings:   # an active settings version in the store wins over the values passed in
        cfg = dataclasses.replace(cfg, **res_cfg.settings.model_dump())
    effective = {k: getattr(cfg, k) for k in MatchingSettings.model_fields}
    effective["blocking_rules"] = effective["blocking_rules"] or [r.name for r in DEFAULT_RULES]
    summary["configs"] = {**res_cfg.used, "effective_settings": {**effective, "hash": content_hash(effective)}}
    used_dir = work / "config_used"
    used_dir.mkdir(exist_ok=True)
    for n, content in {**res_cfg.contents, "settings": effective}.items():
        (used_dir / f"{n}.json").write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")

    if res_cfg.sources:         # sources managed through the API/UI: the ACTIVE version is what runs, and the exact content is kept with the run
        sources_path = used_dir / "sources.json"
        sources_path.write_text(json.dumps(res_cfg.sources, indent=2, ensure_ascii=False), encoding="utf-8")
    elif cfg.sources:
        sources_path = cfg.sources
        file_content = canonical_config("sources", json.loads(Path(cfg.sources).read_text(encoding="utf-8")))
        summary["configs"]["sources"] = {"source": "file", "version": None, "hash": content_hash(file_content), "path": str(cfg.sources)}
    else:
        raise ValueError("no sources configured: pass --sources, or activate a `sources` version in the config store")
    tmp = work / ".duckdb_tmp"
    con = crypto.connect({"memory_limit": cfg.memory_limit, "temp_directory": str(tmp)})
    summary["fingerprints"] = inc.fingerprints()
    prev = Path(cfg.previous_run) if cfg.previous_run else None
    reuse, why_not = inc.check(prev, prev / "model.json" if prev else None, summary["configs"]["effective_settings"]["hash"])
    with meter.stage("ingest"):
        summary["ingest"] = ingest(sources_path, p["canonical"], cfg.batch_size)
        inc.write_hashes(con, p["canonical"], work / inc.HASHES)          # before Aadhaar is erased: the fingerprint of the record as read
    incr = {"previous_run": prev.name if prev else None, "used": reuse, "reason": why_not}
    with meter.stage("standardise"):
        if reuse:
            d = inc.delta(con, prev, work / inc.HASHES)
            changed, fresh = work / "changed_canonical.parquet", work / "changed_standardised.parquet"
            inc.split_canonical(con, p["canonical"], changed)
            standardise(changed, fresh, cfg.batch_size)
            inc.merge_standardised(con, prev, fresh, work / inc.HASHES, p["std"])
            changed.unlink()
            fresh.unlink()
            summary["standardise"] = {**source_report(p["std"]), "output": str(p["std"]), "reused_rows": d["unchanged"]}
            incr["records"] = d
        else:
            summary["standardise"] = standardise(p["canonical"], p["std"], cfg.batch_size)
    if cfg.redact_aadhaar:
        with meter.stage("redact_aadhaar"):
            redact_aadhaar(p["canonical"])
    with meter.stage("blocking"):
        gen = block(p["std"], p["cand"], cap=cfg.blocking_cap, con=con, rules=rules_by_name(cfg.blocking_rules))
        summary["blocking"] = {"records": gen["records"], "candidate_pairs": gen["candidate_pairs"], "all_pairs": gen["all_pairs"]}
        _drop(con, "rule_pairs", "k", "blocks")
    with meter.stage("scoring"):
        load_tables(con, p["std"], p["cand"])
        moved = (incr["records"]["new"] + incr["records"]["changed"] + incr["records"]["removed"]) / max(1, incr["records"]["unchanged"] + incr["records"]["changed"] + incr["records"]["removed"]) if reuse else 1.0
        prev_mode = (json.loads((prev / "pipeline_summary.json").read_text(encoding="utf-8")).get("scoring") or {}).get("mode") if reuse else None
        if reuse and moved <= inc.MAX_CHANGE_FOR_MODEL and prev_mode in ("id_anchored", "unsupervised_em"):
            model = Model.load(prev / "model.json")
            model.meta = {**model.meta, "reused_from_run": prev.name}
            incr["pairs_reused"] = inc.reusable_pairs(con, prev, p["cand"].with_suffix(".index.parquet"))
            prepare_tf_tables(con)
            materialize(con, "todo", "cand_levels", with_tf=True)
            fresh = work / "new_scored_pairs.parquet"
            incr["pairs_scored"] = score(con, model, fresh)["pairs"]
            inc.combine_scores(con, fresh, p["scored"])
            fresh.unlink()
            incr["model"] = "reused"
            _drop(con, "reused", "todo", "changed_keys", "h_new", "h_old")
        else:
            model = train(con)
            score(con, model, p["scored"])
            incr["model"] = "learned"
            if reuse:
                incr["model_reason"] = (f"{moved:.0%} of records changed (more than {inc.MAX_CHANGE_FOR_MODEL:.0%})" if moved > inc.MAX_CHANGE_FOR_MODEL
                                        else "the earlier run's scores were not meaningful")
        model.save(p["model"])
        summary["incremental"] = incr
        summary["scoring"] = {"seed_pairs": model.meta["seed_pairs"], "bias_bits": round(model.bias / 0.6931, 2), "mode": model.meta["scoring_mode"],
                              "mode_reason": model.meta["scoring_mode_reason"], "em": model.meta["em"]}
        _drop(con, "cand_levels", "candidates", "rand_pairs", *_tables_like(con, "tf_"))
    with meter.stage("decisions"):
        summary["decisions"] = decide(con, p["scored"], dcfg, p["decisions"])
        _drop(con, "scored", "wide", "flagged")
    with meter.stage("clustering"):
        nodes, edges, cannot, ids = load_inputs(con, p["std"], p["cand"], p["decisions"], dcfg)
        cons = None
        if cfg.judgments_url:
            cons = constraints_from_store(con, JudgmentStore.open(cfg.judgments_url))
            cannot = drop_vetoes_overridden_by_stewards(cannot, cons) + cons.cannot
            edges = list(edges) + cons.must
            for rid in cons.neutralise:
                ids[rid] = {}
        res = cluster(nodes, edges, ids, cannot, ClusterConfig(max_cluster_size=cfg.max_cluster_size), guard=cfg.cluster_guard,
                      separations=cons.separations if cons else None)
        if cons:
            rep = audit(cons, res.labels, ids)
            summary["judgments"] = {**cons.summary(), "held": rep["held"], "not_held": rep["not_held"]}
            clusters_dir.mkdir(parents=True, exist_ok=True)
            (clusters_dir / "judgment_report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
            con.register("judged_src", pa.table({"l_rid": pa.array([a for a, _ in cons.judged_pairs], pa.int64()),
                                                 "r_rid": pa.array([b for _, b in cons.judged_pairs], pa.int64())}))
            con.execute("CREATE OR REPLACE TABLE judged_pairs AS SELECT * FROM judged_src")
        summary["identity"] = write_outputs(con, res, clusters_dir, registry, p["scored"])
        summary["clustering"] = res.stats
        del nodes, edges, cannot, ids
        _drop(con, "cluster_flags")
    with meter.stage("golden_record"):
        overrides = [{"id": j.id, "field": j.subject["field"], "value": j.subject["value"], "anchors": j.subject["anchors"]}
                     for j in JudgmentStore.open(cfg.judgments_url).active(("field_override",))] if cfg.judgments_url else []
        summary["golden_record"] = build_golden(con, p["std"], p["canonical"], clusters_dir / "clusters.parquet",
                                                scfg, work / "golden", overrides)
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
            pred = predictions(con, res)
            # score only records that were read: a source left out of this run (or rows it refused) must not count as missed matches
            absent = len(set(truth) - set(pred))
            truth = {k: v for k, v in truth.items() if k in pred}
            hh_truth = {k: v for k, v in hh_truth.items() if k in pred}
            summary["eval_clusters"] = {**{k: v for k, v in evaluate(pred, truth, hh_truth, traps).items() if k != "by_trap"},
                                        "truth_records_not_in_input": absent}
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
    ap.add_argument("--sources", type=Path, default=None, help="sources file; omit to use the active `sources` version of the config store")
    ap.add_argument("--work", type=Path, default=Path("data/staging"))
    ap.add_argument("--decision-config", type=Path, default=Path("configs/decision_default.json"))
    ap.add_argument("--survivorship-config", type=Path, default=Path("configs/survivorship_default.json"))
    ap.add_argument("--judgments-url", default=None, help="steward decision store (SQLAlchemy URL); omit to ignore steward decisions")
    ap.add_argument("--config-store-url", default=None, help="versioned config store (SQLAlchemy URL); its active versions override the config files")
    ap.add_argument("--database-from-env", action="store_true",
                    help="use DEDUPE_DATABASE_URL for BOTH the steward decisions and the config store (keeps credentials off the command line)")
    ap.add_argument("--registry-dir", type=Path, default=None, help="person-ID registry kept across runs (default: <work>/identity)")
    ap.add_argument("--model-path", type=Path, default=None, help="where to save the fitted model")
    ap.add_argument("--truth", type=Path, default=None, help="ground-truth folder (dev/synthetic only)")
    ap.add_argument("--cap", type=int, default=50)
    ap.add_argument("--household-threshold", type=float, default=5.0)
    ap.add_argument("--keep-raw-aadhaar", action="store_true", help="do NOT erase raw Aadhaar from the canonical file")
    ap.add_argument("--previous-run", type=Path, default=None, help="an earlier successful run of the same project: reuse its work for unchanged records")
    ap.add_argument("--config-versions", type=Path, default=None, help="JSON file: config name -> version to use instead of the active one")
    a = ap.parse_args()
    if a.database_from_env:
        url = os.environ.get("DEDUPE_DATABASE_URL")
        if not url:
            ap.error("--database-from-env needs DEDUPE_DATABASE_URL")
        a.judgments_url = a.config_store_url = url
    s = run_pipeline(PipelineConfig(a.sources, a.work, decision_config=a.decision_config, survivorship_config=a.survivorship_config,
                                    judgments_url=a.judgments_url, config_store_url=a.config_store_url, truth=a.truth, blocking_cap=a.cap, household_threshold=a.household_threshold,
                                    redact_aadhaar=not a.keep_raw_aadhaar, registry_dir=a.registry_dir, model_path=a.model_path,
                                    previous_run=a.previous_run,
                                    config_versions=json.loads(a.config_versions.read_text(encoding="utf-8")) if a.config_versions else None))
    print(_fmt(s))


if __name__ == "__main__":
    main()
