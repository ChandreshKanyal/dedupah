"""Use the engine from Python or a terminal, without the portal: point it at files, get the duplicates, golden records and review pairs back.

    from dedupe import sdk
    found = sdk.inspect("crm.csv")                       # what each column holds and which field it would fill
    res = sdk.run(["crm.csv", "loans.xlsx"], out="out", preset="balanced", fmt="xlsx")
    res.summary                                          # records, people, merged, unmatched, pairs for a person, files written
    res.explain("P000000012")                            # why that person's records were joined

The same from a terminal: `dedupe inspect`, `dedupe init`, `dedupe run`, `dedupe explain` (see dedupe.cli). Everything runs on this
machine: nothing is sent anywhere. Columns are mapped automatically from their names and values (the portal's mapping); a job file
(`dedupe init`) fixes the mapping, rules and outputs when the automatic choice is not what you want.
"""
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dedupe import crypto, defaults
from dedupe.connectors.files import FILE_SUFFIX
from dedupe.mapping.check import inspect_source

OUTPUTS = {
    "golden": "one clean record per person: the best value of every field, how many source records built it and from which sources",
    "map": "every source record with the person id it belongs to (carry the id back into your own systems)",
    "review": "pairs of people the engine was unsure about, for a person to decide",
    "clean": "every source record as cleaned for matching (names, dates, phones, PAN... in one standard form), with its person id",
    "links": "every merge between two records: the rule that made it or the match score, and the other rules that also held",
}
FORMATS = ("csv", "xlsx", "parquet", "json")
_SUFFIX_FORMAT = {s: f for f, ss in FILE_SUFFIX.items() for s in ss if f not in ("tsv", "fixed_width")}
_SUFFIX_FORMAT[".tsv"] = "tsv"
CLEAN_COLUMNS = ("source_id", "source_record_id", "name_clean", "father_name_clean", "dob", "gender", "mobile", "mobile_alt", "email", "pan",
                 "aadhaar_last4", "passport_no", "dl_no", "voter_id", "customer_number", "address_norm", "address_pin", "city", "state",
                 "invalid_fields", "oddities")


def _lit(path: Path) -> str:
    """A file path as a SQL string literal (forward slashes, quotes doubled): safe for any folder name, on Windows too."""
    return "'" + Path(path).as_posix().replace("'", "''") + "'"


class SdkError(ValueError):
    """Something the person can fix: an unknown file type, a mapping that names a missing column, an unknown output."""


def _source_id(path: Path, taken: set) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_")[:30] or "source"
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}_{n}", n + 1
    taken.add(sid)
    return sid


def connector_for(path: str | Path, sheet: str | None = None) -> dict:
    """The connector setting that reads one local file, chosen by its extension."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SdkError(f"no such file: {path}")
    fmt = _SUFFIX_FORMAT.get(p.suffix.lower())
    if fmt is None:
        raise SdkError(f"{p.name}: unknown file type; use one of {', '.join(sorted(_SUFFIX_FORMAT))}")
    return {"type": fmt, "path": str(p), **({"sheet": sheet} if sheet else {})}


def inspect(path: str | Path, sheet: str | None = None) -> dict:
    """What the engine finds in a file: its columns, the type each one's values look like, the field each would fill (with confidence and
    whether the column name, the values or both said so) and the column that looks like each row's own id. No values are returned."""
    found = inspect_source(connector_for(path, sheet))
    return {"file": str(path), "columns": found["columns"], "record_id": found["record_id"],
            "mapping": {s["field"]: {"column": s["column"], "confidence": s["confidence"], "why": s["why"]} for s in found["suggestions"]},
            "types": {c: (d.get("description") or d.get("type")) for c, d in found["types"].items()},
            "unused": [c for c in found["columns"] if c not in {s["column"] for s in found["suggestions"]} and c != found["record_id"]]}


def job_for(files: list, preset: str = "balanced", fmt: str = "csv", outputs: tuple = ("golden", "map", "review")) -> dict:
    """A complete job (sources with their mapping, rules, outputs) from the automatic mapping, to save, edit and run again."""
    taken: set = set()
    sources = []
    for f in files:
        f, sheet = (f["path"], f.get("sheet")) if isinstance(f, dict) else (f, None)
        found = inspect(f, sheet)
        src = {"source_id": _source_id(Path(f), taken), "connector": connector_for(f, sheet), "record_id": found["record_id"],
               "fields": {k: v["column"] for k, v in found["mapping"].items()}, "attributes": []}
        sources.append(src)
    return {"sources": sources, "preset": preset, "format": fmt, "outputs": list(outputs)}


@dataclass
class Result:
    out: Path
    work: Path
    summary: dict
    files: dict = field(default_factory=dict)

    def table(self, name: str) -> pa.Table:
        """An output as a table (read from what was written; parquet is used when the files are another format)."""
        return pq.read_table(self.work / "sdk_outputs" / f"{name}.parquet")

    def explain(self, person_id: str) -> dict:
        return explain(self.out, person_id)


def _validate_job(job: dict) -> tuple[list, str, str, list]:
    sources = job.get("sources") or []
    if not sources:
        raise SdkError("the job names no files")
    preset = job.get("preset", "balanced")
    if preset not in defaults.PRESETS:
        raise SdkError(f"unknown preset {preset!r}; choose one of: {', '.join(defaults.PRESETS)}")
    fmt = job.get("format", "csv")
    if fmt not in FORMATS:
        raise SdkError(f"unknown format {fmt!r}; choose one of: {', '.join(FORMATS)}")
    outputs = list(job.get("outputs") or ["golden", "map", "review"])
    bad = [o for o in outputs if o not in OUTPUTS]
    if bad:
        raise SdkError(f"unknown output {bad[0]!r}; choose from: {', '.join(OUTPUTS)}")
    for s in sources:
        cols = set(inspect_source(s["connector"], sample_rows=1)["columns"])
        named = [c for c in s.get("fields", {}).values() if isinstance(c, str)] + ([s["record_id"]] if s.get("record_id") else [])
        missing = [c for c in named if c not in cols]
        if missing:
            raise SdkError(f"source {s['source_id']}: the mapping names columns the file does not have: {', '.join(missing)}")
    return sources, preset, fmt, outputs


def run(files=None, out: str | Path = "dedupe_out", preset: str = "balanced", fmt: str = "csv", outputs=("golden", "map", "review"),
        job: dict | str | Path | None = None, rules: str | Path | None = None, golden_rules: str | Path | None = None, quiet: bool = True) -> Result:
    """Deduplicate files (or a job file from `job_for` / `dedupe init`) into the folder `out`. Returns the summary and the files written.
    `rules` / `golden_rules`: your own matching or golden-record rules (JSON, as the portal's "For experts" shows them) instead of the shipped ones."""
    if job is None:
        if not files:
            raise SdkError("give the files to deduplicate, or a job file")
        job = job_for(list(files) if isinstance(files, (list, tuple)) else [files], preset, fmt, tuple(outputs))
    elif not isinstance(job, dict):
        job = json.loads(Path(job).read_text(encoding="utf-8"))
    sources, preset, fmt, outputs = _validate_job(job)
    out = Path(out).expanduser().resolve()
    work = out / "work"
    if work.exists():
        shutil.rmtree(work)                       # a run starts clean: an earlier run's engine files would be reused otherwise
    work.mkdir(parents=True)
    (work / "sources.json").write_text(json.dumps({"sources": sources}, indent=2), encoding="utf-8")
    dec = json.loads(Path(rules).read_text(encoding="utf-8")) if rules else (job.get("rules") or defaults.decision(preset))
    gold = json.loads(Path(golden_rules).read_text(encoding="utf-8")) if golden_rules else (job.get("golden_rules") or defaults.survivorship())
    (work / "decision.json").write_text(json.dumps(dec, indent=2), encoding="utf-8")
    (work / "survivorship.json").write_text(json.dumps(gold, indent=2), encoding="utf-8")
    from dedupe.pipeline import PipelineConfig, run_pipeline
    cfg = PipelineConfig(work / "sources.json", work / "run", decision_config=work / "decision.json", survivorship_config=work / "survivorship.json",
                         model_path=work / "model.json", registry_dir=out / "identity", build_households=False)
    if quiet:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            s = run_pipeline(cfg)
    else:
        s = run_pipeline(cfg)
    files_written = _write_outputs(work / "run", out, work / "sdk_outputs", fmt, outputs)
    c = s.get("clustering") or {}
    summary = {"records": (s.get("ingest") or {}).get("total"), "records_by_source": (s.get("ingest") or {}).get("records"),
               "people": c.get("clusters"), "built_from_several_records": (c.get("clusters") or 0) - (c.get("singletons") or 0),
               "no_match": c.get("singletons"), "merged_pairs": (s.get("decisions") or {}).get("auto_merge"),
               "pairs_for_a_person": (s.get("review_queue") or {}).get("queue_items"), "preset": preset,
               "rules_not_applied": (s.get("decisions") or {}).get("rules_not_applied") or [],
               "rejected_values": {k: v.get("invalid") for k, v in ((s.get("standardise") or {}).get("profile") or {}).items()},
               "seconds": s.get("total_seconds"), "files": files_written}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return Result(out, work, summary, files_written)


def _service(run_dir: Path):
    from dedupe.review.service import ReviewService
    from dedupe.store import JudgmentStore
    return ReviewService(run_dir, JudgmentStore.open("sqlite:///:memory:"))


def _write_outputs(run_dir: Path, out: Path, keep: Path, fmt: str, outputs: list) -> dict:
    """Each asked-for output as a parquet in `keep` (for Result.table and explain) and as a file in the asked format in `out`."""
    keep.mkdir(parents=True, exist_ok=True)
    con = crypto.connect()
    rp = crypto.rp
    sql = {
        "golden": f"SELECT cluster_id AS person_id, * EXCLUDE (cluster_id, aadhaar_token) FROM {rp(run_dir / 'golden' / 'golden_records.parquet')} ORDER BY 1",
        "map": f"SELECT cluster_id AS person_id, source_id, source_record_id FROM {rp(run_dir / 'clusters' / 'clusters.parquet')} ORDER BY 1, 2, 3",
        "clean": None, "review": None, "links": None,
    }
    have = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rp(run_dir / 'standardised.parquet')}").fetchall()}
    cols = ", ".join(f"s.{c}" for c in CLEAN_COLUMNS if c in have)
    sql["clean"] = (f"SELECT c.cluster_id AS person_id, {cols} FROM {rp(run_dir / 'standardised.parquet')} s "
                    f"JOIN {rp(run_dir / 'clusters' / 'clusters.parquet')} c USING (source_id, source_record_id) ORDER BY 1, 2, 3")
    written = {}
    for name in outputs:
        target = keep / f"{name}.parquet"
        if name == "review":
            items = _service(run_dir).queue(limit=1_000_000, status="all")
            keys = ["cluster_l", "name_l", "cluster_r", "name_r", "top_probability", "forced", "size_l", "size_r", "record_pairs"]
            t = pa.table({k: [i.get(k) for i in items] for k in keys}).rename_columns(
                ["person_a", "name_a", "person_b", "name_b", "match_score", "sent_by_a_rule", "records_a", "records_b", "record_pairs"])
            pq.write_table(t, target)
        elif name == "links":
            pq.write_table(_all_links(run_dir), target)
        else:
            con.execute(f"COPY ({sql[name]}) TO {_lit(target)} (FORMAT PARQUET)")
        dest = out / f"{name}.{'xlsx' if fmt == 'xlsx' else fmt}"
        if fmt == "parquet":
            shutil.copyfile(target, dest)
        elif fmt == "csv":
            con.execute(f"COPY (SELECT * FROM {_lit(target)}) TO {_lit(dest)} (HEADER, DELIMITER ',')")
        elif fmt == "json":
            con.execute(f"COPY (SELECT * FROM {_lit(target)}) TO {_lit(dest)} (FORMAT JSON, ARRAY true)")
        else:
            _xlsx(pq.read_table(target), dest, name)
        written[name] = str(dest)
    return written


def _all_links(run_dir: Path) -> pa.Table:
    con = crypto.connect()
    rp = crypto.rp
    rows = con.execute(f"""SELECT a.cluster_id, a.source_id, a.source_record_id, b.source_id, b.source_record_id,
            CASE WHEN d.decided_by = 'score' THEN 'match score' ELSE 'rule' END, nullif(d.decided_by, 'score'), d.reason, round(d.probability, 4),
            array_to_string(list_filter(d.fired, x -> x <> d.decided_by), '; ')
        FROM {rp(run_dir / 'decisions.parquet')} d JOIN {rp(run_dir / 'clusters' / 'clusters.parquet')} a ON a.rid = d.l_rid
        JOIN {rp(run_dir / 'clusters' / 'clusters.parquet')} b ON b.rid = d.r_rid
        WHERE d.decision = 'auto_merge' AND a.cluster_id = b.cluster_id ORDER BY 1, 2, 3""").fetchall()
    names = ["person_id", "source_a", "record_a", "source_b", "record_b", "joined_by", "rule", "reason", "match_score", "also_true"]
    return pa.table({n: [r[i] for r in rows] for i, n in enumerate(names)})


def _xlsx(t: pa.Table, dest: Path, name: str) -> None:
    from openpyxl import Workbook
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(name[:31])
    ws.append(t.column_names)
    for row in t.to_pylist():
        ws.append([", ".join(map(str, v)) if isinstance(v, list) else v for v in row.values()])
    wb.save(dest)


def explain(out: str | Path, person_id: str) -> dict:
    """Why the records of one person (from a finished `run` into `out`) were joined: each link with its rule or score."""
    run_dir = Path(out).expanduser().resolve() / "work" / "run"
    if not run_dir.exists():
        raise SdkError(f"no finished run in {out}")
    from dedupe.review.service import ReviewError
    try:
        return _service(run_dir).links(person_id)
    except ReviewError:
        raise SdkError(f"no person {person_id} in this run") from None


__all__ = ["inspect", "job_for", "run", "explain", "Result", "SdkError", "OUTPUTS", "FORMATS"]
