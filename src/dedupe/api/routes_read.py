"""Read endpoints of a run (review queue, people, comparisons) and of a project (rules and their history).

Every response is built from an explicit model (property-level allow-list): a column that is added to an output file later does not reach a
client until someone lists it here. Identifiers (PAN, phone, email, document numbers) are masked unless the caller asks to see them AND holds
`pii.unmask` AND gives a reason; each such view is written to the security log (who, which person, which fields, why) and is rate limited.
Names, dates of birth and addresses are shown to everyone who may read people: that is a policy choice stewards need, not a masking guarantee.
"""
import json
from pathlib import Path as Path_
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from dedupe.governance.diff import describe_changes
from dedupe.governance.store import ConfigError
from dedupe.review.service import SENSITIVE, ReviewError

from .auth import Principal
from .guards import _context, _services, require
from .scope import PROJECT_ID, RUN_ID, project_scope, run_scope

PERSON_ID = r"^P\d{9}$"
RUN = "/projects/{project_id}/runs/{run_id}"
PROJ = "/projects/{project_id}"
router = APIRouter()
READ_QUEUE = Depends(require("queue.read"))
READ_PERSON = Depends(require("person.read"))
READ_CONFIG = Depends(require("config.read"))
MIN_REASON = 10
PID = Path(pattern=PROJECT_ID)
RID = Path(pattern=RUN_ID)
CONFIG_NAMES = ("decision", "survivorship", "settings")
ConfigName = Literal[CONFIG_NAMES]            # type: ignore[valid-type]


# ---- response models: the allow-list of what leaves the system ---------------------------------------------------------
class QueueItem(BaseModel):
    person_l: str
    person_r: str
    record_pairs: int
    top_probability: float
    mean_probability: float
    forced: bool
    size_l: int
    size_r: int
    priority: float
    state: str


class RecordView(BaseModel):
    source_id: str
    source_record_id: str
    name_as_written: str | None = None
    name_clean: str | None = None
    dob: str | None = None
    gender: str | None = None
    mobile: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    pincode: str | None = None
    pan: str | None = None
    aadhaar_last4: str | None = None
    passport_no: str | None = None
    dl_no: str | None = None
    voter_id: str | None = None


class PersonView(BaseModel):
    person_id: str
    golden: dict[str, Any]
    record_count: int
    sources: list[str] | None = None
    conflict_fields: list[str] = []
    records: list[RecordView]
    unmasked: bool = False


class Contribution(BaseModel):
    comparison: str
    level: str
    bits: float


class JudgmentView(BaseModel):
    id: int
    kind: str
    actor: str
    status: str
    reason: str


class Comparison(BaseModel):
    persons: list[PersonView]
    record_pairs: int
    why_match: list[Contribution]
    why_not: list[Contribution]
    probability: float | None = None
    decided_by: str | None = None
    reason: str | None = None
    hard_conflicts: list[str]
    existing_judgments: list[JudgmentView]
    unmasked: bool = False


class QueueSummary(BaseModel):
    queue_items_in_last_run: int
    open_now: int | None
    active_judgments: dict[str, int]
    pending_approval: int
    chain_ok: bool


class VersionView(BaseModel):
    name: str
    version: int
    status: str
    proposed_by: str
    proposed_at: str
    reason: str
    parent_version: int | None
    content_hash: str
    rollback: bool
    approved_by: list[str]
    declined_by: str | None
    activated_at: str | None
    activated_by: str | None


class ConfigHistoryRow(BaseModel):
    id: int
    created_at: str
    actor: str
    kind: str
    name: str
    version: int
    parent_version: int | None
    content_hash: str
    reason: str


# ---- helpers --------------------------------------------------------------------------------------------------------
def _not_found() -> HTTPException:
    return HTTPException(404, "Not found.")


def _unmask_gate(request: Request, p: Principal, unmask: bool, reason: str | None, object_ref: str) -> bool:
    """True when identifiers may be shown. Needs the permission, a reason, and stays within the unmask rate limit. Logged by `_log_unmask`."""
    if not unmask:
        return False
    sv = _services(request)
    if "pii.unmask" not in p.permissions:
        sv.seclog.record("permission_denied", actor=p.actor, roles=p.roles, status=403, object_ref=object_ref, detail={"needs": "pii.unmask"}, **_context(request))
        raise HTTPException(403, "You do not have permission to do this.")
    if not reason or len(reason.strip()) < MIN_REASON:
        raise HTTPException(422, f"Showing identifiers needs a reason of at least {MIN_REASON} characters.")
    if not sv.limiter.allow(f"unmask:{p.sub}", sv.settings.unmask_limit_per_min):
        sv.seclog.record("rate_limited", actor=p.actor, roles=p.roles, status=429, object_ref=object_ref, detail={"scope": "unmask"}, **_context(request))
        raise HTTPException(429, "Too many requests to see identifiers.", headers={"Retry-After": "60"})
    return True


def _log_unmask(request: Request, p: Principal, object_ref: str, reason: str, what: str) -> None:
    _services(request).seclog.record("pii_unmasked", actor=p.actor, roles=p.roles, status=200, object_ref=object_ref,
                                     detail={"fields": list(SENSITIVE), "reason": reason.strip(), "view": what}, **_context(request))


# ---- queue -------------------------------------------------------------------------------------------------------------
@router.get(RUN + "/queue", response_model=list[QueueItem])
def queue(request: Request, project_id: str = PID, run_id: str = RID, limit: int = Query(50, ge=1), offset: int = Query(0, ge=0, le=100_000),
          status: Literal["open", "unsure", "all"] = "open", min_probability: float = Query(0.0, ge=0.0, le=1.0),
          max_probability: float = Query(1.0, ge=0.0, le=1.0), p: Principal = READ_QUEUE):
    rs = run_scope(request, project_id, run_id)
    rows = rs.review.queue(limit=min(limit, _services(request).settings.max_page_size), offset=offset, status=status, min_probability=min_probability,
                           max_probability=max_probability)
    return [QueueItem(person_l=r["cluster_l"], person_r=r["cluster_r"], **{k: r[k] for k in (
        "record_pairs", "top_probability", "mean_probability", "forced", "size_l", "size_r", "priority", "state")}) for r in rows]


@router.get(RUN + "/queue/summary", response_model=QueueSummary)
def queue_summary(request: Request, project_id: str = PID, run_id: str = RID, p: Principal = READ_QUEUE):
    return run_scope(request, project_id, run_id).review.summary()


# ---- people and comparisons --------------------------------------------------------------------------------------------
@router.get(RUN + "/people/{person_id}", response_model=PersonView)
def person(request: Request, project_id: str = PID, run_id: str = RID, person_id: str = Path(pattern=PERSON_ID), unmask: bool = False,
           reason: str | None = Query(None, max_length=300), p: Principal = READ_PERSON):
    rs = run_scope(request, project_id, run_id)
    ref = f"{project_id}/{run_id}/{person_id}"
    reveal = _unmask_gate(request, p, unmask, reason, ref)
    try:
        out = (rs.review_reveal if reveal else rs.review).person(person_id, reveal=reveal)
    except ReviewError:
        raise _not_found() from None
    out["unmasked"] = reveal
    if reveal:
        _log_unmask(request, p, ref, reason, "person")
    return out


@router.get(RUN + "/people/{person_l}/compare/{person_r}", response_model=Comparison)
def compare(request: Request, project_id: str = PID, run_id: str = RID, person_l: str = Path(pattern=PERSON_ID), person_r: str = Path(pattern=PERSON_ID),
            unmask: bool = False, reason: str | None = Query(None, max_length=300), p: Principal = READ_PERSON):
    rs = run_scope(request, project_id, run_id)
    if person_l == person_r:
        raise HTTPException(422, "Choose two different people.")
    ref = f"{project_id}/{run_id}/{person_l},{person_r}"
    reveal = _unmask_gate(request, p, unmask, reason, ref)
    try:
        out = (rs.review_reveal if reveal else rs.review).explain(person_l, person_r, reveal=reveal)
    except ReviewError:
        raise _not_found() from None
    out["unmasked"] = reveal
    for person_view in out["persons"]:
        person_view["unmasked"] = reveal
    if reveal:
        _log_unmask(request, p, ref, reason, "comparison")
    return out


# ---- the project's rules (read only) -----------------------------------------------------------------------------------
@router.get(PROJ + "/config/{name}", response_model=list[VersionView])
def config_versions(request: Request, name: ConfigName, project_id: str = PID, p: Principal = READ_CONFIG):
    return [VersionView(**{**v.__dict__, "approved_by": list(v.approved_by)}) for v in project_scope(request, project_id).configs.versions(name)]


@router.get(PROJ + "/config/{name}/versions/{version}")
def config_version(request: Request, name: ConfigName, project_id: str = PID, version: int = Path(ge=1, le=1_000_000), p: Principal = READ_CONFIG):
    try:
        return {"name": name, "version": version, "content": project_scope(request, project_id).configs.get(name, version)}
    except ConfigError:
        raise _not_found() from None


DEFAULT_FILES = {"decision": "decision_default.json", "survivorship": "survivorship_default.json"}


def _default_content(name: str) -> dict:
    """What a run uses when the project has no live version: the shipped file (decision, survivorship) or the settings' own defaults."""
    if name == "settings":
        from dedupe.governance.settings import MatchingSettings
        return MatchingSettings().model_dump(mode="json")
    for base in (Path_.cwd(), Path_(__file__).resolve().parents[3]):
        f = base / "configs" / DEFAULT_FILES[name]
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    raise _not_found()


@router.get(PROJ + "/config/{name}/default")
def config_default(request: Request, name: ConfigName, project_id: str = PID, p: Principal = READ_CONFIG):
    """The built-in content, as a starting point for a project's first version."""
    project_scope(request, project_id)
    return {"name": name, "content": _default_content(name)}


@router.get(PROJ + "/config/{name}/diff", response_model=list[str])
def config_diff(request: Request, name: ConfigName, project_id: str = PID, old: int = Query(ge=1, le=1_000_000), new: int = Query(ge=1, le=1_000_000),
                p: Principal = READ_CONFIG):
    store = project_scope(request, project_id).configs
    try:
        return describe_changes(name, store.get(name, old), store.get(name, new))
    except ConfigError:
        raise _not_found() from None


@router.get(PROJ + "/config/{name}/history", response_model=list[ConfigHistoryRow])
def config_history(request: Request, name: ConfigName, project_id: str = PID, p: Principal = READ_CONFIG):
    return project_scope(request, project_id).configs.history(name)


@router.get(PROJ + "/config-log/verify")
def config_log_verify(request: Request, project_id: str = PID, p: Principal = READ_CONFIG):
    return project_scope(request, project_id).configs.verify()
