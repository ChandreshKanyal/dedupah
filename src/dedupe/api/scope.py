"""Project and run scoping for the routes. Every project or run named in an address is looked up (unknown = the same 404 as missing data), a
run must belong to the project in the address, and a run's results are readable only once it has finished."""
from dataclasses import dataclass

from fastapi import HTTPException, Request

from dedupe.governance.store import ConfigStore
from dedupe.review.service import ReviewPolicy, ReviewService
from dedupe.store import JudgmentStore
from dedupe.workspace import PROJECT_ID, RUN_ID, Project, Run

from .guards import _services

__all__ = ["PROJECT_ID", "RUN_ID", "ProjectScope", "RunScope", "project_scope", "run_scope", "can_see"]
MAX_CACHED = 32


@dataclass
class ProjectScope:
    project: Project
    judgments: JudgmentStore
    configs: ConfigStore


@dataclass
class RunScope(ProjectScope):
    run: Run
    review: ReviewService           # masks identifiers whatever it is asked
    review_reveal: ReviewService    # may unmask: used only after the permission, reason and rate-limit checks


def can_see(sv, principal, project_id: str) -> bool:
    """Same rule as `_check_membership`, as a yes/no for lists (a members-only project does not appear for outsiders)."""
    if "platform_admin" in principal.roles or "auditor" in principal.roles:
        return True
    members = sv.workspace.members(project_id)
    return not members or any(g.subject == principal.sub for g in members)


def _check_membership(request: Request, project_id: str) -> None:
    """Business-unit scoping: a project with no grants is open to anyone holding the matching global permission (today's
    behaviour, unchanged). The moment a project has at least one grant, only its members may touch it - except
    platform_admin and auditor, who always see every project (administration and oversight). Called after the route's
    own `require(...)` guard has already run and set `request.state.principal`."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return
    if "platform_admin" in principal.roles or "auditor" in principal.roles:
        return
    sv = _services(request)
    members = sv.workspace.members(project_id)
    if not members or any(g.subject == principal.sub for g in members):
        return
    sv.seclog.record("permission_denied", actor=principal.actor, roles=sorted(principal.roles), status=403, detail={"needs": f"member of {project_id}"},
                      method=request.method, route=getattr(request.scope.get("route"), "path", "") or "",
                      request_id=request.scope["state"].get("request_id", ""), client_ip=request.scope["state"].get("client_ip", ""))
    raise HTTPException(403, "You are not a member of this project.")


def project_scope(request: Request, project_id: str) -> ProjectScope:
    sv = _services(request)
    proj = sv.workspace.get(project_id)
    if proj is None:
        raise HTTPException(404, "Not found.")
    _check_membership(request, project_id)
    j, c = sv.workspace.stores(project_id)
    return ProjectScope(proj, j, c)


def run_scope(request: Request, project_id: str, run_id: str) -> RunScope:
    ps = project_scope(request, project_id)
    sv = _services(request)
    run = sv.jobs.get(project_id, run_id)
    if run is None:
        raise HTTPException(404, "Not found.")
    if run.status in ("queued", "running"):
        raise HTTPException(409, "This run has not finished yet.")
    if run.status != "succeeded":
        raise HTTPException(409, "This run did not finish successfully, so it has no results.")
    work = sv.workspace.run_dir(project_id, run_id)
    if not (work / "review_queue.parquet").exists():
        raise HTTPException(404, "No pipeline run was found.")
    reviews = sv.reviews
    if len(reviews) > MAX_CACHED:
        reviews.clear()
    pair = []
    for reveal in (False, True):
        key = (project_id, run_id, reveal)
        if key not in reviews:
            reviews[key] = ReviewService(work, ps.judgments, ReviewPolicy(reveal_sensitive=reveal))
        pair.append(reviews[key])
    return RunScope(ps.project, ps.judgments, ps.configs, run, pair[0], pair[1])
