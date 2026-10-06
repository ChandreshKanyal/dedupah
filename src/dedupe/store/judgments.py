"""The judgment log: every human decision about who is (not) the same person, kept forever and replayed on every run.

Design rules (see PROGRESS.md section 25):
  * APPEND-ONLY. Rows are never updated or deleted (database triggers refuse it). Undo, supersede, approve and decline are
    new rows that point at an earlier one. The current state is computed by replaying the log.
  * KEYED BY SOURCE RECORDS, never by group labels or person IDs: those change between runs, a (source_id, source_record_id)
    pair does not. A decision therefore survives re-processing, new data and rule changes.
  * TAMPER-EVIDENT. Each row stores the hash of the previous row and its own hash, so `verify()` detects edits made behind
    the application's back (an audit requirement in banking).
  * FOUR EYES (optional per decision): a judgment flagged `requires_approval` has no effect until a DIFFERENT person approves it.

Kinds
  same_person        two records are the same person            (must-link)
  different_person   two records are different people           (cannot-link)
  unsure             a steward looked and could not decide      (snoozes the pair in the queue; no effect on clustering)
  split              a person was wrongly merged: the listed parts must stay apart (every record of one part is different from every
                     record of the others)
  field_override     the golden value of a field is fixed by a steward for the person that contains the anchor records
  revoke             cancels an earlier judgment (undo)
  approve / decline  second person's verdict on a judgment that requires approval

The store works on SQLite (development, tests) and PostgreSQL (production) through SQLAlchemy; the URL comes from the
DEDUPE_DATABASE_URL environment variable (credentials never live in config files).
"""
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, Column, Index, Integer, MetaData, String, Table, Text, create_engine, event, select, text
from sqlalchemy.engine import Engine

from dedupe.survivorship.config import GOLDEN_FIELDS

DECISION_KINDS = ("same_person", "different_person", "unsure", "split", "field_override")
META_KINDS = ("revoke", "approve", "decline")
PAIR_KINDS = ("same_person", "different_person", "unsure")
GENESIS = "0" * 64
DEFAULT_URL = "sqlite:///data/judgments.db"

_meta = MetaData()
judgments = Table(
    "judgments", _meta,
    Column("id", Integer, primary_key=True),
    Column("created_at", String(40), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("subject", Text, nullable=False),        # canonical JSON, see _canon_*
    Column("reason", Text, nullable=False, default=""),
    Column("evidence", Text, nullable=False, default="{}"),   # snapshot of what the steward saw (probability, per-field evidence, versions)
    Column("target_id", Integer),                    # for revoke / approve / decline
    Column("requires_approval", Boolean, nullable=False, default=False),
    Column("prev_hash", String(64), nullable=False),
    Column("row_hash", String(64), nullable=False),
    Index("ix_judgments_kind_subject", "kind", "subject"),
    Index("ix_judgments_target", "target_id"),
)

Key = tuple  # (source_id, source_record_id)


class JudgmentError(ValueError):
    pass


@dataclass(frozen=True)
class Judgment:
    id: int
    created_at: str
    actor: str
    kind: str
    subject: dict
    reason: str
    evidence: dict
    requires_approval: bool
    status: str   # active | pending_approval | declined | revoked


def _key(k) -> list:
    if not (isinstance(k, (tuple, list)) and len(k) == 2 and all(isinstance(x, str) and x for x in k)):
        raise JudgmentError(f"a record key is (source_id, source_record_id), both non-empty text; got {k!r}")
    return [k[0], k[1]]


def _json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canon_pair(a, b) -> str:
    a, b = _key(a), _key(b)
    if a == b:
        raise JudgmentError("a judgment needs two different records")
    lo, hi = sorted([a, b])
    return _json({"a": lo, "b": hi})


def _canon_split(parts) -> str:
    cleaned = [sorted([_key(k) for k in p]) for p in parts]
    if len(cleaned) < 2 or any(not p for p in cleaned):
        raise JudgmentError("a split needs at least two non-empty parts")
    flat = [tuple(k) for p in cleaned for k in p]
    if len(flat) != len(set(flat)):
        raise JudgmentError("a record cannot be in two parts of a split (or twice in one)")
    return _json({"parts": sorted(cleaned)})


def _canon_override(field, value, anchors) -> str:
    if field not in GOLDEN_FIELDS:
        raise JudgmentError(f"unknown golden field {field!r}; valid: {sorted(GOLDEN_FIELDS)}")
    if value is not None and not isinstance(value, str):
        raise JudgmentError("an override value is text (or null to clear the field)")
    anchors = sorted(_key(k) for k in anchors)
    if not anchors:
        raise JudgmentError("an override needs at least one anchor record (any record of the person)")
    return _json({"anchors": anchors, "field": field, "value": value})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _hash(prev: str, row: dict) -> str:
    body = _json({k: row[k] for k in ("id", "created_at", "actor", "kind", "subject", "reason", "evidence", "target_id", "requires_approval")})
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


def install_append_only(engine: Engine, table: str = "judgments") -> None:
    """The database itself refuses to change or delete a row of `table` (belt and braces next to the application API)."""
    with engine.begin() as c:
        if engine.dialect.name == "sqlite":
            for op in ("UPDATE", "DELETE"):
                c.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                               f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"))
        elif engine.dialect.name == "postgresql":
            c.execute(text(f"CREATE OR REPLACE FUNCTION {table}_append_only() RETURNS trigger AS $$ "
                           f"BEGIN RAISE EXCEPTION '{table} is append-only'; END; $$ LANGUAGE plpgsql"))
            for op in ("UPDATE", "DELETE"):
                c.execute(text(f"DROP TRIGGER IF EXISTS {table}_no_{op.lower()} ON {table}"))
                c.execute(text(f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"))


_install_triggers = install_append_only


class JudgmentStore:
    def __init__(self, engine: Engine, clock=_now):
        self.engine = engine
        self._clock = clock
        _meta.create_all(engine)
        _install_triggers(engine)

    @classmethod
    def open(cls, url: str | None = None, clock=_now) -> "JudgmentStore":
        url = url or os.environ.get("DEDUPE_DATABASE_URL") or DEFAULT_URL
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url)
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA journal_mode=WAL"))
        return cls(engine, clock)

    # ---- writing -------------------------------------------------------------------------------------------------
    def _append(self, conn, kind, actor, subject, reason="", evidence=None, target_id=None, requires_approval=False) -> int:
        if not actor or not isinstance(actor, str):
            raise JudgmentError("every judgment names the person who made it")
        if self.engine.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE judgments IN EXCLUSIVE MODE"))
        last = conn.execute(select(judgments.c.id, judgments.c.row_hash).order_by(judgments.c.id.desc()).limit(1)).first()
        prev, new_id = (last.row_hash, last.id + 1) if last else (GENESIS, 1)
        row = {"id": new_id, "created_at": self._clock(), "actor": actor, "kind": kind, "subject": subject, "reason": reason or "",
               "evidence": _json(evidence or {}), "target_id": target_id, "requires_approval": bool(requires_approval)}
        conn.execute(judgments.insert().values(**row, prev_hash=prev, row_hash=_hash(prev, row)))
        return new_id

    def _decide_pair(self, kind, a, b, actor, reason, evidence, requires_approval) -> int:
        subject = _canon_pair(a, b)
        with self.engine.begin() as conn:
            mine, opposite = [], []
            for j in self._rows_for_subject(conn, subject):
                if j.status in ("active", "pending_approval") and j.kind in PAIR_KINDS:
                    (mine if j.kind == kind else opposite).append(j)
            if mine:
                return mine[0].id        # the same decision is already in force (or waiting for approval): idempotent
            # a decision on a pair replaces earlier opposite decisions on it (a steward changing their mind); 'unsure' never replaces one
            if kind == "unsure" and any(o.kind in ("same_person", "different_person") for o in opposite):
                raise JudgmentError("this pair already has a decision; revoke it first if you really are unsure")
            for o in opposite:
                self._append(conn, "revoke", actor, "{}", f"superseded by a new {kind} judgment", None, o.id)
            return self._append(conn, kind, actor, subject, reason, evidence, None, requires_approval)

    def same_person(self, a: Key, b: Key, actor: str, reason: str = "", evidence: dict | None = None, requires_approval: bool = False,
                    confirm_id_conflict: bool = False) -> int:
        """`confirm_id_conflict`: the records carry different valid PANs/Aadhaar and the steward confirms they are still one person
        (needs a reason); without it such a link is not applied by the clustering step and is reported."""
        if confirm_id_conflict:
            if not reason:
                raise JudgmentError("confirming a merge across conflicting identifiers needs a reason")
            evidence = {**(evidence or {}), "confirmed_id_conflict": True}
        return self._decide_pair("same_person", a, b, actor, reason, evidence, requires_approval)

    def different_person(self, a: Key, b: Key, actor: str, reason: str = "", evidence: dict | None = None, requires_approval: bool = False) -> int:
        return self._decide_pair("different_person", a, b, actor, reason, evidence, requires_approval)

    def unsure(self, a: Key, b: Key, actor: str, reason: str = "", evidence: dict | None = None) -> int:
        return self._decide_pair("unsure", a, b, actor, reason, evidence, False)

    def split(self, parts: list[list[Key]], actor: str, reason: str, evidence: dict | None = None, requires_approval: bool = False) -> int:
        if not reason:
            raise JudgmentError("a split needs a reason")
        with self.engine.begin() as conn:
            return self._append(conn, "split", actor, _canon_split(parts), reason, evidence, None, requires_approval)

    def override_field(self, field: str, value: str | None, anchors: list[Key], actor: str, reason: str, evidence: dict | None = None,
                       requires_approval: bool = False) -> int:
        if not reason:
            raise JudgmentError("a field override needs a reason")
        with self.engine.begin() as conn:
            return self._append(conn, "field_override", actor, _canon_override(field, value, anchors), reason, evidence, None, requires_approval)

    def revoke(self, judgment_id: int, actor: str, reason: str = "") -> int:
        with self.engine.begin() as conn:
            j = self._get(conn, judgment_id)
            if j.kind in META_KINDS:
                raise JudgmentError("only decisions can be revoked")
            if j.status == "revoked":
                raise JudgmentError(f"judgment {judgment_id} is already revoked")
            return self._append(conn, "revoke", actor, "{}", reason, None, judgment_id)

    def approve(self, judgment_id: int, actor: str, reason: str = "") -> int:
        with self.engine.begin() as conn:
            j = self._get(conn, judgment_id)
            if not j.requires_approval or j.status != "pending_approval":
                raise JudgmentError(f"judgment {judgment_id} is not waiting for approval (status: {j.status})")
            if actor == j.actor:
                raise JudgmentError("four eyes: a decision cannot be approved by the person who made it")
            return self._append(conn, "approve", actor, "{}", reason, None, judgment_id)

    def decline(self, judgment_id: int, actor: str, reason: str) -> int:
        if not reason:
            raise JudgmentError("declining needs a reason")
        with self.engine.begin() as conn:
            j = self._get(conn, judgment_id)
            if not j.requires_approval or j.status != "pending_approval":
                raise JudgmentError(f"judgment {judgment_id} is not waiting for approval (status: {j.status})")
            if actor == j.actor:
                raise JudgmentError("four eyes: a decision cannot be declined by the person who made it")
            return self._append(conn, "decline", actor, "{}", reason, None, judgment_id)

    # ---- reading -------------------------------------------------------------------------------------------------
    @staticmethod
    def _statuses(rows: list, metas: list) -> dict[int, str]:
        revoked = {m.target_id for m in metas if m.kind == "revoke"}
        declined = {m.target_id for m in metas if m.kind == "decline"}
        approvers: dict[int, set] = {}
        for m in metas:
            if m.kind == "approve":
                approvers.setdefault(m.target_id, set()).add(m.actor)
        out = {}
        for r in rows:
            if r.id in revoked:
                out[r.id] = "revoked"
            elif r.id in declined:
                out[r.id] = "declined"
            elif r.requires_approval and not (approvers.get(r.id, set()) - {r.actor}):
                out[r.id] = "pending_approval"
            else:
                out[r.id] = "active"
        return out

    @staticmethod
    def _make(r, status) -> Judgment:
        return Judgment(r.id, r.created_at, r.actor, r.kind, json.loads(r.subject), r.reason, json.loads(r.evidence), bool(r.requires_approval), status)

    def _rows_for_subject(self, conn, subject: str) -> list[Judgment]:
        rows = conn.execute(select(judgments).where(judgments.c.subject == subject, judgments.c.kind.in_(DECISION_KINDS))).fetchall()
        return self._with_status(conn, rows)

    def _with_status(self, conn, rows) -> list[Judgment]:
        ids = [r.id for r in rows]
        metas = conn.execute(select(judgments).where(judgments.c.target_id.in_(ids), judgments.c.kind.in_(META_KINDS))).fetchall() if ids else []
        st = self._statuses(rows, metas)
        return [self._make(r, st[r.id]) for r in rows]

    def _get(self, conn, judgment_id: int) -> Judgment:
        r = conn.execute(select(judgments).where(judgments.c.id == judgment_id)).first()
        if r is None:
            raise JudgmentError(f"no judgment with id {judgment_id}")
        if r.kind in META_KINDS:
            return self._make(r, "active")
        return self._with_status(conn, [r])[0]

    def history(self, kinds: tuple | None = None) -> list[Judgment]:
        """Every row, oldest first, with the status each decision has NOW (the audit view)."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(judgments).order_by(judgments.c.id)).fetchall()
        metas = [r for r in rows if r.kind in META_KINDS]
        decisions = [r for r in rows if r.kind in DECISION_KINDS]
        st = self._statuses(decisions, metas)
        out = [self._make(r, st.get(r.id, "active")) for r in rows]
        return [j for j in out if not kinds or j.kind in kinds]

    def active(self, kinds: tuple | None = None) -> list[Judgment]:
        """Decisions in force right now (not revoked, not declined, not still waiting for a second person)."""
        return [j for j in self.history(kinds or DECISION_KINDS) if j.status == "active"]

    def pending_approval(self) -> list[Judgment]:
        return [j for j in self.history(DECISION_KINDS) if j.status == "pending_approval"]

    def pair_state(self, a: Key, b: Key) -> str | None:
        """'same_person' | 'different_person' | 'unsure' | None for the pair, as currently in force."""
        subject = _canon_pair(a, b)
        with self.engine.connect() as conn:
            for j in self._rows_for_subject(conn, subject):
                if j.status == "active":
                    return j.kind
        return None

    def verify(self) -> dict:
        """Recompute the hash chain. {'ok': bool, 'rows': n, 'first_bad_id': id or None}."""
        prev, n = GENESIS, 0
        with self.engine.connect() as conn:
            for r in conn.execute(select(judgments).order_by(judgments.c.id)):
                row = {k: getattr(r, k) for k in ("id", "created_at", "actor", "kind", "subject", "reason", "evidence", "target_id", "requires_approval")}
                row["requires_approval"] = bool(row["requires_approval"])
                if r.prev_hash != prev or r.row_hash != _hash(prev, row):
                    return {"ok": False, "rows": n, "first_bad_id": r.id}
                prev, n = r.row_hash, n + 1
        return {"ok": True, "rows": n, "first_bad_id": None}
