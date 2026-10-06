"""Pipeline run jobs: start (202, returns at once), list, status. The caller supplies only a reason and an optional idempotency key; what runs
(sources, work folder, database, configuration) is fixed by the operator. Output files are rewritten in place by a run, so reads answer 503
while a run is active rather than showing a half-written state."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .auth import Principal
from .guards import _context, _services, require
from .jobs import JobError
from .routes_write import KEY_PATTERN, _key

router = APIRouter()
START = Depends(require("runs.start"))
SEE = Depends(require("runs.read"))
JOB_ID = r"^J[0-9a-f]{12}$"


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field("", max_length=300)


class JobView(BaseModel):
    id: str
    created_at: str
    requested_by: str
    status: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    reason: str
    replayed: bool = False


def _jobs(request: Request):
    sv = _services(request)
    if sv.jobs is None:
        raise HTTPException(404, "Not found.")
    return sv.jobs


@router.post("/runs", status_code=202, response_model=JobView)
def start_run(request: Request, response: Response, body: StartIn, key: str | None = Depends(_key), p: Principal = START):
    jobs = _jobs(request)
    try:
        job, replayed = jobs.start(p.sub, body.reason, key)
    except JobError as e:
        raise HTTPException(409, str(e)[:300]) from None
    if replayed:
        response.status_code = 200
    else:
        _services(request).seclog.record("run_started", actor=p.actor, roles=p.roles, status=202, object_ref=job.id, detail={"action": "start"},
                                         **_context(request))
    return {**job.__dict__, "replayed": replayed}


@router.get("/runs", response_model=list[JobView])
def list_runs(request: Request, limit: int = Query(20, ge=1), p: Principal = SEE):
    return [j.__dict__ for j in _jobs(request).recent(min(limit, _services(request).settings.max_page_size))]


@router.get("/runs/{job_id}", response_model=JobView)
def run_status(request: Request, job_id: str = Path(pattern=JOB_ID), p: Principal = SEE):
    job = _jobs(request).get(job_id)
    if job is None:
        raise HTTPException(404, "Not found.")
    return job.__dict__


__all__ = ["router", "KEY_PATTERN"]
