"""Pipeline run jobs: a request starts a run and returns at once; the run is a separate OS process (so a crash or a memory spike never takes
the API down), and its state lives in the database. One run at a time, enforced by the database, not by memory.

The caller chooses nothing about WHAT runs: sources, work folder, database and configuration come from the operator's settings. The command
is a fixed argument list (no shell); the database URL (which may hold a password) travels in the child's environment, never on the command
line. The child's output goes to a log file in the work folder that the API never serves (it can contain record identifiers).
"""
import os
import subprocess  # nosec B404
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, create_engine, select, update
from sqlalchemy.engine import Engine

STATES = ("queued", "running", "succeeded", "failed", "interrupted")
ACTIVE = ("queued", "running")

_meta = MetaData()
run_jobs = Table(
    "run_jobs", _meta,
    Column("id", String(20), primary_key=True),
    Column("created_at", String(40), nullable=False),
    Column("requested_by", String(200), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", String(40)),
    Column("finished_at", String(40)),
    Column("exit_code", Integer),
    Column("idempotency_key", String(64)),
    Column("reason", String(300), nullable=False, default=""),
)
Index("ix_run_jobs_requester_key", run_jobs.c.requested_by, run_jobs.c.idempotency_key, unique=True)


class JobError(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    id: str
    created_at: str
    requested_by: str
    status: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    reason: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pipeline_command(sources: Path, work: Path) -> list[str]:
    return [sys.executable, "-m", "dedupe.pipeline", "--sources", str(sources), "--work", str(work), "--database-from-env"]


class RunJobs:
    def __init__(self, engine: Engine, work: Path, database_url: str, sources: Path | None, command=pipeline_command):
        self.engine, self.work, self.database_url, self.sources, self._command = engine, work, database_url, sources, command
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        _meta.create_all(engine)
        self._mark_interrupted()

    @classmethod
    def open(cls, url: str, work: Path, sources: Path | None, command=pipeline_command) -> "RunJobs":
        return cls(create_engine(url, future=True), work, url, sources, command)

    # ---- state -------------------------------------------------------------------------------------------------------
    def _mark_interrupted(self) -> None:
        """A job still 'running' when the API starts belongs to a process that no longer exists (the supervising thread died with it)."""
        with self.engine.begin() as c:
            c.execute(update(run_jobs).where(run_jobs.c.status.in_(ACTIVE)).values(status="interrupted", finished_at=_now()))

    @staticmethod
    def _job(r) -> Job:
        return Job(r.id, r.created_at, r.requested_by, r.status, r.started_at, r.finished_at, r.exit_code, r.reason)

    def get(self, job_id: str) -> Job | None:
        with self.engine.connect() as c:
            r = c.execute(select(run_jobs).where(run_jobs.c.id == job_id)).first()
        return self._job(r) if r else None

    def recent(self, limit: int = 20) -> list[Job]:
        with self.engine.connect() as c:
            return [self._job(r) for r in c.execute(select(run_jobs).order_by(run_jobs.c.created_at.desc(), run_jobs.c.id.desc()).limit(limit))]

    def active(self) -> Job | None:
        with self.engine.connect() as c:
            r = c.execute(select(run_jobs).where(run_jobs.c.status.in_(ACTIVE)).limit(1)).first()
        return self._job(r) if r else None

    # ---- start -------------------------------------------------------------------------------------------------------
    def start(self, actor: str, reason: str = "", key: str | None = None) -> tuple[Job, bool]:
        """Returns (job, replayed). Raises JobError when nothing is configured or a run is already active."""
        if self.sources is None:
            raise JobError("runs are not configured: the operator must set DEDUPE_PIPELINE_SOURCES")
        with self._lock:
            if key is not None:
                with self.engine.connect() as c:
                    old = c.execute(select(run_jobs).where(run_jobs.c.requested_by == actor, run_jobs.c.idempotency_key == key)).first()
                if old:
                    return self._job(old), True
            if (busy := self.active()) is not None:
                raise JobError(f"a run is already in progress ({busy.id}); one run at a time")
            job_id = "J" + uuid.uuid4().hex[:12]
            with self.engine.begin() as c:
                c.execute(run_jobs.insert().values(id=job_id, created_at=_now(), requested_by=actor, status="queued", idempotency_key=key, reason=reason))
            self._launch(job_id)
        return self.get(job_id), False

    def _launch(self, job_id: str) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        logs = self.work / "jobs"
        logs.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k != "DEDUPE_DEV_AUTH_SECRET"}      # the child never needs the API's login secret
        env["DEDUPE_DATABASE_URL"] = self.database_url
        log = open(logs / f"{job_id}.log", "wb")                                                 # noqa: SIM115 - closed by the supervising thread
        try:
            proc = subprocess.Popen(  # nosec B603
                self._command(self.sources, self.work), stdout=log, stderr=subprocess.STDOUT, env=env, stdin=subprocess.DEVNULL)
        except OSError:
            log.close()
            self._finish(job_id, 127)
            return
        with self.engine.begin() as c:
            c.execute(update(run_jobs).where(run_jobs.c.id == job_id).values(status="running", started_at=_now()))
        t = threading.Thread(target=self._supervise, args=(job_id, proc, log), daemon=True, name=f"run-{job_id}")
        self._threads[job_id] = t
        t.start()

    def _supervise(self, job_id: str, proc, log) -> None:
        code = proc.wait()
        log.close()
        self._finish(job_id, code)

    def _finish(self, job_id: str, code: int) -> None:
        with self.engine.begin() as c:
            c.execute(update(run_jobs).where(run_jobs.c.id == job_id).values(status="succeeded" if code == 0 else "failed", finished_at=_now(), exit_code=code))
        self.on_finish(job_id, code)

    def on_finish(self, job_id: str, code: int) -> None:       # hook: the API writes the audit row
        return None

    def wait(self, job_id: str, timeout: float = 60.0) -> Job | None:
        t = self._threads.get(job_id)
        if t is not None:
            t.join(timeout)
        return self.get(job_id)


__all__ = ["RunJobs", "Job", "JobError", "STATES"]
