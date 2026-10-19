"""Runs: one execution of a project over chosen connections, in a separate OS process, into its own folder.

A request starts a run and returns at once. The run is a separate process (a crash or memory spike never takes the API down); its state lives in
the database. Runs wait in a queue: only `max_concurrent` (default 1: a big run needs the machine's memory) run at once, the rest are "queued" in
the order they were asked for and start by themselves when one finishes, also after the API restarts. The caller chooses the project and the approved connections, nothing
else: the command is a fixed argument list (no shell), the project's database URL travels in the child's environment (never on the command line),
the child does not receive the API's login secret, and the child's output goes to a log file the API never serves.
"""
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, Text, func, select, update

from dedupe.schema import create_and_upgrade

from .projects import Workspace

RUN_ID = r"^R[0-9a-f]{12}$"
STATES = ("queued", "running", "succeeded", "failed", "interrupted", "cancelled", "deleted")
ACTIVE = ("queued", "running")

_meta = MetaData()
runs = Table(
    "project_runs", _meta,
    Column("id", String(20), primary_key=True),
    Column("project_id", String(40), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("requested_by", String(200), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", String(40)),
    Column("finished_at", String(40)),
    Column("exit_code", Integer),
    Column("idempotency_key", String(64)),
    Column("reason", String(300), nullable=False, default=""),
    Column("connections", Text, nullable=False, default="[]"),       # [{"id": ..., "version": ...}] used by the run
    Column("summary", Text, nullable=False, default="{}"),            # headline numbers, filled when the run finishes
)
Index("ix_project_runs_project", runs.c.project_id, runs.c.created_at)
Index("ix_project_runs_key", runs.c.requested_by, runs.c.idempotency_key, unique=True)


class RunError(ValueError):
    pass


@dataclass(frozen=True)
class Run:
    id: str
    project_id: str
    created_at: str
    requested_by: str
    status: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    reason: str
    connections: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _now(precision: str = "seconds") -> str:
    return datetime.now(timezone.utc).isoformat(timespec=precision)       # run creation uses microseconds so same-second runs keep their order


def pipeline_command(run_dir: Path, sources: Path, registry: Path) -> list[str]:
    prev, pinned = run_dir / PREVIOUS_RUN, run_dir / CONFIG_VERSIONS
    return [sys.executable, "-m", "dedupe.pipeline", "--sources", str(sources), "--work", str(run_dir), "--registry-dir", str(registry),
            "--model-path", str(run_dir / "model.json"), "--database-from-env",
            *(["--config-versions", str(pinned)] if pinned.exists() else []),
            *(["--previous-run", prev.read_text(encoding="utf-8").strip()] if prev.exists() else [])]


PREVIOUS_RUN = "previous_run.txt"     # written at launch: the project's latest successful run, whose work an incremental run reuses
CONFIG_VERSIONS = "config_versions.json"   # written at start when the run was started with chosen rule versions


def headline(work: Path) -> dict:
    """The numbers a person wants first, read from a finished run's summary (no personal data)."""
    p = Path(work) / "pipeline_summary.json"
    if not p.exists():
        return {}
    s = json.loads(p.read_text(encoding="utf-8"))
    dec = s.get("decisions") or {}
    return {"records": (s.get("ingest") or {}).get("total"), "sources": sorted(((s.get("ingest") or {}).get("records") or {}).keys()),
            "candidate_pairs": (s.get("blocking") or {}).get("candidate_pairs"), "auto_merged_pairs": dec.get("auto_merge"),
            "review_pairs": dec.get("review"), "people": (s.get("clustering") or {}).get("clusters"),
            "review_items": (s.get("review_queue") or {}).get("queue_items"), "households": (s.get("households") or {}).get("households"),
            "seconds": s.get("total_seconds"), "records_by_source": (s.get("ingest") or {}).get("records") or {},
            "scoring": {k: (s.get("scoring") or {}).get(k) for k in ("mode", "mode_reason")} if s.get("scoring") else None,
            "judgments": s.get("judgments"),
            "source_quality": (s.get("standardise") or {}).get("profile"),
            "incremental": s.get("incremental"),
            "duplicate_record_ids": (s.get("ingest") or {}).get("duplicate_source_record_ids") or {},
            "golden": {k: (s.get("golden_record") or {}).get(k) for k in ("trust_order", "auto_ranked_sources", "sources_without_dates", "shared_values_passed_over",
                                                                     "values_with_source_conflict")} if s.get("golden_record") else None}


class RunJobs:
    def __init__(self, workspace: Workspace, command=pipeline_command, recover: bool = True, max_concurrent: int = 1, max_queued: int = 20):
        """`recover`: mark runs left 'running' by a previous API process as interrupted and start the ones still queued (the API does this at start;
        tools must not)."""
        self.ws, self._command = workspace, command
        self.max_concurrent, self.max_queued = max(1, max_concurrent), max(1, max_queued)
        self.engine = workspace.engine
        self._threads: dict[str, threading.Thread] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()
        create_and_upgrade(self.engine, _meta)
        if recover:
            self._mark_interrupted()
            with self._lock:
                self._dispatch()

    def on_finish(self, run: "Run") -> None:            # hook: the API writes the audit row
        return None

    # ---- state -------------------------------------------------------------------------------------------------------
    def _mark_interrupted(self) -> None:
        """A run still 'running' when the API starts belongs to a process whose supervisor died with the old API. Queued runs have not started and stay queued."""
        with self.engine.begin() as c:
            c.execute(update(runs).where(runs.c.status == "running").values(status="interrupted", finished_at=_now()))

    @staticmethod
    def _run(r) -> Run:
        return Run(r.id, r.project_id, r.created_at, r.requested_by, r.status, r.started_at, r.finished_at, r.exit_code, r.reason,
                   json.loads(r.connections or "[]"), json.loads(r.summary or "{}"))

    def get(self, project_id: str, run_id: str) -> Run | None:
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.id == run_id, runs.c.project_id == project_id)).first()
        return self._run(r) if r else None

    def recent(self, project_id: str | None = None, limit: int = 20) -> list[Run]:
        q = select(runs).where(runs.c.status != "deleted").order_by(runs.c.created_at.desc(), runs.c.id.desc()).limit(limit)
        if project_id:
            q = q.where(runs.c.project_id == project_id)
        with self.engine.connect() as c:
            return [self._run(r) for r in c.execute(q)]

    def latest_succeeded(self, project_id: str) -> Run | None:
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.project_id == project_id, runs.c.status == "succeeded")
                          .order_by(runs.c.created_at.desc(), runs.c.id.desc()).limit(1)).first()
        return self._run(r) if r else None

    def active(self) -> Run | None:
        """A run that is going (or, failing that, the next in the queue)."""
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.status.in_(ACTIVE)).order_by(runs.c.status.desc(), runs.c.created_at, runs.c.id).limit(1)).first()
        return self._run(r) if r else None

    def _count(self, status: str) -> int:
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(runs).where(runs.c.status == status)).scalar() or 0

    def queue_position(self, run: Run) -> int | None:
        """1 for the run that starts next; None when the run is not queued."""
        if run.status != "queued":
            return None
        with self.engine.connect() as c:
            ahead = c.execute(select(func.count()).select_from(runs).where(
                runs.c.status == "queued", (runs.c.created_at < run.created_at) | ((runs.c.created_at == run.created_at) & (runs.c.id < run.id)))).scalar() or 0
        return ahead + 1

    def build_request(self, project_id: str, ids: list[str]) -> tuple[dict, list[dict]]:
        """The ingest configuration and the list of versions used, from the LIVE versions of the project's chosen connections right now.
        Raises RunError for a connection that has no approved live version."""
        configs = self.ws.stores(project_id)[1]
        sources, used = [], []
        for cid in ids:
            act = configs.active(f"connection/{cid}")
            if act is None:
                raise RunError(f"Connection {cid!r} has no approved live version yet.")
            sources.append(act.content)
            used.append({"id": cid, "version": act.version})
        return {"sources": sources}, used

    # ---- start -------------------------------------------------------------------------------------------------------
    def start(self, project_id: str, sources: dict, used: list[dict], actor: str, reason: str = "", key: str | None = None,
              full: bool = False, rules: dict | None = None) -> tuple[Run, bool]:
        """`sources` is the ingest configuration assembled from the chosen connections' live versions; `used` records which ones.
        `full`: process every record from scratch instead of reusing the project's latest successful run for unchanged records.
        `rules`: config name -> version to use (checked by the caller); names left out use the version that is live when the run starts.
        Returns (run, replayed). The run starts at once when there is room, otherwise it waits in the queue. Raises RunError when the queue is full."""
        with self._lock:
            if key is not None:
                with self.engine.connect() as c:
                    old = c.execute(select(runs).where(runs.c.requested_by == actor, runs.c.idempotency_key == key)).first()
                if old:
                    return self._run(old), True
            if self._count("queued") >= self.max_queued:
                raise RunError(f"the run queue is full ({self.max_queued} waiting): wait for some to finish or cancel one")
            rid = "R" + uuid.uuid4().hex[:12]
            run_dir = self.ws.run_dir(project_id, rid)
            run_dir.mkdir(parents=True)
            (run_dir / "sources.json").write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
            (run_dir / "run_options.json").write_text(json.dumps({"full": bool(full), "rules": rules or {}}), encoding="utf-8")
            if rules:
                (run_dir / CONFIG_VERSIONS).write_text(json.dumps(rules), encoding="utf-8")
            with self.engine.begin() as c:
                c.execute(runs.insert().values(id=rid, project_id=project_id, created_at=_now("microseconds"), requested_by=actor, status="queued", idempotency_key=key,
                                               reason=reason, connections=json.dumps(used), summary="{}"))
            self._dispatch()
        return self.get(project_id, rid), False

    def _dispatch(self) -> None:
        """Start queued runs, oldest first, while there is room. Called with the lock held."""
        while self._count("running") < self.max_concurrent:
            with self.engine.connect() as c:
                nxt = c.execute(select(runs.c.id, runs.c.project_id).where(runs.c.status == "queued").order_by(runs.c.created_at, runs.c.id).limit(1)).first()
            if nxt is None:
                return
            with self.engine.begin() as c:
                claimed = c.execute(update(runs).where(runs.c.id == nxt.id, runs.c.status == "queued").values(status="running", started_at=_now())).rowcount
            if claimed:
                self._launch(nxt.project_id, nxt.id)

    def _launch(self, pid: str, rid: str) -> None:
        run_dir = self.ws.run_dir(pid, rid)
        env = {k: v for k, v in os.environ.items() if k != "DEDUPE_DEV_AUTH_SECRET"}      # the child never needs the API's login secret
        env["DEDUPE_DATABASE_URL"] = self.ws.project_url(pid)
        env["DEDUPE_UPLOAD_DIR"] = str(self.ws.upload_dir)
        opts = run_dir / "run_options.json"
        full = json.loads(opts.read_text(encoding="utf-8")).get("full", False) if opts.exists() else False
        prev = None if full else self.latest_succeeded(pid)
        if prev is not None:                                                               # decided now, not when queued: the newest result wins
            (run_dir / PREVIOUS_RUN).write_text(str(self.ws.run_dir(pid, prev.id)), encoding="utf-8")
        log = open(run_dir / "run.log", "wb")                                              # noqa: SIM115 - closed by the supervising thread
        try:
            proc = subprocess.Popen(  # nosec B603
                self._command(run_dir, run_dir / "sources.json", self.ws.identity_dir(pid)), stdout=log, stderr=subprocess.STDOUT, env=env,
                stdin=subprocess.DEVNULL)
        except OSError:
            log.close()
            self._finish(pid, rid, 127)
            return
        self._procs[rid] = proc                                                            # already inside the caller's lock (the dispatcher)
        t = threading.Thread(target=self._supervise, args=(pid, rid, proc, log), daemon=True, name=f"run-{rid}")
        self._threads[rid] = t
        t.start()

    def _supervise(self, pid: str, rid: str, proc, log) -> None:
        code = proc.wait()
        log.close()
        self._finish(pid, rid, code)

    def _finish(self, pid: str, rid: str, code: int) -> None:
        cancelled = rid in self._cancelled
        self._cancelled.discard(rid)
        self._procs.pop(rid, None)
        status = "cancelled" if cancelled else ("succeeded" if code == 0 else "failed")
        summary = headline(self.ws.run_dir(pid, rid)) if status == "succeeded" else {}
        with self.engine.begin() as c:
            c.execute(update(runs).where(runs.c.id == rid).values(status=status, finished_at=_now(), exit_code=code, summary=json.dumps(summary)))
        self.on_finish(self.get(pid, rid))
        with self._lock:
            self._dispatch()                                                               # room has opened up: the next queued run starts

    def wait(self, project_id: str, run_id: str, timeout: float = 60.0) -> Run | None:
        t = self._threads.get(run_id)
        if t is not None:
            t.join(timeout)
        return self.get(project_id, run_id)

    def cancel(self, project_id: str, run_id: str, actor: str) -> Run:
        """Stop a run that is still queued (it simply never starts) or running (SIGTERM, then SIGKILL after a grace period). The result is always a
        clean 'cancelled' run, never mistaken for a crash."""
        with self._lock:
            run = self.get(project_id, run_id)
            if run is None:
                raise RunError("no such run")
            if run.status not in ACTIVE:
                raise RunError("this run has already finished")
            proc = self._procs.get(run_id)
            if proc is None:                                            # still waiting in the queue
                with self.engine.begin() as c:
                    c.execute(update(runs).where(runs.c.id == run_id, runs.c.status == "queued").values(status="cancelled", finished_at=_now()))
                self.on_finish(self.get(project_id, run_id))
                return self.get(project_id, run_id)
            self._cancelled.add(run_id)
        proc.terminate()                                                  # our own child process
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return self.wait(project_id, run_id, timeout=15) or self.get(project_id, run_id)

    def delete(self, project_id: str, run_id: str) -> Run:
        """Erase a finished run's folder (its results hold personal data). The run's row stays, marked 'deleted', so the history and the audit
        trail still show that it existed, who started it and when. Decisions stewards made while looking at it belong to the project and stay."""
        with self._lock:
            run = self.get(project_id, run_id)
            if run is None or run.status == "deleted":
                raise RunError("no such run")
            if run.status in ACTIVE:
                raise RunError("this run has not finished: cancel it first")
            shutil.rmtree(self.ws.run_dir(project_id, run_id), ignore_errors=True)
            with self.engine.begin() as c:
                c.execute(update(runs).where(runs.c.id == run_id).values(status="deleted"))
        return self.get(project_id, run_id)

    def forget_project(self, project_id: str) -> int:
        """Remove every run row of a project that is being deleted (its folder goes with the project's folder)."""
        with self._lock:
            with self.engine.connect() as c:
                if c.execute(select(func.count()).select_from(runs).where(runs.c.project_id == project_id, runs.c.status.in_(ACTIVE))).scalar():
                    raise RunError("this project has a run that is queued or going: cancel it first")
            with self.engine.begin() as c:
                return c.execute(runs.delete().where(runs.c.project_id == project_id)).rowcount

    # ---- bring in results made elsewhere ---------------------------------------------------------------------------------
    def import_results(self, project_id: str, folder: Path, actor: str, reason: str = "imported") -> Run:
        """Register the output folder of a pipeline run made outside the API (command line, migration from the single-folder layout) as a
        finished run of the project. The folder is copied; a person-ID registry inside it seeds the project's registry when that is empty."""
        folder = Path(folder)
        if not (folder / "pipeline_summary.json").exists():
            raise RunError("that folder holds no finished pipeline run")
        rid = "R" + uuid.uuid4().hex[:12]
        dest = self.ws.run_dir(project_id, rid)
        shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("identity", ".duckdb_tmp", "*.db", "*.db-wal", "*.db-shm"))
        reg = self.ws.identity_dir(project_id)
        if (folder / "identity").is_dir() and not (reg.exists() and any(reg.iterdir())):
            shutil.copytree(folder / "identity", reg, dirs_exist_ok=True)
        now = _now()
        with self.engine.begin() as c:
            c.execute(runs.insert().values(id=rid, project_id=project_id, created_at=now, requested_by=actor, status="succeeded", started_at=now,
                                           finished_at=now, exit_code=0, reason=reason, connections="[]", summary=json.dumps(headline(dest))))
        return self.get(project_id, rid)


__all__ = ["RunJobs", "Run", "RunError", "RUN_ID", "pipeline_command", "headline"]
