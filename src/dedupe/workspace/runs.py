"""Runs: one execution of a project over chosen connections, in a separate OS process, into its own folder.

A request starts a run and returns at once. The run is a separate process (a crash or memory spike never takes the API down); its state lives in
the database; one run at a time on this machine, enforced by the database. The caller chooses the project and the approved connections, nothing
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

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, Text, select, update

from .projects import Workspace

RUN_ID = r"^R[0-9a-f]{12}$"
STATES = ("queued", "running", "succeeded", "failed", "interrupted")
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pipeline_command(run_dir: Path, sources: Path, registry: Path) -> list[str]:
    return [sys.executable, "-m", "dedupe.pipeline", "--sources", str(sources), "--work", str(run_dir), "--registry-dir", str(registry),
            "--model-path", str(run_dir / "model.json"), "--database-from-env"]


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
            "seconds": s.get("total_seconds")}


class RunJobs:
    def __init__(self, workspace: Workspace, command=pipeline_command, recover: bool = True):
        """`recover`: mark runs left 'running' by a previous API process as interrupted (the API does this at start; tools must not)."""
        self.ws, self._command = workspace, command
        self.engine = workspace.engine
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        _meta.create_all(self.engine)
        if recover:
            self._mark_interrupted()

    def on_finish(self, run: "Run") -> None:            # hook: the API writes the audit row
        return None

    # ---- state -------------------------------------------------------------------------------------------------------
    def _mark_interrupted(self) -> None:
        """A run still 'running' when the API starts belongs to a process whose supervisor died with the old API."""
        with self.engine.begin() as c:
            c.execute(update(runs).where(runs.c.status.in_(ACTIVE)).values(status="interrupted", finished_at=_now()))

    @staticmethod
    def _run(r) -> Run:
        return Run(r.id, r.project_id, r.created_at, r.requested_by, r.status, r.started_at, r.finished_at, r.exit_code, r.reason,
                   json.loads(r.connections or "[]"), json.loads(r.summary or "{}"))

    def get(self, project_id: str, run_id: str) -> Run | None:
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.id == run_id, runs.c.project_id == project_id)).first()
        return self._run(r) if r else None

    def recent(self, project_id: str | None = None, limit: int = 20) -> list[Run]:
        q = select(runs).order_by(runs.c.created_at.desc(), runs.c.id.desc()).limit(limit)
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
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.status.in_(ACTIVE)).limit(1)).first()
        return self._run(r) if r else None

    # ---- start -------------------------------------------------------------------------------------------------------
    def start(self, project_id: str, sources: dict, used: list[dict], actor: str, reason: str = "", key: str | None = None) -> tuple[Run, bool]:
        """`sources` is the ingest configuration assembled from the chosen connections' live versions; `used` records which ones.
        Returns (run, replayed). Raises RunError when another run is going."""
        with self._lock:
            if key is not None:
                with self.engine.connect() as c:
                    old = c.execute(select(runs).where(runs.c.requested_by == actor, runs.c.idempotency_key == key)).first()
                if old:
                    return self._run(old), True
            if (busy := self.active()) is not None:
                raise RunError(f"another run is in progress ({busy.id} in {busy.project_id}); one run at a time on this machine")
            rid = "R" + uuid.uuid4().hex[:12]
            run_dir = self.ws.run_dir(project_id, rid)
            run_dir.mkdir(parents=True)
            (run_dir / "sources.json").write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
            with self.engine.begin() as c:
                c.execute(runs.insert().values(id=rid, project_id=project_id, created_at=_now(), requested_by=actor, status="queued", idempotency_key=key,
                                               reason=reason, connections=json.dumps(used), summary="{}"))
            self._launch(project_id, rid)
        return self.get(project_id, rid), False

    def _launch(self, pid: str, rid: str) -> None:
        run_dir = self.ws.run_dir(pid, rid)
        env = {k: v for k, v in os.environ.items() if k != "DEDUPE_DEV_AUTH_SECRET"}      # the child never needs the API's login secret
        env["DEDUPE_DATABASE_URL"] = self.ws.project_url(pid)
        env["DEDUPE_UPLOAD_DIR"] = str(self.ws.upload_dir)
        log = open(run_dir / "run.log", "wb")                                              # noqa: SIM115 - closed by the supervising thread
        try:
            proc = subprocess.Popen(  # nosec B603
                self._command(run_dir, run_dir / "sources.json", self.ws.identity_dir(pid)), stdout=log, stderr=subprocess.STDOUT, env=env,
                stdin=subprocess.DEVNULL)
        except OSError:
            log.close()
            self._finish(pid, rid, 127)
            return
        with self.engine.begin() as c:
            c.execute(update(runs).where(runs.c.id == rid).values(status="running", started_at=_now()))
        t = threading.Thread(target=self._supervise, args=(pid, rid, proc, log), daemon=True, name=f"run-{rid}")
        self._threads[rid] = t
        t.start()

    def _supervise(self, pid: str, rid: str, proc, log) -> None:
        code = proc.wait()
        log.close()
        self._finish(pid, rid, code)

    def _finish(self, pid: str, rid: str, code: int) -> None:
        summary = headline(self.ws.run_dir(pid, rid)) if code == 0 else {}
        with self.engine.begin() as c:
            c.execute(update(runs).where(runs.c.id == rid).values(status="succeeded" if code == 0 else "failed", finished_at=_now(), exit_code=code,
                                                                  summary=json.dumps(summary)))
        self.on_finish(self.get(pid, rid))

    def wait(self, project_id: str, run_id: str, timeout: float = 60.0) -> Run | None:
        t = self._threads.get(run_id)
        if t is not None:
            t.join(timeout)
        return self.get(project_id, run_id)

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
