"""Versioned, audited configuration: who changed which setting, when, why, who approved it, and what was live when.

Same discipline as the judgment log (see dedupe.store.judgments):
  * APPEND-ONLY and TAMPER-EVIDENT: rows are never changed (database triggers refuse it); each row carries the hash of the previous
    one, so `verify()` detects edits made behind the application's back.
  * IMMUTABLE VERSIONS: a change is a new version (1, 2, 3 ... per config name). Nothing is edited in place; rolling back publishes the
    old content again as a NEW version, so the history shows the rollback too.
  * DRAFT -> APPROVED -> ACTIVE: a proposed version affects nothing until it is activated. With the default policy a version can only be
    activated after somebody OTHER than its proposer approved it (four eyes). `solo=True` switches that off for development.
  * EVERY RUN says which versions it used (pipeline_summary.json -> configs), so any past decision can be explained and reproduced.

Config names: decision (rules and thresholds), survivorship (golden-record rules), settings (blocking rules, caps, household threshold).
The actor is trusted text until the API has authentication.
"""
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, Text, create_engine, event, select, text
from sqlalchemy.engine import Engine

from dedupe.decision.config import DecisionConfig
from dedupe.mapping.config import IngestConfig
from dedupe.store.judgments import DEFAULT_URL, GENESIS, _json, _now, install_append_only
from dedupe.survivorship.config import SurvivorshipConfig

from .settings import MatchingSettings

MODELS = {"decision": DecisionConfig, "survivorship": SurvivorshipConfig, "settings": MatchingSettings, "sources": IngestConfig}
KINDS = ("propose", "approve", "decline", "activate", "rollback")

_meta = MetaData()
config_log = Table(
    "config_log", _meta,
    Column("id", Integer, primary_key=True),
    Column("created_at", String(40), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("kind", String(12), nullable=False),
    Column("name", String(40), nullable=False),
    Column("version", Integer, nullable=False),
    Column("parent_version", Integer),          # propose: the version that was live when this was drafted; rollback: the version restored
    Column("content", Text, nullable=False, default=""),    # canonical JSON (propose and rollback rows only)
    Column("content_hash", String(64), nullable=False, default=""),
    Column("reason", Text, nullable=False, default=""),
    Column("prev_hash", String(64), nullable=False),
    Column("row_hash", String(64), nullable=False),
    Index("ix_config_log_name_version", "name", "version"),
)
_HASHED = ("id", "created_at", "actor", "kind", "name", "version", "parent_version", "content", "content_hash", "reason")


class ConfigError(ValueError):
    pass


def canonical(name: str, content: dict) -> dict:
    """Validate `content` against the model for `name` and return it in full, canonical form (defaults filled in)."""
    if name not in MODELS:
        raise ConfigError(f"unknown config {name!r}; valid: {sorted(MODELS)}")
    try:
        return MODELS[name].model_validate(content).model_dump(mode="json")
    except ValueError as e:
        raise ConfigError(f"invalid {name} config: {e}") from e


def content_hash(content: dict) -> str:
    return hashlib.sha256(_json(content).encode("utf-8")).hexdigest()


def _row_hash(prev: str, row: dict) -> str:
    return hashlib.sha256((prev + _json({k: row[k] for k in _HASHED})).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Version:
    name: str
    version: int
    status: str          # active | approved | proposed | declined | superseded
    proposed_by: str
    proposed_at: str
    reason: str
    parent_version: int | None
    content_hash: str
    rollback: bool = False
    approved_by: tuple = ()
    declined_by: str | None = None
    activated_at: str | None = None
    activated_by: str | None = None


@dataclass(frozen=True)
class Active:
    name: str
    version: int
    content: dict
    hash: str


class ConfigStore:
    def __init__(self, engine: Engine, solo: bool = False, clock=_now):
        self.engine, self.solo, self._clock = engine, solo, clock
        _meta.create_all(engine)
        install_append_only(engine, "config_log")

    @classmethod
    def open(cls, url: str | None = None, solo: bool = False, clock=_now) -> "ConfigStore":
        url = url or os.environ.get("DEDUPE_DATABASE_URL") or DEFAULT_URL
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url)
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA journal_mode=WAL"))
        return cls(engine, solo, clock)

    # ---- reading -------------------------------------------------------------------------------------------------
    def _rows(self, conn, name: str | None = None) -> list:
        q = select(config_log).order_by(config_log.c.id)
        if name:
            q = q.where(config_log.c.name == name)
        return conn.execute(q).fetchall()

    @staticmethod
    def _replay(rows: list) -> dict[tuple, Version]:
        """(name, version) -> Version, computed by replaying the log."""
        out: dict[tuple, dict] = {}
        live: dict[str, int] = {}
        for r in rows:
            k = (r.name, r.version)
            if r.kind in ("propose", "rollback"):
                out[k] = {"name": r.name, "version": r.version, "proposed_by": r.actor, "proposed_at": r.created_at, "reason": r.reason,
                          "parent_version": r.parent_version, "content_hash": r.content_hash, "rollback": r.kind == "rollback",
                          "approved_by": [], "declined_by": None, "activated_at": None, "activated_by": None}
            if r.kind == "approve" and r.actor != out[k]["proposed_by"]:
                out[k]["approved_by"].append(r.actor)
            elif r.kind == "decline":
                out[k]["declined_by"] = r.actor
            if r.kind in ("activate", "rollback"):
                live[r.name] = r.version
                out[k]["activated_at"], out[k]["activated_by"] = r.created_at, r.actor
        result = {}
        for k, d in out.items():
            ever_live = d["activated_at"] is not None
            if live.get(k[0]) == k[1]:
                status = "active"
            elif ever_live:
                status = "superseded"
            elif d["declined_by"]:
                status = "declined"
            elif d["approved_by"]:
                status = "approved"
            else:
                status = "proposed"
            result[k] = Version(status=status, **{**d, "approved_by": tuple(dict.fromkeys(d["approved_by"]))})
        return result

    def versions(self, name: str) -> list[Version]:
        with self.engine.connect() as conn:
            rows = self._rows(conn, name)
        return sorted(self._replay(rows).values(), key=lambda v: v.version)

    def get(self, name: str, version: int) -> dict:
        with self.engine.connect() as conn:
            r = conn.execute(select(config_log).where(config_log.c.name == name, config_log.c.version == version,
                                                      config_log.c.kind.in_(("propose", "rollback")))).first()
        if r is None:
            raise ConfigError(f"no version {version} of {name!r}")
        return json.loads(r.content)

    def active(self, name: str) -> Active | None:
        live = [v for v in self.versions(name) if v.status == "active"]
        return Active(name, live[0].version, self.get(name, live[0].version), live[0].content_hash) if live else None

    def history(self, name: str | None = None) -> list[dict]:
        """Every log row, oldest first (the audit view). Content is left out: use get() for it."""
        with self.engine.connect() as conn:
            rows = self._rows(conn, name)
        return [{k: getattr(r, k) for k in ("id", "created_at", "actor", "kind", "name", "version", "parent_version", "content_hash", "reason")}
                for r in rows]

    def verify(self) -> dict:
        prev, n = GENESIS, 0
        with self.engine.connect() as conn:
            for r in self._rows(conn):
                row = {k: getattr(r, k) for k in _HASHED}
                if r.prev_hash != prev or r.row_hash != _row_hash(prev, row):
                    return {"ok": False, "rows": n, "first_bad_id": r.id}
                prev, n = r.row_hash, n + 1
        return {"ok": True, "rows": n, "first_bad_id": None}

    # ---- writing -------------------------------------------------------------------------------------------------
    def _append(self, conn, kind, actor, name, version, parent=None, content="", chash="", reason="") -> int:
        if not actor or not isinstance(actor, str):
            raise ConfigError("every change names the person who made it")
        if self.engine.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE config_log IN EXCLUSIVE MODE"))
        last = conn.execute(select(config_log.c.id, config_log.c.row_hash).order_by(config_log.c.id.desc()).limit(1)).first()
        prev, new_id = (last.row_hash, last.id + 1) if last else (GENESIS, 1)
        row = {"id": new_id, "created_at": self._clock(), "actor": actor, "kind": kind, "name": name, "version": version,
               "parent_version": parent, "content": content, "content_hash": chash, "reason": reason or ""}
        conn.execute(config_log.insert().values(**row, prev_hash=prev, row_hash=_row_hash(prev, row)))
        return new_id

    def _version(self, conn, name: str, version: int) -> Version:
        v = self._replay(self._rows(conn, name)).get((name, version))
        if v is None:
            raise ConfigError(f"no version {version} of {name!r}")
        return v

    def propose(self, name: str, content: dict, actor: str, reason: str) -> int:
        """Draft a new version. It changes nothing until it is approved and activated. Returns the version number."""
        if not reason:
            raise ConfigError("a change needs a reason")
        content = canonical(name, content)
        chash = content_hash(content)
        with self.engine.begin() as conn:
            vs = [v for k, v in self._replay(self._rows(conn, name)).items()]
            if any(v.content_hash == chash and v.status in ("active", "approved", "proposed") for v in vs):
                same = next(v for v in vs if v.content_hash == chash and v.status in ("active", "approved", "proposed"))
                raise ConfigError(f"identical to version {same.version} of {name!r} ({same.status}): nothing to propose")
            live = next((v.version for v in vs if v.status == "active"), None)
            new = max((v.version for v in vs), default=0) + 1
            self._append(conn, "propose", actor, name, new, live, _json(content), chash, reason)
            return new

    def approve(self, name: str, version: int, actor: str, reason: str = "") -> None:
        with self.engine.begin() as conn:
            v = self._version(conn, name, version)
            if v.declined_by or v.status in ("active", "superseded"):
                raise ConfigError(f"{name} v{version} is {v.status}: it cannot be approved")
            if actor == v.proposed_by:
                raise ConfigError("four eyes: a change cannot be approved by the person who proposed it")
            self._append(conn, "approve", actor, name, version, reason=reason)

    def decline(self, name: str, version: int, actor: str, reason: str) -> None:
        if not reason:
            raise ConfigError("declining needs a reason")
        with self.engine.begin() as conn:
            v = self._version(conn, name, version)
            if v.status in ("active", "superseded", "declined"):
                raise ConfigError(f"{name} v{version} is {v.status}: it cannot be declined")
            self._append(conn, "decline", actor, name, version, reason=reason)

    def activate(self, name: str, version: int, actor: str, reason: str = "") -> None:
        """Make an approved version the one every later run uses."""
        with self.engine.begin() as conn:
            v = self._version(conn, name, version)
            if v.status == "active":
                return
            if v.declined_by:
                raise ConfigError(f"{name} v{version} was declined")
            if v.status == "superseded":
                raise ConfigError(f"{name} v{version} was live before; use rollback() to restore it")
            if not (self.solo or v.approved_by):
                raise ConfigError(f"{name} v{version} needs approval by someone other than {v.proposed_by!r} before it can go live")
            self._append(conn, "activate", actor, name, version, reason=reason)

    def rollback(self, name: str, to_version: int, actor: str, reason: str) -> int:
        """Go back to content that was live before. Published as a NEW version (history is never rewritten); no fresh approval is
        needed because that exact content was already approved once. Returns the new version number."""
        if not reason:
            raise ConfigError("a rollback needs a reason")
        with self.engine.begin() as conn:
            vs = self._replay(self._rows(conn, name))
            old = vs.get((name, to_version))
            if old is None or old.activated_at is None:
                raise ConfigError(f"{name} v{to_version} was never live: only a version that was live can be restored")
            if old.status == "active":
                raise ConfigError(f"{name} v{to_version} is already the live version")
            content = conn.execute(select(config_log.c.content).where(config_log.c.name == name, config_log.c.version == to_version,
                                                                      config_log.c.kind.in_(("propose", "rollback")))).scalar_one()
            new = max(v.version for k, v in vs.items()) + 1
            self._append(conn, "rollback", actor, name, new, to_version, content, old.content_hash, f"rollback to v{to_version}: {reason}")
            return new


__all__ = ["ConfigStore", "ConfigError", "Version", "Active", "canonical", "content_hash", "MODELS"]
