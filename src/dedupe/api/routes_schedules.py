"""Scheduled runs of a project: list, create, change (pause, resume, retime) and delete.

A schedule starts an ordinary run of the project's live connections at the chosen times, as the actor `schedule:<id>`. Creating or changing one needs
the same permission as starting a run, and every change is written to the project's activity feed.
"""
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dedupe.workspace.schedules import FREQUENCIES, MODES, SCHEDULE_ID, ScheduleError

from .auth import Principal
from .guards import _context, _services, require
from .routes_read import PID
from .scope import project_scope

router = APIRouter()
SEE = Depends(require("runs.read"))
CHANGE = Depends(require("runs.start"))
SID = Path(pattern=SCHEDULE_ID)
BASE = "/projects/{project_id}/schedules"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ScheduleIn(Strict):
    name: str = Field(min_length=1, max_length=100)
    connections: list[str] = Field(min_length=1, max_length=20)
    frequency: str = Field(pattern="^(" + "|".join(FREQUENCIES) + ")$")
    at_time: str = Field("02:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")        # hourly uses only the minutes
    weekday: int | None = Field(None, ge=0, le=6)                                 # weekly: 0 = Monday ... 6 = Sunday
    timezone: str = Field("UTC", max_length=40)
    mode: str = Field("incremental", pattern="^(" + "|".join(MODES) + ")$")       # incremental: reuse unchanged records' work; full: reload everything


class SchedulePatch(Strict):
    name: str | None = Field(None, min_length=1, max_length=100)
    connections: list[str] | None = Field(None, min_length=1, max_length=20)
    frequency: str | None = Field(None, pattern="^(" + "|".join(FREQUENCIES) + ")$")
    at_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekday: int | None = Field(None, ge=0, le=6)
    timezone: str | None = Field(None, max_length=40)
    mode: str | None = Field(None, pattern="^(" + "|".join(MODES) + ")$")
    enabled: bool | None = None


def _log(request: Request, p: Principal, action: str, sid: str, **detail) -> None:
    _services(request).seclog.record("config_change", actor=p.actor, roles=p.roles, status=200, object_ref=f"schedule/{sid}", detail={"action": action, **detail},
                                     **_context(request))


@router.get(BASE)
def list_schedules(request: Request, project_id: str = PID, p: Principal = SEE):
    project_scope(request, project_id)
    return _services(request).schedules.for_project(project_id)


@router.post(BASE, status_code=201)
def create_schedule(request: Request, body: ScheduleIn, project_id: str = PID, p: Principal = CHANGE):
    ps = project_scope(request, project_id)
    for cid in body.connections:                                              # only this project's connections (live ones at the time it fires)
        if not ps.configs.versions(f"connection/{cid}"):
            raise HTTPException(422, f"Connection {cid!r} does not exist in this project.")
    try:
        s = _services(request).schedules.create(project_id, body.name, body.connections, body.frequency, body.at_time, body.weekday, body.timezone, p.actor,
                                                   body.mode)
    except ScheduleError as e:
        raise HTTPException(422, str(e)) from None
    _log(request, p, "create_schedule", s["id"], frequency=body.frequency, mode=body.mode)
    return s


@router.patch(BASE + "/{schedule_id}")
def change_schedule(request: Request, body: SchedulePatch, project_id: str = PID, schedule_id: str = SID, p: Principal = CHANGE):
    ps = project_scope(request, project_id)
    sv = _services(request)
    if sv.schedules.get(project_id, schedule_id) is None:
        raise HTTPException(404, "Not found.")
    for cid in body.connections or []:
        if not ps.configs.versions(f"connection/{cid}"):
            raise HTTPException(422, f"Connection {cid!r} does not exist in this project.")
    try:
        s = sv.schedules.update(project_id, schedule_id, **body.model_dump(exclude_unset=True))
    except ScheduleError as e:
        raise HTTPException(422, str(e)) from None
    _log(request, p, "change_schedule", schedule_id, enabled=s["enabled"])
    return s


@router.delete(BASE + "/{schedule_id}", status_code=204)
def delete_schedule(request: Request, project_id: str = PID, schedule_id: str = SID, p: Principal = CHANGE):
    project_scope(request, project_id)
    sv = _services(request)
    if not sv.schedules.delete(project_id, schedule_id):
        raise HTTPException(404, "Not found.")
    _log(request, p, "delete_schedule", schedule_id)
    return Response(status_code=204)


__all__ = ["router"]
