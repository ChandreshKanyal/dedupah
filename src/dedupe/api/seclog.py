"""The security and access log: who did what, to whom, when, from where. Append-only (database triggers), hash-chained (`verify()`),
structured, free of personal data, and exportable as JSON lines for the customer's SIEM.

It records security-relevant events only: refused logins, permission denials, rate limiting, unmasked views of identifiers (with the
reason the person gave), run starts, configuration changes. Routine reads are not logged here. Values that look like PAN, Aadhaar, phone or
email are scrubbed from anything written, so the log can be shipped and retained (CERT-In: 180 days in India; DPDP: at least a year)
without becoming a second copy of the personal data.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, Text, create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from dedupe.schema import create_and_upgrade
from dedupe.store.judgments import DEFAULT_URL, GENESIS, _json, _now, install_append_only

EVENTS = ("auth_failed", "login", "permission_denied", "rate_limited", "request_rejected", "pii_unmasked", "run_started", "run_finished", "run_cancelled", "export", "config_change",
          "judgment_change", "data_deleted", "server_error", "startup")

_WRITE_LOCK = threading.Lock()
_meta = MetaData()
security_log = Table(
    "security_log", _meta,
    Column("id", Integer, primary_key=True),
    Column("created_at", String(40), nullable=False),
    Column("event", String(30), nullable=False),
    Column("actor", String(200), nullable=False, default=""),
    Column("roles", String(200), nullable=False, default=""),
    Column("method", String(10), nullable=False, default=""),
    Column("route", String(200), nullable=False, default=""),      # the route TEMPLATE (/people/{id}), never the filled-in path or query
    Column("status", Integer),
    Column("object_ref", String(200), nullable=False, default=""),  # an opaque id (P000000123), never a name
    Column("detail", Text, nullable=False, default="{}"),
    Column("request_id", String(64), nullable=False, default=""),
    Column("client_ip", String(64), nullable=False, default=""),
    Column("prev_hash", String(64), nullable=False),
    Column("row_hash", String(64), nullable=False),
    Column("project_id", String(40)),                  # the project an event belongs to (for the project's activity feed); part of the hash when set
    Index("ix_security_log_event", "event"),
    Index("ix_security_log_actor", "actor"),
    Index("ix_security_log_project", "project_id"),
)
_HASHED = ("id", "created_at", "event", "actor", "roles", "method", "route", "status", "object_ref", "detail", "request_id", "client_ip")

_PATTERNS = [re.compile(p) for p in (r"[A-Za-z]{5}\d{4}[A-Za-z]", r"[\w.+-]+@[\w-]+\.[\w.-]+", r"\b\d{4}\s?\d{4}\s?\d{4}\b", r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")]
_ALLOWED_DETAIL_KEYS = re.compile(r"[a-z][a-z0-9_]{0,39}")
log = logging.getLogger("dedupe.security")


def scrub(value, limit: int = 200):
    """Remove anything that looks like personal data from a value before it is logged."""
    if isinstance(value, str):
        for p in _PATTERNS:
            value = p.sub("[redacted]", value)
        return value[:limit]
    return value if isinstance(value, (int, float, bool)) or value is None else scrub(str(value), limit)


def scrub_text(text: str, line_limit: int = 400) -> str:
    """Scrub a block of free text (a run's log) line by line: anything that looks like personal data is removed and long lines are cut."""
    out = []
    for line in str(text).splitlines():
        for p in _PATTERNS:
            line = p.sub("[redacted]", line)
        out.append(line[:line_limit])
    return "\n".join(out)


def safe_detail(detail: dict | None) -> dict:
    out = {}
    for k, v in (detail or {}).items():
        if not _ALLOWED_DETAIL_KEYS.fullmatch(str(k)):
            continue
        out[k] = [scrub(x) for x in v[:20]] if isinstance(v, (list, tuple)) else scrub(v)
    return out


def _row_hash(prev: str, row: dict) -> str:
    body = {k: row[k] for k in _HASHED}
    if row.get("project_id"):
        body["project_id"] = row["project_id"]       # rows written before projects existed have none, and keep hashing exactly as they did
    return hashlib.sha256((prev + _json(body)).encode("utf-8")).hexdigest()


class SecurityLog:
    def __init__(self, engine: Engine, clock=_now):
        self.engine, self._clock = engine, clock
        create_and_upgrade(engine, _meta)
        install_append_only(engine, "security_log")

    @classmethod
    def open(cls, url: str | None = None, clock=_now) -> "SecurityLog":
        url = url or os.environ.get("DEDUPE_DATABASE_URL") or DEFAULT_URL
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url)
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA journal_mode=WAL"))
        return cls(engine, clock)

    def record(self, event_name: str, *, actor: str = "", roles=(), method: str = "", route: str = "", status: int | None = None,
               object_ref: str = "", detail: dict | None = None, request_id: str = "", client_ip: str = "", project_id: str | None = None) -> int:
        if event_name not in EVENTS:
            raise ValueError(f"unknown security event {event_name!r}")
        # The chain needs the newest row's id and hash: two requests writing at once would both read the same newest row. PostgreSQL locks the
        # table; SQLite takes its write lock only at the insert, so writers in this process queue on a lock, and a clash with another process
        # (same id taken in between) is retried.
        for attempt in range(8):
            try:
                with _WRITE_LOCK:
                    return self._append(event_name, actor, roles, method, route, status, object_ref, detail, request_id, client_ip, project_id)
            except IntegrityError:
                if attempt == 7:
                    raise
                time.sleep(0.01 * (attempt + 1))
        raise RuntimeError("unreachable")

    def _append(self, event_name, actor, roles, method, route, status, object_ref, detail, request_id, client_ip, project_id) -> int:
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("LOCK TABLE security_log IN EXCLUSIVE MODE"))
            last = conn.execute(select(security_log.c.id, security_log.c.row_hash).order_by(security_log.c.id.desc()).limit(1)).first()
            prev, new_id = (last.row_hash, last.id + 1) if last else (GENESIS, 1)
            row = {"id": new_id, "created_at": self._clock(), "event": event_name, "actor": scrub(actor, 200), "roles": ",".join(sorted(roles))[:200],
                   "method": method[:10], "route": route[:200], "status": status, "object_ref": scrub(object_ref, 200),
                   "detail": _json(safe_detail(detail)), "request_id": request_id[:64], "client_ip": client_ip[:64], "project_id": (project_id or "")[:40] or None}
            conn.execute(security_log.insert().values(**row, prev_hash=prev, row_hash=_row_hash(prev, row)))
        log.info(_json({k: row[k] for k in _HASHED} | ({"project_id": row["project_id"]} if row["project_id"] else {})))      # same line for the customer's log shipper / SIEM
        return new_id

    def events(self, after_id: int = 0, limit: int = 100, event_name: str | None = None, newest_first: bool = False, before_id: int | None = None) -> list[dict]:
        """Oldest first after `after_id` (for a log shipper), or newest first before `before_id` (for a person reading the log)."""
        if newest_first:
            q = select(security_log).order_by(security_log.c.id.desc()).limit(limit)
            if before_id:
                q = q.where(security_log.c.id < before_id)
        else:
            q = select(security_log).where(security_log.c.id > after_id).order_by(security_log.c.id).limit(limit)
        if event_name:
            q = q.where(security_log.c.event == event_name)
        with self.engine.connect() as conn:
            return [{k: getattr(r, k) for k in _HASHED} | {"detail": json.loads(r.detail), "project_id": r.project_id or ""} for r in conn.execute(q)]

    def activity(self, project_id: str, limit: int = 50, before_id: int | None = None) -> list[dict]:
        """A project's own events, newest first (for its Activity tab)."""
        q = select(security_log).where(security_log.c.project_id == project_id).order_by(security_log.c.id.desc()).limit(limit)
        if before_id:
            q = q.where(security_log.c.id < before_id)
        with self.engine.connect() as conn:
            return [{k: getattr(r, k) for k in _HASHED} | {"detail": json.loads(r.detail), "project_id": r.project_id or ""} for r in conn.execute(q)]

    def verify(self) -> dict:
        prev, n = GENESIS, 0
        with self.engine.connect() as conn:
            for r in conn.execute(select(security_log).order_by(security_log.c.id)):
                row = {k: getattr(r, k) for k in _HASHED} | {"project_id": r.project_id}
                if r.prev_hash != prev or r.row_hash != _row_hash(prev, row):
                    return {"ok": False, "rows": n, "first_bad_id": r.id}
                prev, n = r.row_hash, n + 1
        return {"ok": True, "rows": n, "first_bad_id": None}
