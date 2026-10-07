"""Connection health: did the source open and read the last time we looked, how many rows, and did its columns change?

A check is recorded when someone presses "Test now" on a connection and when a run that used the connection finishes (rows read, or failed). Only
facts are stored: no values, no paths, no error text from a driver (the callers pass fixed sentences). The newest first ten are shown on the
connection page; a changed column list is reported as drift ("2 columns added, 1 removed") so a source that was quietly re-exported with a
different layout is noticed before it silently stops matching.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Index, Integer, MetaData, String, Table, Text, select

from dedupe.schema import create_and_upgrade

_meta = MetaData()
checks = Table(
    "connection_checks", _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String(40), nullable=False),
    Column("connection_id", String(40), nullable=False),
    Column("version", Integer),
    Column("kind", String(10), nullable=False),            # test | run
    Column("ok", Boolean, nullable=False),
    Column("rows", Integer),                              # rows read (a run) or sampled (a test)
    Column("columns", Text),                              # JSON list of source column names (tests only)
    Column("drift", Text, nullable=False, server_default="{}"),
    Column("error", String(300)),
    Column("checked_by", String(200), nullable=False),
    Column("checked_at", String(40), nullable=False),
    Index("ix_connection_checks_conn", "project_id", "connection_id", "id"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConnectionHealth:
    def __init__(self, engine):
        self.engine = engine
        create_and_upgrade(engine, _meta)

    def _previous_columns(self, project_id: str, connection_id: str) -> list[str] | None:
        with self.engine.connect() as c:
            r = c.execute(select(checks.c.columns).where(checks.c.project_id == project_id, checks.c.connection_id == connection_id, checks.c.ok.is_(True),
                                                         checks.c.columns.is_not(None)).order_by(checks.c.id.desc()).limit(1)).first()
        return json.loads(r.columns) if r else None

    def record(self, project_id: str, connection_id: str, version: int | None, kind: str, ok: bool, rows: int | None = None, columns: list[str] | None = None,
               error: str | None = None, actor: str = "") -> dict:
        drift: dict = {}
        if columns is not None:
            before = self._previous_columns(project_id, connection_id)
            if before is not None and before != columns:
                drift = {"added": sorted(set(columns) - set(before)), "removed": sorted(set(before) - set(columns))}
        row = {"project_id": project_id, "connection_id": connection_id, "version": version, "kind": kind, "ok": bool(ok), "rows": rows,
               "columns": json.dumps(columns) if columns is not None else None, "drift": json.dumps(drift), "error": (error or "")[:300] or None,
               "checked_by": actor[:200], "checked_at": _now()}
        with self.engine.begin() as c:
            c.execute(checks.insert().values(**row))
        return self._view(row)

    @staticmethod
    def _view(r) -> dict:
        g = (lambda k: r[k]) if isinstance(r, dict) else (lambda k: getattr(r, k))
        return {"version": g("version"), "kind": g("kind"), "ok": bool(g("ok")), "rows": g("rows"), "drift": json.loads(g("drift") or "{}"), "error": g("error"),
                "checked_by": g("checked_by"), "checked_at": g("checked_at")}

    def history(self, project_id: str, connection_id: str, limit: int = 10) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(checks).where(checks.c.project_id == project_id, checks.c.connection_id == connection_id)
                             .order_by(checks.c.id.desc()).limit(limit))
            return [self._view(r) for r in rows]

    def latest(self, project_id: str) -> dict[str, dict]:
        """The newest check of each connection in a project (for the connection list)."""
        out: dict[str, dict] = {}
        with self.engine.connect() as c:
            for r in c.execute(select(checks).where(checks.c.project_id == project_id).order_by(checks.c.id.desc())):
                out.setdefault(r.connection_id, self._view(r))
        return out


__all__ = ["ConnectionHealth"]
