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

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dedupe.governance.impact import dry_run
from dedupe.governance.store import MODELS, ConfigError
from dedupe.review.service import ReviewError, ReviewService
from dedupe.store.judgments import JudgmentError
from dedupe.survivorship.config import GOLDEN_FIELDS

from .auth import Principal
from .guards import _context, _services, require
from .routes_read import PERSON_ID, PID, RID, ConfigName
from .scope import RUN_ID, project_scope, run_scope

router = APIRouter()
WRITE = Depends(require("judgment.write"))
APPROVE = Depends(require("judgment.approve"))
SEE_PENDING = Depends(require("queue.read"))
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
    run_id: str | None = None                 # the run the decision was made in (open its comparison there)
    probability: float | None = None
    top_evidence: list[dict] = []
    field: str | None = None                  # field overrides: which field (never the value: that is personal data)
    yours: bool = False                       # made by the caller: they cannot approve it themselves
    action_id: str | None = None


# ---- helpers ---------------------------------------------------------------------------------------------------------
RUN = "/projects/{project_id}/runs/{run_id}"
PROJ = "/projects/{project_id}"
_DECISIONS = ("same_person", "different_person", "unsure", "split", "field_override")


def _action_id(p: Principal, route: str, key: str | None, body: BaseModel, extra: str = "") -> str | None:
    """Same caller + same key + same body = same action id (idempotent); no key = a fresh random id from the service."""
    if key is None:
        return None
    material = json.dumps([p.sub, route, key, body.model_dump(mode="json"), extra], sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def _key(value: str | None = Header(None, alias="Idempotency-Key", pattern=KEY_PATTERN)) -> str | None:
    return value


def _seen(store, action_id: str | None) -> dict | None:
    """The judgments an earlier request with this action id created, if any."""
    if action_id is None:
        return None
    ids, pending = [], False
    for j in store.history(_DECISIONS):
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


def _do(request: Request, p: Principal, project_id: str, run_id: str, route: str, key: str | None, body: BaseModel, ref: str, kind: str, run) -> dict:
    rs = run_scope(request, project_id, run_id)
    action_id = _action_id(p, f"{project_id}:{route}", key, body)
    if (again := _seen(rs.judgments, action_id)) is not None:
        return again
    try:
        out = run(rs.review, action_id)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", kind, f"{project_id}/{ref}", judgments=len(out["judgment_ids"]), pending=out["pending_approval"])
    return out


# ---- review decisions (made while looking at a run's results; they belong to the project and apply to its later runs) --------------------
@router.post(RUN + "/decisions/approve", response_model=ActionOut)
def approve(request: Request, body: ApproveIn, project_id: str = PID, run_id: str = RID, key: str | None = Depends(_key), p: Principal = WRITE):
    return _do(request, p, project_id, run_id, "approve", key, body, f"{body.person_l},{body.person_r}", "approve_match",
               lambda r, a: r.approve_match(body.person_l, body.person_r, p.actor, body.reason, body.confirm_id_conflict, a))


@router.post(RUN + "/decisions/reject", response_model=ActionOut)
def reject(request: Request, body: DecideIn, project_id: str = PID, run_id: str = RID, key: str | None = Depends(_key), p: Principal = WRITE):
    return _do(request, p, project_id, run_id, "reject", key, body, f"{body.person_l},{body.person_r}", "reject_match",
               lambda r, a: r.reject_match(body.person_l, body.person_r, p.actor, body.reason, a))


@router.post(RUN + "/decisions/unsure", response_model=ActionOut)
def unsure(request: Request, body: DecideIn, project_id: str = PID, run_id: str = RID, key: str | None = Depends(_key), p: Principal = WRITE):
    return _do(request, p, project_id, run_id, "unsure", key, body, f"{body.person_l},{body.person_r}", "mark_unsure",
               lambda r, a: r.mark_unsure(body.person_l, body.person_r, p.actor, body.reason, a))


@router.post(RUN + "/people/{person_id}/split", response_model=ActionOut)
def split(request: Request, body: SplitIn, project_id: str = PID, run_id: str = RID, person_id: str = Path(pattern=PERSON_ID),
          key: str | None = Depends(_key), p: Principal = WRITE):
    parts = [[(k.source_id, k.source_record_id) for k in part] for part in body.parts]
    return _do(request, p, project_id, run_id, f"split:{person_id}", key, body, person_id, "split_person",
               lambda r, a: r.split_person(person_id, parts, p.actor, body.reason, a))


@router.post(RUN + "/people/{person_id}/override", response_model=ActionOut)
def override(request: Request, body: OverrideIn, project_id: str = PID, run_id: str = RID, person_id: str = Path(pattern=PERSON_ID),
             key: str | None = Depends(_key), p: Principal = WRITE):
    # the value is personal data: it goes to the judgment log (the audited record of the decision) but never to the security log
    return _do(request, p, project_id, run_id, f"override:{person_id}", key, body, person_id, "override_field",
               lambda r, a: r.override_field(person_id, body.field, body.value, p.actor, body.reason, a))


@router.post(RUN + "/decisions/bulk", response_model=BulkOut)
def bulk(request: Request, body: BulkIn, project_id: str = PID, run_id: str = RID, key: str | None = Depends(_key), p: Principal = WRITE):
    """Up to 100 pairs, each decided on its own: one failure does not undo the others, and each result says why it failed."""
    rs = run_scope(request, project_id, run_id)
    done, failed = [], []
    for item in body.items:
        action_id = _action_id(p, f"{project_id}:bulk:" + body.decision, key, body, f"{item.person_l},{item.person_r}")
        pair = f"{item.person_l},{item.person_r}"
        if (again := _seen(rs.judgments, action_id)) is not None:
            done.append({"pair": pair, **again})
            continue
        try:
            if body.decision == "approve":
                out = rs.review.approve_match(item.person_l, item.person_r, p.actor, body.reason, False, action_id)
            else:
                out = rs.review.reject_match(item.person_l, item.person_r, p.actor, body.reason, action_id)
            done.append({"pair": pair, **out})
        except (ReviewError, JudgmentError) as e:
            failed.append({"pair": pair, "error": str(e)[:300]})
    _log(request, p, "judgment_change", "bulk_" + body.decision, f"{project_id}/bulk", done=len(done), failed=len(failed))
    return {"done": done, "failed": failed}


@router.post(PROJ + "/decisions/{action_id}/undo")
def undo(request: Request, body: UndoIn, project_id: str = PID, action_id: str = Path(pattern=r"^[0-9a-f]{12}$"), p: Principal = WRITE):
    ps = project_scope(request, project_id)
    try:
        out = ReviewService(_services(request).workspace.project_dir(project_id), ps.judgments).undo_action(action_id, p.actor, body.reason)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", "undo", f"{project_id}/{action_id}", judgments=len(out["revoked"]))
    return out


# ---- four eyes on decisions --------------------------------------------------------------------------------------------
@router.get(PROJ + "/approvals", response_model=list[PendingRow])
def pending(request: Request, project_id: str = PID, p: Principal = SEE_PENDING):
    """Decisions waiting for a second person. Approvers see all of them; others see only their own (so they know what is waiting)."""
    approver = "judgment.approve" in p.permissions
    return [{"id": j.id, "kind": j.kind, "actor": j.actor, "reason": j.reason, "persons": j.evidence.get("persons"), "created_at": j.created_at,
             "run_id": j.evidence.get("run_id"), "probability": j.evidence.get("probability"), "top_evidence": (j.evidence.get("top_evidence") or [])[:4],
             "field": j.subject.get("field") if j.kind == "field_override" else None, "yours": j.actor == p.actor, "action_id": j.evidence.get("action_id")}
            for j in project_scope(request, project_id).judgments.pending_approval() if approver or j.actor == p.actor]


@router.post(PROJ + "/approvals/{judgment_id}/approve")
def approve_pending(request: Request, body: ReasonIn, project_id: str = PID, judgment_id: int = Path(ge=1, le=10**9), p: Principal = APPROVE):
    try:
        out = project_scope(request, project_id).judgments.approve(judgment_id, p.actor, body.reason)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", "approve_pending", f"{project_id}/{judgment_id}")
    return {"judgment_id": judgment_id, "approval_id": out}


@router.post(PROJ + "/approvals/{judgment_id}/decline")
def decline_pending(request: Request, body: DeclineIn, project_id: str = PID, judgment_id: int = Path(ge=1, le=10**9), p: Principal = APPROVE):
    try:
        out = project_scope(request, project_id).judgments.decline(judgment_id, p.actor, body.reason)
    except (ReviewError, JudgmentError) as e:
        raise _conflict(e) from None
    _log(request, p, "judgment_change", "decline_pending", f"{project_id}/{judgment_id}")
    return {"judgment_id": judgment_id, "decline_id": out}


# ---- the project's rules and settings: propose -> approve (someone else) -> activate ------------------------------------
@router.post(PROJ + "/config/{name}/propose")
def propose(request: Request, name: ConfigName, body: ProposeIn, project_id: str = PID, p: Principal = PROPOSE):
    store = project_scope(request, project_id).configs
    try:
        version = store.propose(name, body.content, p.actor, body.reason)
    except ConfigError as e:
        raise (_invalid_config(name, body.content) if str(e).startswith("invalid") else _conflict(e)) from None
    _log(request, p, "config_change", "propose", f"{project_id}/{name}:{version}")
    return {"name": name, "version": version, "status": "proposed"}


@router.post(PROJ + "/config/{name}/versions/{version}/dry-run")
def dry_run_version(request: Request, name: ConfigName, project_id: str = PID, version: int = Path(ge=1, le=1_000_000),
                    run_id: str | None = Query(None, pattern=RUN_ID), p: Principal = PROPOSE):
    """What the proposed decision rules would change on the pairs of a run (default: the project's latest finished run). Nothing is changed."""
    ps, sv = project_scope(request, project_id), _services(request)
    if name != "decision":
        raise HTTPException(422, "A dry run exists for decision rules only; settings and survivorship need a full run.")
    run = sv.jobs.get(project_id, run_id) if run_id else sv.jobs.latest_succeeded(project_id)
    if run is None or run.status != "succeeded":
        raise HTTPException(404, "No finished run was found to compare against.")
    try:
        candidate = MODELS[name].model_validate(ps.configs.get(name, version))
        result = dry_run(sv.workspace.run_dir(project_id, run.id), candidate)
    except ConfigError:
        raise HTTPException(404, "Not found.") from None
    except FileNotFoundError:
        raise HTTPException(404, "No finished run was found to compare against.") from None
    _log(request, p, "config_change", "dry_run", f"{project_id}/{name}:{version}")
    return {**result, "run_id": run.id}


@router.post(PROJ + "/config/{name}/versions/{version}/approve")
def approve_version(request: Request, name: ConfigName, body: ReasonIn, project_id: str = PID, version: int = Path(ge=1, le=1_000_000),
                    p: Principal = APPROVE_CFG):
    try:
        project_scope(request, project_id).configs.approve(name, version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "approve", f"{project_id}/{name}:{version}")
    return {"name": name, "version": version, "approved": True}


@router.post(PROJ + "/config/{name}/versions/{version}/decline")
def decline_version(request: Request, name: ConfigName, body: DeclineIn, project_id: str = PID, version: int = Path(ge=1, le=1_000_000),
                    p: Principal = APPROVE_CFG):
    try:
        project_scope(request, project_id).configs.decline(name, version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "decline", f"{project_id}/{name}:{version}")
    return {"name": name, "version": version, "declined": True}


@router.post(PROJ + "/config/{name}/versions/{version}/activate")
def activate_version(request: Request, name: ConfigName, body: ReasonIn, project_id: str = PID, version: int = Path(ge=1, le=1_000_000),
                     p: Principal = ACTIVATE_CFG):
    """Takes effect at the project's NEXT run; nothing already computed changes."""
    try:
        project_scope(request, project_id).configs.activate(name, version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "activate", f"{project_id}/{name}:{version}")
    return {"name": name, "version": version, "active": True, "applies": "from the next run of this project"}


@router.post(PROJ + "/config/{name}/rollback")
def rollback(request: Request, name: ConfigName, body: RollbackIn, project_id: str = PID, p: Principal = ACTIVATE_CFG):
    try:
        new = project_scope(request, project_id).configs.rollback(name, body.to_version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "config_change", "rollback", f"{project_id}/{name}:{new}")
    return {"name": name, "version": new, "rolled_back_to": body.to_version, "applies": "from the next run of this project"}
