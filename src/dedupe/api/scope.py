"""Project and run scoping for the routes. Every project or run named in an address is looked up (unknown = the same 404 as missing data), a
run must belong to the project in the address, and a run's results are readable only once it has finished."""
from dataclasses import dataclass

from fastapi import HTTPException, Request

from dedupe.governance.store import ConfigStore
from dedupe.review.service import ReviewPolicy, ReviewService
from dedupe.store import JudgmentStore
from dedupe.workspace import PROJECT_ID, RUN_ID, Project, Run

from .guards import _context, _services, in_project

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
    """May this person open the project at all (a members-only project does not appear for outsiders)? See guards.in_project."""
    return in_project(sv, principal, project_id) is not None


def _check_membership(request: Request, project_id: str) -> None:
    """Business-unit scoping for a project that is NOT the one in the address (the guard already checked that one): a project with members
    is open only to them and to holders of `projects.all`. Uses the principal the route's guard put on the request."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return
    sv = _services(request)
    if can_see(sv, principal, project_id):
        return
    sv.seclog.record("permission_denied", actor=principal.actor, roles=sorted(principal.roles), status=403, detail={"needs": f"member of {project_id}"},
                      **{**_context(request), "project_id": project_id})
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
    second = ps.project.second_approval
    for reveal in (False, True):
        key = (project_id, run_id, reveal, second)
        if key not in reviews:
            # a one-person project cannot give a decision "a second look": the sensitive-decision approvals are off until the project asks for them
            policy = ReviewPolicy(reveal_sensitive=reveal) if second else ReviewPolicy(
                reveal_sensitive=reveal, four_eyes_id_conflict=False, four_eyes_override_fields=(), four_eyes_split=False, four_eyes_merge_min_records=None)
            reviews[key] = ReviewService(work, ps.judgments, policy)
        pair.append(reviews[key])
    return RunScope(ps.project, ps.judgments, ps.configs, run, pair[0], pair[1])
