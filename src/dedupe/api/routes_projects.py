"""Home, projects and runs.

A project holds its own decisions, person IDs and rules. A run executes a project over chosen connections (only their live, approved versions)
and returns 202 at once; its results stay readable afterwards. The caller chooses the project and the connections, nothing else.
"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dedupe.workspace import PROJECT_ID, RUN_ID, RunError, WorkspaceError

from .auth import Principal
from .guards import _context, _services, authenticated, require
from .routes_read import CONFIG_NAMES, PID, RID
from .routes_write import _key
from .scope import project_scope

router = APIRouter()
ANY = Depends(authenticated())
SEE_RUNS = Depends(require("runs.read"))
START = Depends(require("runs.start"))
CONNECTION_ID = r"^[A-Za-z0-9_-]{1,40}$"
SUMMARY_KEYS = ("records", "sources", "candidate_pairs", "auto_merged_pairs", "review_pairs", "people", "review_items", "households", "seconds")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectIn(Strict):
    id: str = Field(pattern=PROJECT_ID)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=500)


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


class ProjectView(BaseModel):
    id: str
    name: str
    description: str
    created_by: str
    created_at: str
    runs: int = 0
    latest_run: RunView | None = None


def _run_view(run, replayed: bool = False) -> RunView:
    return RunView(**{**run.__dict__, "summary": {k: run.summary.get(k) for k in SUMMARY_KEYS if k in run.summary},
                      "connections": [{"id": c.get("id"), "version": c.get("version")} for c in run.connections]}, replayed=replayed)


def _project_view(sv, proj) -> ProjectView:
    runs = sv.jobs.recent(proj.id, limit=1000)
    return ProjectView(**proj.__dict__, runs=len(runs), latest_run=_run_view(runs[0]) if runs else None)


# ---- home ----------------------------------------------------------------------------------------------------------------
@router.get("/home")
def home(request: Request, p: Principal = ANY):
    """Counts and recent activity for the start page. Only what the caller's roles allow is filled in."""
    sv = _services(request)
    projects = sv.workspace.list()
    out = {"projects": len(projects), "connections": len(sv.connections.names("connection/")), "recent_runs": [], "open_review_items": None,
           "pending_approvals": None}
    if "runs.read" in p.permissions:
        out["recent_runs"] = [_run_view(r) for r in sv.jobs.recent(limit=8)]
    if "queue.read" in p.permissions:
        latest = [sv.jobs.latest_succeeded(x.id) for x in projects]
        out["open_review_items"] = sum((r.summary.get("review_items") or 0) for r in latest if r)
    if "judgment.approve" in p.permissions or "config.approve" in p.permissions:
        decisions = sum(len(sv.workspace.stores(x.id)[0].pending_approval()) for x in projects) if "judgment.approve" in p.permissions else 0
        connections = sum(1 for n in sv.connections.names("connection/") if sv.connections.versions(n)[-1].status == "proposed")             if "config.approve" in p.permissions else 0
        out["pending_approvals"] = decisions + connections
    return out


# ---- projects ----------------------------------------------------------------------------------------------------------
@router.get("/projects", response_model=list[ProjectView])
def list_projects(request: Request, p: Principal = ANY):
    sv = _services(request)
    return [_project_view(sv, x) for x in sv.workspace.list()]


@router.post("/projects", response_model=ProjectView, status_code=201)
def create_project(request: Request, body: ProjectIn, p: Principal = START):
    sv = _services(request)
    try:
        proj = sv.workspace.create(body.id, body.name, body.description, p.actor)
    except WorkspaceError as e:
        raise HTTPException(409 if "exists" in str(e) else 422, str(e)) from None
    sv.seclog.record("config_change", actor=p.actor, roles=p.roles, status=201, object_ref=proj.id, detail={"action": "create_project"}, **_context(request))
    return _project_view(sv, proj)


@router.get("/projects/{project_id}")
def get_project(request: Request, project_id: str = PID, p: Principal = ANY):
    ps = project_scope(request, project_id)
    sv = _services(request)
    rules = {}
    for name in CONFIG_NAMES:
        act = ps.configs.active(name)
        rules[name] = act.version if act else None
    return {**_project_view(sv, ps.project).model_dump(), "live_rules": rules}


# ---- runs ----------------------------------------------------------------------------------------------------------------
@router.get("/runs", response_model=list[RunView])
def recent_runs(request: Request, limit: int = Query(20, ge=1), p: Principal = SEE_RUNS):
    sv = _services(request)
    return [_run_view(r) for r in sv.jobs.recent(limit=min(limit, sv.settings.max_page_size))]


@router.get("/projects/{project_id}/runs", response_model=list[RunView])
def project_runs(request: Request, project_id: str = PID, limit: int = Query(50, ge=1), p: Principal = SEE_RUNS):
    project_scope(request, project_id)
    sv = _services(request)
    return [_run_view(r) for r in sv.jobs.recent(project_id, limit=min(limit, sv.settings.max_page_size))]


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
    sources, used = [], []
    for cid in ids:
        act = sv.connections.active(f"connection/{cid}")
        if act is None:
            raise HTTPException(409, f"Connection {cid!r} has no approved live version yet.")
        sources.append(act.content)
        used.append({"id": cid, "version": act.version})
    try:
        run, replayed = sv.jobs.start(project_id, {"sources": sources}, used, p.sub, body.reason, key)
    except RunError as e:
        raise HTTPException(409, str(e)[:300]) from None
    if replayed:
        response.status_code = 200
    else:
        sv.seclog.record("run_started", actor=p.actor, roles=p.roles, status=202, object_ref=f"{project_id}/{run.id}", detail={"action": "start",
                         "connections": ids}, **_context(request))
    return _run_view(run, replayed)


def _valid(cid: str) -> bool:
    return bool(re.match(CONNECTION_ID, cid))


@router.get("/projects/{project_id}/runs/{run_id}")
def run_detail(request: Request, project_id: str = PID, run_id: str = RID, p: Principal = SEE_RUNS):
    project_scope(request, project_id)
    sv = _services(request)
    run = sv.jobs.get(project_id, run_id)
    if run is None:
        raise HTTPException(404, "Not found.")
    out = _run_view(run).model_dump()
    summ = sv.workspace.run_dir(project_id, run_id) / "pipeline_summary.json"
    if run.status == "succeeded" and summ.exists():
        s = json.loads(summ.read_text(encoding="utf-8"))
        out["stages"] = [{k: st.get(k) for k in ("stage", "seconds", "peak_memory_mb")} for st in s.get("stages", [])]
        out["configs"] = {k: {x: v.get(x) for x in ("source", "version", "hash")} for k, v in (s.get("configs") or {}).items() if k != "effective_settings"}
        out["peak_memory_mb"] = s.get("peak_memory_mb")
    return out

