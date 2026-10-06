"""Project and run scoping for the routes. Every project or run named in an address is looked up (unknown = the same 404 as missing data), a
run must belong to the project in the address, and a run's results are readable only once it has finished."""
from dataclasses import dataclass

from fastapi import HTTPException, Request

from dedupe.governance.store import ConfigStore
from dedupe.review.service import ReviewPolicy, ReviewService
from dedupe.store import JudgmentStore
from dedupe.workspace import PROJECT_ID, RUN_ID, Project, Run

from .guards import _services

__all__ = ["PROJECT_ID", "RUN_ID", "ProjectScope", "RunScope", "project_scope", "run_scope"]
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


def project_scope(request: Request, project_id: str) -> ProjectScope:
    sv = _services(request)
    proj = sv.workspace.get(project_id)
    if proj is None:
        raise HTTPException(404, "Not found.")
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
