"""Taking results out: downloads (Excel, CSV, Parquet) and writes into a database, plus the project's saved database destinations.

This is personal data leaving the platform, so every route needs the `results.export` permission and a reason of at least ten characters, is rate
limited, and is written to the security log (dataset, format or table, row count: never a value). Database writes go only to a DESTINATION the
project saved: the name of an environment variable set by whoever runs the server (never a typed URL or password), versioned and approved like a
connection, so one project cannot write into another client's database.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from dedupe.governance.store import ConfigError
from dedupe.results import (
    DATASETS,
    DESTINATION_ID,
    FORMATS,
    MODES,
    DestinationConfig,
    ExportError,
    database_url,
    describe,
    preview,
    test_destination,
    write_database,
    write_file,
)
from dedupe.review.service import SENSITIVE, _mask

from .auth import Principal
from .guards import _context, _services, require
from .routes_read import PID, RID, VersionView, _log_unmask, _unmask_gate
from .scope import project_scope

router = APIRouter()
EXPORT = Depends(require("results.export"))
READ_PERSON = Depends(require("person.read"))
MANAGE = Depends(require("sources.manage"))
APPROVE = Depends(require("config.approve"))
ACTIVATE = Depends(require("config.activate"))
DID = Path(pattern=DESTINATION_ID)
RUN = "/projects/{project_id}/runs/{run_id}"
DEST = "/projects/{project_id}/destinations"
DatasetName = Literal[tuple(DATASETS)]       # type: ignore[valid-type]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FileIn(Strict):
    dataset: DatasetName
    format: Literal["xlsx", "csv", "parquet"]
    reason: str = Field(min_length=10, max_length=300)
    safe_for_spreadsheets: bool = True          # CSV only: a leading apostrophe on values a spreadsheet would run as a formula; turn off for machine use


class DatabaseIn(Strict):
    destination: str = Field(pattern=DESTINATION_ID)
    dataset: DatasetName
    table: str = Field(min_length=1, max_length=63)
    mode: Literal["create", "append", "replace"]
    schema_name: str | None = Field(None, max_length=63)
    confirm_replace: bool = False
    reason: str = Field(min_length=10, max_length=300)


class ProposeDestIn(Strict):
    destination: dict
    reason: str = Field(min_length=10, max_length=300)


class ReasonIn(Strict):
    reason: str = Field("", max_length=300)


class DeclineIn(Strict):
    reason: str = Field(min_length=3, max_length=300)


def _throttle(request: Request, p: Principal) -> None:
    sv = _services(request)
    if not sv.limiter.allow(f"export:{p.sub}", sv.settings.export_limit_per_min):
        sv.seclog.record("rate_limited", actor=p.actor, roles=p.roles, status=429, detail={"scope": "export"}, **_context(request))
        raise HTTPException(429, "Too many exports; try again in a minute.", headers={"Retry-After": "60"})


def _results(request: Request, project_id: str, run_id: str):
    """The folder of a finished run of this project (404 unknown, 409 not finished or no results)."""
    project_scope(request, project_id)
    sv = _services(request)
    run = sv.jobs.get(project_id, run_id)
    if run is None:
        raise HTTPException(404, "Not found.")
    if run.status != "succeeded":
        raise HTTPException(409, "This run has no results to take out.")
    return sv.workspace.run_dir(project_id, run_id)


def _audit(request: Request, p: Principal, ref: str, **detail) -> None:
    _services(request).seclog.record("export", actor=p.actor, roles=p.roles, status=200, object_ref=ref, detail=detail, **_context(request))


# ---- what a run can give --------------------------------------------------------------------------------------------------------
def _hide(col: str, v):
    """Identifiers and contact details as on the person page; the sources' extra columns (content unknown) keep their last 3 characters."""
    if v is None or col in SENSITIVE:
        return _mask(col, v)
    s = str(v)
    return "*" * max(len(s) - 3, 0) + s[-3:]


@router.get(RUN + "/datasets/{name}/rows")
def dataset_rows(request: Request, name: DatasetName, project_id: str = PID, run_id: str = RID, limit: int = Query(25, ge=1, le=100),
                 offset: int = Query(0, ge=0, le=10_000_000), q: str = Query("", max_length=60), unmask: bool = False,
                 reason: str | None = Query(None, max_length=300), p: Principal = READ_PERSON):
    """A page of a result table to look at on screen. Identifiers, contact details and the sources' own extra columns are masked unless
    the person holds `pii.unmask` and gives a reason (logged, rate limited). Viewing is not exporting: nothing leaves the platform."""
    run_dir = _results(request, project_id, run_id)
    ref = f"{project_id}/{run_id}/{name}"
    reveal = _unmask_gate(request, p, unmask, reason, ref)
    try:
        page = preview(run_dir, name, limit, offset, q)
    except ExportError as e:
        raise HTTPException(404, str(e)) from None
    hide = [i for i, c in enumerate(page["columns"]) if c in SENSITIVE or c.startswith("extra_")]
    if not reveal:
        for r in page["rows"]:
            for i in hide:
                r[i] = _hide(page["columns"][i], r[i])
    else:
        _log_unmask(request, p, ref, reason, "results preview")
    return {**page, "masked": [page["columns"][i] for i in hide] if not reveal else [], "unmasked": reveal}


@router.get(RUN + "/datasets")
def datasets(request: Request, project_id: str = PID, run_id: str = RID, p: Principal = EXPORT):
    run_dir = _results(request, project_id, run_id)
    return {"datasets": describe(run_dir), "formats": {k: v[0] for k, v in FORMATS.items()}, "modes": list(MODES)}


@router.post(RUN + "/export")
def export_file(request: Request, body: FileIn, project_id: str = PID, run_id: str = RID, p: Principal = EXPORT):
    """Download one dataset as Excel, CSV or Parquet. The file is made on the server, sent, and deleted."""
    run_dir = _results(request, project_id, run_id)
    _throttle(request, p)
    sv = _services(request)
    folder = sv.settings.work / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    label, ext, media = FORMATS[body.format]
    tmp = folder / (uuid.uuid4().hex + ext)
    try:
        rows = write_file(run_dir, body.dataset, body.format, tmp, body.safe_for_spreadsheets)
    except ExportError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, str(e)) from None
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    _audit(request, p, f"{project_id}/{run_id}", action="export_file", dataset=body.dataset, format=body.format, rows=rows, reason=body.reason)
    return FileResponse(tmp, media_type=media, filename=f"{project_id}_{run_id}_{body.dataset}{ext}", headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(tmp.unlink, missing_ok=True))


@router.post(RUN + "/export/database")
def export_database(request: Request, body: DatabaseIn, project_id: str = PID, run_id: str = RID, p: Principal = EXPORT):
    """Write one dataset into a table of one of the project's live destinations: a new table, add to an existing one, or replace its rows."""
    run_dir = _results(request, project_id, run_id)
    _throttle(request, p)
    ps = project_scope(request, project_id)
    act = ps.configs.active(f"destination/{body.destination}")
    if act is None:
        raise HTTPException(409, f"Destination {body.destination!r} has no approved live version in this project.")
    cfg = DestinationConfig.model_validate(act.content)
    try:
        result = write_database(database_url(cfg.url_env), run_dir, body.dataset, body.table, body.mode, body.schema_name or cfg.schema_name, body.confirm_replace)
    except ExportError as e:
        raise HTTPException(422, str(e)) from None
    _audit(request, p, f"{project_id}/{run_id}", action="export_database", dataset=body.dataset, destination=body.destination, table=result["table"],
           mode=result["mode"], rows=result["rows"], reason=body.reason)
    return {**result, "destination": body.destination, "dataset": body.dataset}


# ---- destinations (versioned and approved like connections) ----------------------------------------------------------------------
def _summary(configs, name: str) -> dict:
    vs = configs.versions(name)
    live = next((v for v in vs if v.status == "active"), None)
    content = configs.get(name, live.version if live else vs[-1].version)
    return {"id": name.split("/", 1)[1], "url_env": content.get("url_env"), "schema_name": content.get("schema_name"), "description": content.get("description", ""),
            "live_version": live.version if live else None, "latest_version": vs[-1].version, "latest_status": vs[-1].status,
            "updated_at": vs[-1].proposed_at, "updated_by": vs[-1].proposed_by}


def _conflict(e: Exception) -> HTTPException:
    return HTTPException(409, str(e)[:300])


def _log(request: Request, p: Principal, action: str, ref: str) -> None:
    _services(request).seclog.record("config_change", actor=p.actor, roles=p.roles, status=200, object_ref=ref, detail={"action": action}, **_context(request))


@router.get(DEST)
def list_destinations(request: Request, project_id: str = PID, p: Principal = EXPORT):
    ps = project_scope(request, project_id)
    return [_summary(ps.configs, n) for n in ps.configs.names("destination/")]


@router.get(DEST + "/{destination_id}")
def get_destination(request: Request, project_id: str = PID, destination_id: str = DID, p: Principal = EXPORT):
    ps = project_scope(request, project_id)
    name = f"destination/{destination_id}"
    vs = ps.configs.versions(name)
    if not vs:
        raise HTTPException(404, "Not found.")
    live = next((v for v in vs if v.status == "active"), None)
    return {"id": destination_id, "versions": [VersionView(**{**v.__dict__, "approved_by": list(v.approved_by)}) for v in vs],
            "live": ps.configs.get(name, live.version) if live else None, "latest": ps.configs.get(name, vs[-1].version), "second_approval": ps.project.second_approval}


@router.post(DEST)
def propose_destination(request: Request, body: ProposeDestIn, project_id: str = PID, p: Principal = MANAGE):
    """Save a destination (the NAME of an environment variable holding a database URL). It is tried at once; an unreachable one is refused."""
    ps = project_scope(request, project_id)
    try:
        cfg = DestinationConfig.model_validate(body.destination)
        test_destination(database_url(cfg.url_env), cfg.schema_name)
    except ExportError as e:
        raise HTTPException(422, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, "The destination is not valid: " + "; ".join(sorted({".".join(str(x) for x in err["loc"]) for err in getattr(e, "errors", lambda: [])()})[:6])) from None
    try:
        version = ps.configs.propose(f"destination/{cfg.destination_id}", cfg.model_dump(), p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "propose_destination", f"destination/{cfg.destination_id}:{version}")
    return {"id": cfg.destination_id, "version": version, "status": "proposed", "second_approval": ps.project.second_approval}


@router.post(DEST + "/{destination_id}/test")
def test_saved_destination(request: Request, project_id: str = PID, destination_id: str = DID, p: Principal = MANAGE):
    ps = project_scope(request, project_id)
    name = f"destination/{destination_id}"
    vs = ps.configs.versions(name)
    if not vs:
        raise HTTPException(404, "Not found.")
    cfg = DestinationConfig.model_validate(ps.configs.get(name, next((v for v in vs if v.status == "active"), vs[-1]).version))
    try:
        test_destination(database_url(cfg.url_env), cfg.schema_name)
    except ExportError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "error": None}


@router.post(DEST + "/{destination_id}/versions/{version}/approve")
def approve_destination(request: Request, body: ReasonIn, project_id: str = PID, destination_id: str = DID, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE):
    ps = project_scope(request, project_id)
    try:
        ps.configs.approve(f"destination/{destination_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "approve_destination", f"destination/{destination_id}:{version}")
    return {"id": destination_id, "version": version, "approved": True}


@router.post(DEST + "/{destination_id}/versions/{version}/decline")
def decline_destination(request: Request, body: DeclineIn, project_id: str = PID, destination_id: str = DID, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE):
    ps = project_scope(request, project_id)
    try:
        ps.configs.decline(f"destination/{destination_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "decline_destination", f"destination/{destination_id}:{version}")
    return {"id": destination_id, "version": version, "declined": True}


@router.post(DEST + "/{destination_id}/versions/{version}/activate")
def activate_destination(request: Request, body: ReasonIn, project_id: str = PID, destination_id: str = DID, version: int = Path(ge=1, le=1_000_000), p: Principal = ACTIVATE):
    ps = project_scope(request, project_id)
    try:
        ps.configs.activate(f"destination/{destination_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "activate_destination", f"destination/{destination_id}:{version}")
    return {"id": destination_id, "version": version, "active": True}


__all__ = ["router"]
