"""The steward's side, across projects: an inbox of what waits for a person, and the history of decisions (with what each one led to).

The review queue itself belongs to a run (routes_read); decisions belong to the project (routes_write). These two reads tie them together so a
person does not have to hunt through projects and runs: the inbox looks only at each project's LATEST finished run (older queues are out of
date), and the decision history says for every decision whether a later run has already applied it.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from dedupe.store.judgments import DECISION_KINDS

from .auth import Principal
from .guards import _services, require
from .routes_read import PID
from .scope import can_see, project_scope, run_scope

router = APIRouter()
READ_QUEUE = Depends(require("queue.read"))


class InboxRow(BaseModel):
    project_id: str
    project_name: str
    run_id: str | None
    run_finished_at: str | None
    open: int | None                  # pairs nobody has decided yet (None: too many to count quickly)
    unsure: int
    pending_approval: int             # decisions waiting for a second person (all of them for approvers, your own otherwise)
    waiting_for_run: int              # decisions in force that no run has applied yet
    scoring_mode: str | None


class DecisionRow(BaseModel):
    action_id: str | None
    kind: str
    persons: list[str]
    field: str | None                 # field overrides: the field name only (the value is personal data)
    actor: str
    reason: str
    created_at: str
    run_id: str | None                # the run the decision was made in
    probability: float | None
    status: str                       # in_force | waiting_for_run | pending_approval | declined | undone
    judgments: int
    yours: bool


def _applied_after(sv, project_id: str) -> str | None:
    """Start time of the latest finished run: decisions made before it are applied, later ones wait for the next run."""
    run = sv.jobs.latest_succeeded(project_id)
    return (run.started_at or run.created_at) if run else None


def _status(statuses: set, created_at: str, applied_cut: str | None) -> str:
    if "pending_approval" in statuses:
        return "pending_approval"
    if "active" in statuses:
        return "in_force" if applied_cut and _ts(created_at) < _ts(applied_cut) else "waiting_for_run"
    return "declined" if "declined" in statuses else "undone"


def _ts(s: str) -> datetime:
    """Judgment times end in Z with microseconds, run times in +00:00 with seconds: compare them as times, not as text."""
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def decisions_of(sv, store, project_id: str, actor: str | None = None) -> list[dict]:
    """Every decision of the project, newest first, one row per action (a 'different people' covers several record pairs)."""
    groups: dict[str, dict] = {}
    for j in store.history(DECISION_KINDS):
        key = j.evidence.get("action_id") or f"j{j.id}"
        g = groups.setdefault(key, {"action_id": j.evidence.get("action_id"), "kind": j.kind, "persons": j.evidence.get("persons") or [],
                                    "field": j.subject.get("field") if j.kind == "field_override" else None, "actor": j.actor, "reason": j.reason,
                                    "created_at": j.created_at, "run_id": j.evidence.get("run_id"), "probability": j.evidence.get("probability"),
                                    "statuses": set(), "judgments": 0})
        g["statuses"].add(j.status)
        g["judgments"] += 1
    cut = _applied_after(sv, project_id)
    out = []
    for g in groups.values():
        if actor and g["actor"] != actor:
            continue
        g["status"] = _status(g.pop("statuses"), g["created_at"], cut)
        out.append(g)
    out.sort(key=lambda g: g["created_at"], reverse=True)
    return out


@router.get("/review/inbox", response_model=list[InboxRow])
def inbox(request: Request, p: Principal = READ_QUEUE):
    """For every project you can open: what waits for a person in its latest finished run."""
    sv = _services(request)
    approver = "judgment.approve" in p.permissions
    out = []
    for proj in sv.workspace.list():
        if not can_see(sv, p, proj.id):
            continue
        run = sv.jobs.latest_succeeded(proj.id)
        store = sv.workspace.stores(proj.id)[0]
        pending = [j for j in store.pending_approval() if approver or j.actor == p.actor]
        waiting = sum(1 for d in decisions_of(sv, store, proj.id) if d["status"] == "waiting_for_run")
        row = {"project_id": proj.id, "project_name": proj.name, "run_id": None, "run_finished_at": None, "open": None, "unsure": 0,
               "pending_approval": len(pending), "waiting_for_run": waiting, "scoring_mode": None}
        if run is not None:
            try:
                review = run_scope(request, proj.id, run.id).review
                summary = review.summary()
                row.update(run_id=run.id, run_finished_at=run.finished_at, open=summary.get("open_now"),
                           unsure=len(review.queue(limit=10_000, status="unsure")), scoring_mode=(run.summary.get("scoring") or {}).get("mode"))
            except HTTPException:
                pass
        out.append(row)
    return out


@router.get("/projects/{project_id}/decisions", response_model=list[DecisionRow])
def project_decisions(request: Request, project_id: str = PID, mine: bool = False, status: str | None = Query(None, pattern=r"^[a-z_]{3,20}$"),
                      limit: int = Query(100, ge=1), offset: int = Query(0, ge=0, le=100_000), p: Principal = READ_QUEUE):
    """The project's decisions, newest first, with what became of each: in force, waiting for the next run, waiting for a second person,
    declined, or undone. `mine=true` shows only your own."""
    ps = project_scope(request, project_id)
    sv = _services(request)
    rows = decisions_of(sv, ps.judgments, project_id, p.actor if mine else None)
    if status:
        rows = [r for r in rows if r["status"] == status]
    page = rows[offset: offset + min(limit, sv.settings.max_page_size)]
    return [DecisionRow(**r, yours=r["actor"] == p.actor) for r in page]


__all__ = ["router", "decisions_of"]
