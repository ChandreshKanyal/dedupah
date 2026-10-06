"""Write endpoints: review decisions, four-eyes approvals, rule proposals and their approval/activation.

Everything is done AS the verified caller (`Principal.actor`), never as a name typed into the request. Every body is a strict model
(unknown fields are refused, sizes are capped, choices are fixed lists). Decisions accept an optional `Idempotency-Key`: the same key with
the same body replays the first answer instead of deciding twice (a double click or a network retry cannot create two judgments).
Every change writes a `judgment_change` / `config_change` row to the security log (who, what kind, which object; never the reason text's
personal content beyond what the scrubber allows). The four-eyes rules live in the stores and compare identities.
"""
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from dedupe.governance.impact import dry_run
from dedupe.governance.store import MODELS, ConfigError
from dedupe.review.service import ReviewError
from dedupe.store.judgments import JudgmentError
from dedupe.survivorship.config import GOLDEN_FIELDS

from .auth import Principal
from .guards import _context, _services, require
from .routes_read import PERSON_ID, ConfigName, _review

router = APIRouter()
WRITE = Depends(require("judgment.write"))
APPROVE = Depends(require("judgment.approve"))
SEE_PENDING = Depends(require("judgment.approve"))
PROPOSE = Depends(require("config.propose"))
APPROVE_CFG = Depends(require("config.approve"))
ACTIVATE_CFG = Depends(require("config.activate"))
MIN_REASON = 10
MAX_BULK = 100
KEY_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
FieldName = Literal[tuple(sorted(GOLDEN_FIELDS))]          # type: ignore[valid-type]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Pair(Strict):
    person_l: str = Field(pattern=PERSON_ID)
    person_r: str = Field(pattern=PERSON_ID)


class DecideIn(Pair):
    reason: str = Field("", max_length=300)


class ApproveIn(DecideIn):
    confirm_id_conflict: bool = False


class RecordKey(Strict):
    source_id: str = Field(min_length=1, max_length=100)
    source_record_id: str = Field(min_length=1, max_length=200)


class SplitIn(Strict):
    parts: list[list[RecordKey]] = Field(min_length=2, max_length=20)
    reason: str = Field(min_length=MIN_REASON, max_length=300)


class OverrideIn(Strict):
    field: FieldName
    value: str | None = Field(None, max_length=200)
    reason: str = Field(min_length=MIN_REASON, max_length=300)


class UndoIn(Strict):
    reason: str = Field("", max_length=300)


class BulkIn(Strict):
    decision: Literal["approve", "reject"]
    items: list[Pair] = Field(min_length=1, max_length=MAX_BULK)
    reason: str = Field("", max_length=300)


class ReasonIn(Strict):
    reason: str = Field("", max_length=300)


class DeclineIn(Strict):
    reason: str = Field(min_length=3, max_length=300)


class ProposeIn(Strict):
    content: dict
    reason: str = Field(min_length=MIN_REASON, max_length=300)


class RollbackIn(Strict):
    to_version: int = Field(ge=1, le=1_000_000)
    reason: str = Field(min_length=MIN_REASON, max_length=300)


class ActionOut(BaseModel):
    action_id: str
    judgment_ids: list[int]
    pending_approval: bool
    replayed: bool = False


class BulkOut(BaseModel):
    done: list[dict]
    failed: list[dict]


class PendingRow(BaseModel):
    id: int
    kind: str
    actor: str
    reason: str
    persons: list[str] | None = None
    created_at: str


# ---- helpers ---------------------------------------------------------------------------------------------------------
def _action_id(p: Principal, route: str, key: str | None, body: BaseModel, extra: str = "") -> str | None:
    """Same caller + same key + same body = same action id (idempotent); no key = a fresh random id from the service."""
    if key is None:
        return None
    material = json.dumps([p.sub, route, key, body.model_dump(mode="json"), extra], sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def _key(value: str | None = Header(None, alias="Idempotency-Key", pattern=KEY_PATTERN)) -> str | None:
    return value


def _seen(sv, action_id: str | None) -> dict | None:
    """The judgments an earlier request with this action id created, if any."""
    if action_id is None:
        return None
    ids, pending = [], False
    for j in sv.review.store.history(("same_person", "different_person", "unsure", "split", "field_override")):
        if j.evidence.get("action_id") == action_id:
            ids.append(j.id)
            pending = pending or j.status == "pending_approval"
    return {"action_id": action_id, "judgment_ids": ids, "pending_approval": pending, "replayed": True} if ids else None


def _log(request: Request, p: Principal, event: str, what: str, object_ref: str, **detail) -> None:
    _services(request).seclog.record(event, actor=p.actor, roles=p.roles, status=200, object_ref=object_ref, detail={"action": what, **detail},
                                     **_context(request))


def _conflict(e: Exception) -> HTTPException:
    return HTTPException(409, str(e)[:300])


def _invalid_config(name: str, content: dict) -> HTTPException:
    """Field names and error types only: never echo what was typed."""
    try:
        MODELS[name].model_validate(content)
    except ValueError as err:
        fields = [{"field": ".".join(str(x) for x in e["loc"]), "problem": e["type"]} for e in getattr(err, "errors", lambda: [])()]
        return HTTPException(422, "The configuration is not valid: " + ", ".join(f"{f['field']} ({f['problem']})" for f in fields[:10]))
    return HTTPException(422, "The configuration is not valid.")


def _configs(request: Request):
    sv = _services(request)
    if sv.configs is None:
        raise HTTPException(404, "Not found.")
    return sv.configs


def _do(request: Request, p: Principal, route: str, key: str | None, body: BaseModel, ref: str, kind: str, run) -> dict:
    sv = _review(request)
    action_id = _action_id(p, route, key, body)
    if (again := _seen(sv, action_id)) is not None:
        return again
    try:
        out = run(sv.review, action_id)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", kind, ref, judgments=len(out["judgment_ids"]), pending=out["pending_approval"])
    return out


# ---- review decisions --------------------------------------------------------------------------------------------------
@router.post("/decisions/approve", response_model=ActionOut)
def approve(request: Request, body: ApproveIn, key: str | None = Depends(_key), p: Principal = WRITE):
    return _do(request, p, "approve", key, body, f"{body.person_l},{body.person_r}", "approve_match",
               lambda r, a: r.approve_match(body.person_l, body.person_r, p.actor, body.reason, body.confirm_id_conflict, a))


@router.post("/decisions/reject", response_model=ActionOut)
def reject(request: Request, body: DecideIn, key: str | None = Depends(_key), p: Principal = WRITE):
    return _do(request, p, "reject", key, body, f"{body.person_l},{body.person_r}", "reject_match",
               lambda r, a: r.reject_match(body.person_l, body.person_r, p.actor, body.reason, a))


@router.post("/decisions/unsure", response_model=ActionOut)
def unsure(request: Request, body: DecideIn, key: str | None = Depends(_key), p: Principal = WRITE):
    return _do(request, p, "unsure", key, body, f"{body.person_l},{body.person_r}", "mark_unsure",
               lambda r, a: r.mark_unsure(body.person_l, body.person_r, p.actor, body.reason, a))


@router.post("/people/{person_id}/split", response_model=ActionOut)
def split(request: Request, body: SplitIn, person_id: str = Path(pattern=PERSON_ID), key: str | None = Depends(_key), p: Principal = WRITE):
    parts = [[(k.source_id, k.source_record_id) for k in part] for part in body.parts]
    return _do(request, p, f"split:{person_id}", key, body, person_id, "split_person",
               lambda r, a: r.split_person(person_id, parts, p.actor, body.reason, a))


@router.post("/people/{person_id}/override", response_model=ActionOut)
def override(request: Request, body: OverrideIn, person_id: str = Path(pattern=PERSON_ID), key: str | None = Depends(_key), p: Principal = WRITE):
    # the value is personal data: it goes to the judgment log (the audited record of the decision) but never to the security log
    return _do(request, p, f"override:{person_id}", key, body, person_id, "override_field",
               lambda r, a: r.override_field(person_id, body.field, body.value, p.actor, body.reason, a))


@router.post("/decisions/{action_id}/undo")
def undo(request: Request, body: UndoIn, action_id: str = Path(pattern=r"^[0-9a-f]{12}$"), p: Principal = WRITE):
    sv = _review(request)
    try:
        out = sv.review.undo_action(action_id, p.actor, body.reason)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", "undo", action_id, judgments=len(out["revoked"]))
    return out


@router.post("/decisions/bulk", response_model=BulkOut)
def bulk(request: Request, body: BulkIn, key: str | None = Depends(_key), p: Principal = WRITE):
    """Up to 100 pairs, each decided on its own: one failure does not undo the others, and each result says why it failed."""
    sv = _review(request)
    done, failed = [], []
    for item in body.items:
        action_id = _action_id(p, "bulk:" + body.decision, key, body, f"{item.person_l},{item.person_r}")
        pair = f"{item.person_l},{item.person_r}"
        if (again := _seen(sv, action_id)) is not None:
            done.append({"pair": pair, **again})
            continue
        try:
            if body.decision == "approve":
                out = sv.review.approve_match(item.person_l, item.person_r, p.actor, body.reason, False, action_id)
            else:
                out = sv.review.reject_match(item.person_l, item.person_r, p.actor, body.reason, action_id)
            done.append({"pair": pair, **out})
        except (ReviewError, JudgmentError) as e:
            failed.append({"pair": pair, "error": str(e)[:300]})
    _log(request, p, "judgment_change", "bulk_" + body.decision, "bulk", done=len(done), failed=len(failed))
    return {"done": done, "failed": failed}


# ---- four eyes on decisions --------------------------------------------------------------------------------------------
@router.get("/approvals", response_model=list[PendingRow])
def pending(request: Request, p: Principal = SEE_PENDING):
    return _review(request).review.pending()


@router.post("/approvals/{judgment_id}/approve")
def approve_pending(request: Request, body: ReasonIn, judgment_id: int = Path(ge=1, le=10**9), p: Principal = APPROVE):
    try:
        out = _review(request).review.approve_pending(judgment_id, p.actor, body.reason)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", "approve_pending", str(judgment_id))
    return {"judgment_id": judgment_id, "approval_id": out}


@router.post("/approvals/{judgment_id}/decline")
def decline_pending(request: Request, body: DeclineIn, judgment_id: int = Path(ge=1, le=10**9), p: Principal = APPROVE):
    try:
        out = _review(request).review.decline_pending(judgment_id, p.actor, body.reason)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", "decline_pending", str(judgment_id))
    return {"judgment_id": judgment_id, "decline_id": out}


# ---- rules and settings: propose -> approve (someone else) -> activate ---------------------------------------------------
@router.post("/config/{name}/propose")
def propose(request: Request, name: ConfigName, body: ProposeIn, p: Principal = PROPOSE):
    store = _configs(request)
    try:
        version = store.propose(name, body.content, p.actor, body.reason)
    except ConfigError as e:
        raise (_invalid_config(name, body.content) if str(e).startswith("invalid") else _conflict(e)) from None
    _log(request, p, "config_change", "propose", f"{name}:{version}")
    return {"name": name, "version": version, "status": "proposed"}


@router.post("/config/{name}/versions/{version}/dry-run")
def dry_run_version(request: Request, name: ConfigName, version: int = Path(ge=1, le=1_000_000), p: Principal = PROPOSE):
    """What the proposed decision rules would change on the pairs of the last run. Decision rules only; nothing is changed."""
    store, sv = _configs(request), _services(request)
    if name != "decision":
        raise HTTPException(422, "A dry run exists for decision rules only; settings and survivorship need a full run.")
    try:
        candidate = MODELS[name].model_validate(store.get(name, version))
        result = dry_run(sv.settings.work, candidate)
    except ConfigError:
        raise HTTPException(404, "Not found.") from None
    except FileNotFoundError:
        raise HTTPException(404, "No pipeline run was found.") from None
    _log(request, p, "config_change", "dry_run", f"{name}:{version}")
    return result


@router.post("/config/{name}/versions/{version}/approve")
def approve_version(request: Request, name: ConfigName, body: ReasonIn, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE_CFG):
    try:
        _configs(request).approve(name, version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "approve", f"{name}:{version}")
    return {"name": name, "version": version, "approved": True}


@router.post("/config/{name}/versions/{version}/decline")
def decline_version(request: Request, name: ConfigName, body: DeclineIn, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE_CFG):
    try:
        _configs(request).decline(name, version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "decline", f"{name}:{version}")
    return {"name": name, "version": version, "declined": True}


@router.post("/config/{name}/versions/{version}/activate")
def activate_version(request: Request, name: ConfigName, body: ReasonIn, version: int = Path(ge=1, le=1_000_000), p: Principal = ACTIVATE_CFG):
    """Takes effect at the NEXT pipeline run; nothing already computed changes."""
    try:
        _configs(request).activate(name, version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "activate", f"{name}:{version}")
    return {"name": name, "version": version, "active": True, "applies": "from the next pipeline run"}


@router.post("/config/{name}/rollback")
def rollback(request: Request, name: ConfigName, body: RollbackIn, p: Principal = ACTIVATE_CFG):
    try:
        new = _configs(request).rollback(name, body.to_version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "rollback", f"{name}:{new}")
    return {"name": name, "version": new, "rolled_back_to": body.to_version, "applies": "from the next pipeline run"}
