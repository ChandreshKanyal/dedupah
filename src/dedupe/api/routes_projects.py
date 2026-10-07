"""Home, projects and runs.

A project holds its own decisions, person IDs and rules. A run executes a project over chosen connections (only their live, approved versions)
and returns 202 at once; its results stay readable afterwards. The caller chooses the project and the connections, nothing else.
"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dedupe.workspace import PROJECT_ID, RUN_ID, RunError, WorkspaceError

from .auth import Principal
from .guards import _context, _services, authenticated, require, require_or_owner
from .routes_read import CONFIG_NAMES, PID, RID
from .routes_write import _key
from .scope import can_see, project_scope
from .seclog import scrub_text

router = APIRouter()
ANY = Depends(authenticated())
SEE_RUNS = Depends(require("runs.read"))
START = Depends(require("runs.start"))
MANAGE_ACCESS = Depends(require_or_owner("project.access"))      # a project's owner manages its members and settings
CREATE = Depends(require("projects.create"))
CONNECTION_ID = r"^[A-Za-z0-9_-]{1,40}$"
SUMMARY_KEYS = ("records", "sources", "candidate_pairs", "auto_merged_pairs", "review_pairs", "people", "review_items", "households", "seconds", "records_by_source")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectIn(Strict):
    id: str = Field(pattern=PROJECT_ID)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    second_approval: bool = False        # a one-person project approves its own changes; turn this on once others work in it


class SettingsIn(Strict):
    second_approval: bool


class GrantIn(Strict):
    subject: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=40)


class GrantView(BaseModel):
    project_id: str
    subject: str
    role: str
    granted_by: str
    granted_at: str


class StartIn(Strict):
    connections: list[str] = Field(default_factory=list, max_length=20)
    from_run: str | None = Field(None, pattern=RUN_ID)
    reason: str = Field("", max_length=300)


class RunView(BaseModel):
    id: str
    project_id: str
    created_at: str
    requested_by: str
    status: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    reason: str
    connections: list[dict]
    summary: dict
    replayed: bool = False
    queue_position: int | None = None             # 1 = starts next; set only while the run waits in the queue


class ProjectView(BaseModel):
    id: str
    name: str
    description: str
    created_by: str
    created_at: str
    second_approval: bool = True
    you_own: bool = False
    runs: int = 0
    latest_run: RunView | None = None


def _run_view(run, replayed: bool = False, sv=None) -> RunView:
    return RunView(**{**run.__dict__, "summary": {k: run.summary.get(k) for k in SUMMARY_KEYS if k in run.summary},
                      "connections": [{"id": c.get("id"), "version": c.get("version")} for c in run.connections]}, replayed=replayed,
                   queue_position=sv.jobs.queue_position(run) if sv is not None else None)


def _project_view(sv, proj, p: Principal | None = None) -> ProjectView:
    runs = sv.jobs.recent(proj.id, limit=1000)
    return ProjectView(**{**proj.__dict__, "second_approval": proj.second_approval}, you_own=bool(p and sv.workspace.is_owner(proj.id, p.sub)), runs=len(runs),
                       latest_run=_run_view(runs[0], sv=sv) if runs else None)


# ---- home ----------------------------------------------------------------------------------------------------------------
@router.get("/home")
def home(request: Request, p: Principal = ANY):
    """Counts and recent activity for the start page. Only what the caller's roles allow is filled in."""
    sv = _services(request)
    projects = [x for x in sv.workspace.list() if can_see(sv, p, x.id)]
    out = {"projects": len(projects), "connections": sum(len(sv.workspace.stores(x.id)[1].names("connection/")) for x in projects), "recent_runs": [],
           "open_review_items": None, "pending_approvals": None}
    if "runs.read" in p.permissions:
        out["recent_runs"] = [_run_view(r, sv=sv) for r in sv.jobs.recent(limit=50) if can_see(sv, p, r.project_id)][:8]
    if "queue.read" in p.permissions:
        latest = [sv.jobs.latest_succeeded(x.id) for x in projects]
        out["open_review_items"] = sum((r.summary.get("review_items") or 0) for r in latest if r)
    if "judgment.approve" in p.permissions or "config.approve" in p.permissions:
        decisions = sum(len(sv.workspace.stores(x.id)[0].pending_approval()) for x in projects) if "judgment.approve" in p.permissions else 0
        connections = sum(1 for x in projects for n in sv.workspace.stores(x.id)[1].names("connection/")
                          if sv.workspace.stores(x.id)[1].versions(n)[-1].status == "proposed") if "config.approve" in p.permissions else 0
        out["pending_approvals"] = decisions + connections
    return out


# ---- projects ----------------------------------------------------------------------------------------------------------
@router.get("/projects", response_model=list[ProjectView])
def list_projects(request: Request, p: Principal = ANY):
    sv = _services(request)
    return [_project_view(sv, x, p) for x in sv.workspace.list() if can_see(sv, p, x.id)]


@router.post("/projects", response_model=ProjectView, status_code=201)
def create_project(request: Request, body: ProjectIn, p: Principal = CREATE):
    """The creator becomes the project's owner, and the project is private to them until they add others."""
    sv = _services(request)
    try:
        proj = sv.workspace.create(body.id, body.name, body.description, p.actor, second_approval=body.second_approval)
    except WorkspaceError as e:
        raise HTTPException(409 if "exists" in str(e) else 422, str(e)) from None
    sv.workspace.grant(proj.id, p.sub, "owner", p.actor)
    sv.seclog.record("config_change", actor=p.actor, roles=p.roles, status=201, object_ref=proj.id,
                     detail={"action": "create_project", "second_approval": proj.second_approval}, project_id=proj.id,
                     **{k: v for k, v in _context(request).items() if k != "project_id"})
    return _project_view(sv, proj, p)


@router.get("/projects/{project_id}")
def get_project(request: Request, project_id: str = PID, p: Principal = ANY):
    ps = project_scope(request, project_id)
    sv = _services(request)
    rules = {}
    for name in CONFIG_NAMES:
        act = ps.configs.active(name)
        rules[name] = act.version if act else None
    return {**_project_view(sv, ps.project, p).model_dump(), "live_rules": rules}


# ---- runs ----------------------------------------------------------------------------------------------------------------
@router.get("/runs", response_model=list[RunView])
def recent_runs(request: Request, limit: int = Query(20, ge=1), p: Principal = SEE_RUNS):
    sv = _services(request)
    return [_run_view(r, sv=sv) for r in sv.jobs.recent(limit=sv.settings.max_page_size * 4) if can_see(sv, p, r.project_id)][:min(limit, sv.settings.max_page_size)]


@router.get("/projects/{project_id}/runs", response_model=list[RunView])
def project_runs(request: Request, project_id: str = PID, limit: int = Query(50, ge=1), p: Principal = SEE_RUNS):
    project_scope(request, project_id)
    sv = _services(request)
    return [_run_view(r, sv=sv) for r in sv.jobs.recent(project_id, limit=min(limit, sv.settings.max_page_size))]


@router.post("/projects/{project_id}/runs", response_model=RunView, status_code=202)
def start_run(request: Request, response: Response, body: StartIn, project_id: str = PID, key: str | None = Depends(_key), p: Principal = START):
    """Start a run over the live versions of the chosen connections (or the same connections as an earlier run)."""
    project_scope(request, project_id)
    sv = _services(request)
    ids = list(body.connections)
    if body.from_run:
        earlier = sv.jobs.get(project_id, body.from_run)
        if earlier is None:
            raise HTTPException(404, "Not found.")
        ids = ids or [c["id"] for c in earlier.connections]
    if not ids:
        raise HTTPException(422, "Choose at least one connection.")
    if len(set(ids)) != len(ids) or any(not _valid(c) for c in ids):
        raise HTTPException(422, "Connection ids must be distinct and well formed.")
    try:
        sources, used = sv.jobs.build_request(project_id, ids)
        run, replayed = sv.jobs.start(project_id, sources, used, p.sub, body.reason, key)
    except RunError as e:
        raise HTTPException(409, str(e)[:300]) from None
    if replayed:
        response.status_code = 200
    else:
        sv.seclog.record("run_started", actor=p.actor, roles=p.roles, status=202, object_ref=f"{project_id}/{run.id}", detail={"action": "start",
                         "connections": ids}, **_context(request))
    return _run_view(run, replayed, sv)


def _valid(cid: str) -> bool:
    return bool(re.match(CONNECTION_ID, cid))


@router.get("/projects/{project_id}/runs/{run_id}")
def run_detail(request: Request, project_id: str = PID, run_id: str = RID, p: Principal = SEE_RUNS):
    project_scope(request, project_id)
    sv = _services(request)
    run = sv.jobs.get(project_id, run_id)
    if run is None:
        raise HTTPException(404, "Not found.")
    out = _run_view(run, sv=sv).model_dump()
    summ = sv.workspace.run_dir(project_id, run_id) / "pipeline_summary.json"
    if run.status == "succeeded" and summ.exists():
        s = json.loads(summ.read_text(encoding="utf-8"))
        out["stages"] = [{k: st.get(k) for k in ("stage", "seconds", "peak_memory_mb")} for st in s.get("stages", [])]
        out["configs"] = {k: {x: v.get(x) for x in ("source", "version", "hash")} for k, v in (s.get("configs") or {}).items() if k != "effective_settings"}
        out["peak_memory_mb"] = s.get("peak_memory_mb")
    return out


@router.post("/projects/{project_id}/runs/{run_id}/cancel", response_model=RunView)
def cancel_run(request: Request, project_id: str = PID, run_id: str = RID, p: Principal = START):
    """Stop a queued or running run. It ends as 'cancelled' (never 'failed'), so a deliberate stop is not mistaken for a crash."""
    project_scope(request, project_id)
    sv = _services(request)
    try:
        run = sv.jobs.cancel(project_id, run_id, p.actor)
    except RunError as e:
        raise HTTPException(404 if "no such" in str(e) else 409, "Not found." if "no such" in str(e) else str(e)) from None
    sv.seclog.record("run_cancelled", actor=p.actor, roles=p.roles, status=200, object_ref=f"{project_id}/{run_id}", detail={"action": "cancel"},
                     **_context(request))
    return _run_view(run, sv=_services(request))


@router.get("/projects/{project_id}/runs/{run_id}/log")
def run_log(request: Request, project_id: str = PID, run_id: str = RID, lines: int = Query(200, ge=1, le=1000), p: Principal = SEE_RUNS):
    """The end of the run's log, with anything that looks like personal data removed. Useful when a run failed."""
    project_scope(request, project_id)
    sv = _services(request)
    if sv.jobs.get(project_id, run_id) is None:
        raise HTTPException(404, "Not found.")
    path = sv.workspace.run_dir(project_id, run_id) / "run.log"
    if not path.exists():
        return {"available": False, "lines": [], "truncated": False}
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - 65536))
        raw = f.read().decode("utf-8", errors="replace")
    rows = scrub_text(raw).splitlines()
    return {"available": True, "lines": rows[-lines:], "truncated": size > 65536 or len(rows) > lines}


# ---- who may work in a project (business-unit scoping) -------------------------------------------------------------------
@router.get("/projects/{project_id}/access", response_model=list[GrantView])
def list_access(request: Request, project_id: str = PID, p: Principal = MANAGE_ACCESS):
    project_scope(request, project_id)
    return [GrantView(**g.__dict__) for g in _services(request).workspace.members(project_id)]


@router.post("/projects/{project_id}/access", response_model=GrantView, status_code=201)
def grant_access(request: Request, body: GrantIn, project_id: str = PID, p: Principal = MANAGE_ACCESS):
    """Make a person a member of the project. The first grant turns the project from open to members-only."""
    project_scope(request, project_id)
    sv = _services(request)
    if body.role == "owner" or body.role not in sv.accounts.role_names():
        raise HTTPException(422, "Unknown role.")
    try:
        g = sv.workspace.grant(project_id, body.subject, body.role, p.actor)
    except WorkspaceError as e:
        raise HTTPException(422, str(e)) from None
    sv.seclog.record("config_change", actor=p.actor, roles=p.roles, status=201, object_ref=project_id,
                     detail={"action": "grant_access", "subject": body.subject, "role": body.role}, **_context(request))
    return GrantView(**g.__dict__)


@router.delete("/projects/{project_id}/access/{subject}/{role}", status_code=204)
def revoke_access(request: Request, project_id: str = PID, subject: str = Path(min_length=1, max_length=200), role: str = Path(min_length=1, max_length=40),
                  p: Principal = MANAGE_ACCESS):
    project_scope(request, project_id)
    sv = _services(request)
    if role == "owner":
        raise HTTPException(409, "The owner of a project cannot be removed.")
    if not sv.workspace.revoke(project_id, subject, role):
        raise HTTPException(404, "Not found.")
    sv.seclog.record("config_change", actor=p.actor, roles=p.roles, status=204, object_ref=project_id,
                     detail={"action": "revoke_access", "subject": subject, "role": role}, **_context(request))
    return Response(status_code=204)


@router.patch("/projects/{project_id}/settings", response_model=ProjectView)
def project_settings(request: Request, body: SettingsIn, project_id: str = PID, p: Principal = MANAGE_ACCESS):
    """Whether this project needs a second person to approve connection and rule changes (and sensitive decisions)."""
    ps = project_scope(request, project_id)
    sv = _services(request)
    if body.second_approval == ps.project.second_approval:
        return _project_view(sv, ps.project, p)
    if body.second_approval and len({g.subject for g in sv.workspace.members(project_id)}) < 2:
        raise HTTPException(409, "Add at least one more person to the project first: a second approver is needed.")
    sv.workspace.set_second_approval(project_id, body.second_approval)
    sv.seclog.record("config_change", actor=p.actor, roles=p.roles, status=200, object_ref=project_id,
                     detail={"action": "set_second_approval", "second_approval": body.second_approval}, **_context(request))
    return _project_view(sv, sv.workspace.get(project_id), p)


@router.get("/projects/{project_id}/activity")
def project_activity(request: Request, project_id: str = PID, limit: int = Query(50, ge=1), before_id: int | None = Query(None, ge=1), p: Principal = SEE_RUNS):
    """Who did what in this project, newest first: runs, connections, decisions, rule changes, members, exports."""
    project_scope(request, project_id)
    sv = _services(request)
    return sv.seclog.activity(project_id, min(limit, sv.settings.max_page_size), before_id)
