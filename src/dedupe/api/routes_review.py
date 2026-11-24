"""The steward's side, across projects: an inbox of what waits for a person, and the history of decisions (with what each one led to).

The review queue itself belongs to a run (routes_read); decisions belong to the project (routes_write). These two reads tie them together so a
person does not have to hunt through projects and runs: the inbox looks only at each project's LATEST finished run (older queues are out of
date), and the decision history says for every decision whether a later run has already applied it.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dedupe.review.assign import pair_key
from dedupe.store.judgments import DECISION_KINDS

from .auth import Principal
from .guards import _context, _services, in_project, require
from .roles import PERMISSIONS
from .routes_read import PERSON_ID, PID
from .scope import project_scope, run_scope

router = APIRouter()
READ_QUEUE = Depends(require("queue.read"))
WRITE = Depends(require("judgment.write"))
READ_CONFIG = Depends(require("config.read"))
MAX_ASSIGN = 500


class InboxRow(BaseModel):
    project_id: str
    project_name: str
    run_id: str | None
    run_finished_at: str | None
    open: int | None                  # pairs nobody has decided yet (None: too many to count quickly)
    assigned_to_me: int = 0           # of the open pairs: the ones given to you
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
    out = []
    for proj in sv.workspace.list():
        q = in_project(sv, p, proj.id)                    # what this person may do in that project (None: not a member)
        if q is None or "queue.read" not in q.permissions:
            continue
        approver = "judgment.approve" in q.permissions
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
                mine = {k for k, a in sv.assignments.of_project(proj.id).items() if a.assignee == p.actor}
                if mine:
                    row["assigned_to_me"] = len(review.queue(limit=10_000, keep=lambda a, b, m=mine: pair_key(a, b) in m))
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


# ---- who works on which pair ---------------------------------------------------------------------------------------------------
def reviewers(sv, project_id: str) -> list[dict]:
    """The people who may decide pairs in this project (judgment.write there), with whether they lead (judgment.approve) and their email.
    Local accounts: their own roles narrowed by their project role, as at sign-in. Other sign-ins are known only as members."""
    local = sv.settings.auth_mode == "local"
    users = {u.username: u for u in sv.accounts.list_users()} if local else {}
    members: dict[str, set] = {}
    for g in sv.workspace.members(project_id):
        members.setdefault(g.subject, set()).add(g.role)
    out = []
    for sub in sorted(set(members) | {u for u, x in users.items() if not members or "projects.all" in sv.accounts.permissions_of(x.roles)}):
        u = users.get(sub)
        if local and (u is None or u.disabled):
            continue
        own = sv.accounts.permissions_of(u.roles) if u else frozenset(PERMISSIONS) - {"projects.all"}
        roles = members.get(sub)
        perms = own if (not roles or "owner" in roles or "projects.all" in own) else own & sv.accounts.permissions_of(roles)
        if "judgment.write" in perms:
            out.append({"username": sub, "name": (u.display_name if u else "") or sub, "lead": "judgment.approve" in perms, "email": u.email if u else None})
    return out


class ReviewerRow(BaseModel):
    username: str
    name: str
    lead: bool
    has_email: bool


class PairRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_l: str = Field(pattern=PERSON_ID)
    person_r: str = Field(pattern=PERSON_ID)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    items: list[PairRef] = Field(min_length=1, max_length=MAX_ASSIGN)
    to: str | None = Field(None, max_length=200)          # a reviewer's username; None gives the pairs back (nobody has them)


@router.get("/projects/{project_id}/reviewers", response_model=list[ReviewerRow])
def project_reviewers(request: Request, project_id: str = PID, p: Principal = READ_QUEUE):
    """Who can be given review pairs in this project (never their email address: only whether notices can reach them)."""
    project_scope(request, project_id)
    return [ReviewerRow(username=r["username"], name=r["name"], lead=r["lead"], has_email=bool(r["email"])) for r in reviewers(_services(request), project_id)]


@router.post("/projects/{project_id}/review/assign")
def assign(request: Request, body: AssignIn, project_id: str = PID, p: Principal = WRITE):
    """Give pairs to a reviewer, take them yourself, or give them back. Taking a free pair or giving back your own needs only the right to
    decide; giving pairs to someone else or moving pairs another person has needs judgment.approve (a lead). The person given pairs gets
    one email about them when the server sends email."""
    project_scope(request, project_id)
    sv = _services(request)
    lead = "judgment.approve" in p.permissions
    keys = sorted({pair_key(i.person_l, i.person_r) for i in body.items})
    have = sv.assignments.of_project_pairs(project_id, keys)
    if body.to is not None and body.to != p.actor and not lead:
        raise HTTPException(403, "Only a lead (who can approve decisions) can give pairs to someone else. You can take free pairs yourself.")
    taken = [a for a in have.values() if a.assignee != p.actor and a.assignee != body.to]
    if taken and not lead:
        raise HTTPException(409, f"{len(taken)} of these pairs are already with {taken[0].assignee}. Ask them or a lead to give them back first.")
    if body.to is None:
        n = sv.assignments.unassign(project_id, keys)
    else:
        people = {r["username"]: r for r in reviewers(sv, project_id)}
        if body.to not in people and body.to != p.actor:          # taking pairs yourself needs only the right to decide (checked above)
            raise HTTPException(422, "That person cannot decide pairs in this project (add them to the project with a role that decides pairs).")
        n = sv.assignments.assign(project_id, keys, body.to, p.actor)
        if body.to != p.actor and people.get(body.to, {}).get("email"):
            proj = sv.workspace.get(project_id)
            sv.notifier.send([people[body.to]["email"]], f"Review: {n} pair(s) given to you in {proj.name}",
                             f"{p.actor} gave you {n} pair(s) to review in the project {proj.name}.\n\n"
                             f"Open them: {sv.notifier.link('/review/' + project_id)} (choose Given to me).\n\n"
                             "This notice holds no customer data. Sign in to see the pairs.")
    sv.seclog.record("judgment_change", actor=p.actor, roles=p.roles, status=200, object_ref=f"{project_id}/assign",
                     detail={"action": "assign" if body.to else "unassign", "to": body.to, "pairs": n}, **_context(request))
    return {"pairs": n, "to": body.to}


@router.get("/projects/{project_id}/review/lessons")
def review_lessons(request: Request, project_id: str = PID, p: Principal = READ_CONFIG):
    """What the reviewers' decisions say about the matching rules: counts by rule and score band, and suggestions (counts only, no data)."""
    from dedupe.review.lessons import Decided, lessons

    from .routes_read import _default_content
    ps = project_scope(request, project_id)
    live = [v for v in ps.configs.versions("decision") if v.status == "active"]
    content = ps.configs.get("decision", live[-1].version) if live else _default_content("decision")
    # one decision per action: "different" on two people covers every record pair between them, and counts once
    seen, decided = set(), []
    for j in ps.judgments.active(("same_person", "different_person")):
        key = j.evidence.get("action_id") or f"j{j.id}"
        if key not in seen:
            seen.add(key)
            decided.append(Decided(j.kind, j.evidence.get("decided_by"), j.evidence.get("probability")))
    return lessons(decided, content)


def notify_run_finished(sv, run) -> None:
    """After a successful run with pairs for a person: one email to each reviewer of the project who has an address."""
    if run.status != "succeeded" or not sv.notifier.enabled or not (run.summary.get("review_items") or 0):
        return
    proj = sv.workspace.get(run.project_id)
    to = [r["email"] for r in reviewers(sv, run.project_id) if r["email"]]
    n = run.summary.get("review_items")
    sv.notifier.send(to, f"Review: {n} pair(s) wait in {proj.name}",
                     f"The run of {proj.name} finished: {n} pair(s) need a person to decide whether they are the same.\n\n"
                     f"Open the Review inbox: {sv.notifier.link('/review/' + run.project_id)}\n\n"
                     "This notice holds no customer data. Sign in to see the pairs.")


__all__ = ["router", "decisions_of", "reviewers", "notify_run_finished"]
