"""A project's connections (saved sources with their field mapping) and its file uploads.

A connection belongs to ONE project and is versioned like the rules: whoever manages sources proposes it after a check against the real source;
when the project asks for a second person (`second_approval`), a DIFFERENT person approves it; then it is made live, and runs use only live
versions. Its id is the `source_id` stamped on every record it delivers, so decisions and person IDs stay attached to the same records run after
run. Another project's team never sees it; "copy" makes an independent copy (credentials are only the NAME of an environment variable).

Connection settings are untrusted input (see `dedupe.mapping.check`): per-type allow-list, credentials only as the NAME of an environment
variable starting with DEDUPE_SRC_, files inside DEDUPE_DATA_ROOTS or uploaded to this project, SQL as one SELECT. Looking inside a source reads
real customer data: it is rate limited, logged (never a value), and values leave the server only shortened.
"""
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from dedupe.governance.diff import describe_changes
from dedupe.governance.store import ConfigError
from dedupe.mapping.canonical import CANONICAL_FIELDS
from dedupe.mapping.check import ALLOWED_SETTINGS, SETTING_HELP, SourceProblem, check_source, check_sources, inspect_source, probe_source
from dedupe.mapping.config import SourceConfig
from dedupe.workspace import PROJECT_ID, UploadError

from .auth import Principal
from .guards import _context, _services, require
from .routes_read import PID, VersionView
from .scope import project_scope

router = APIRouter()
MANAGE = Depends(require("sources.manage"))
READ = Depends(require("config.read"))
APPROVE = Depends(require("config.approve"))
ACTIVATE = Depends(require("config.activate"))
CID = Path(pattern=r"^[A-Za-z0-9_-]{1,40}$")
BASE = "/projects/{project_id}/connections"
SAMPLE_ROWS = 5
INSPECT_LIMIT_PER_MIN = 20
UPLOAD_LIMIT_PER_MIN = 10


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InspectIn(Strict):
    connector: dict[str, str] = Field(max_length=12)


class ProposeIn(Strict):
    connection: dict
    reason: str = Field(min_length=10, max_length=300)


class CopyIn(Strict):
    from_project: str = Field(pattern=PROJECT_ID)
    connection_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    new_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,40}$")


class ReasonIn(Strict):
    reason: str = Field("", max_length=300)


class DeclineIn(Strict):
    reason: str = Field(min_length=3, max_length=300)


def mask_cell(v) -> str | None:
    if v is None or v == "":
        return None
    s = str(v)
    return "***" if len(s) <= 3 else s[:2] + "*" * min(len(s) - 3, 8) + s[-1]


def _throttle(request: Request, p: Principal, scope: str, limit: int) -> None:
    sv = _services(request)
    if not sv.limiter.allow(f"{scope}:{p.sub}", limit):
        sv.seclog.record("rate_limited", actor=p.actor, roles=p.roles, status=429, detail={"scope": scope}, **_context(request))
        raise HTTPException(429, "Too many requests of this kind; try again in a minute.", headers={"Retry-After": "60"})


def _log(request: Request, p: Principal, action: str, ref: str, **detail) -> None:
    _services(request).seclog.record("config_change", actor=p.actor, roles=p.roles, status=200, object_ref=ref, detail={"action": action, **detail},
                                     **_context(request))


def _conflict(e: Exception) -> HTTPException:
    return HTTPException(409, str(e)[:300])


def _own_upload(request: Request, project_id: str, connector: dict) -> None:
    """An `upload` connection may only point at a file uploaded to THIS project."""
    if connector.get("type") == "upload":
        owner = _services(request).uploads.project_of(connector.get("upload_id", ""))
        if owner != project_id:
            raise HTTPException(422, "That file was not uploaded to this project. Upload it here first.")


def _summary(configs, name: str) -> dict:
    vs = configs.versions(name)
    live = next((v for v in vs if v.status == "active"), None)
    content = configs.get(name, live.version) if live else configs.get(name, vs[-1].version)
    return {"id": name.split("/", 1)[1], "type": content["connector"].get("type"), "fields_mapped": len(content.get("fields") or {}),
            "live_version": live.version if live else None, "latest_version": vs[-1].version, "latest_status": vs[-1].status,
            "updated_at": vs[-1].proposed_at, "updated_by": vs[-1].proposed_by}


# ---- what the screens need to build their forms (the same for every project) -------------------------------------------------
@router.get("/connectors")
def options(p: Principal = MANAGE):
    return {"connectors": {t: {"settings": sorted(ALLOWED_SETTINGS[t] - {"type"}), "help": h} for t, h in SETTING_HELP.items()},
            "canonical_fields": list(CANONICAL_FIELDS)}


# ---- the project's connections ------------------------------------------------------------------------------------------------
@router.get(BASE)
def list_connections(request: Request, project_id: str = PID, p: Principal = READ):
    ps = project_scope(request, project_id)
    last = _services(request).health.latest(project_id)
    return [{**_summary(ps.configs, name), "last_check": last.get(name.split("/", 1)[1])} for name in ps.configs.names("connection/")]


@router.get(BASE + "/{connection_id}")
def get_connection(request: Request, project_id: str = PID, connection_id: str = CID, p: Principal = READ):
    ps = project_scope(request, project_id)
    name = f"connection/{connection_id}"
    vs = ps.configs.versions(name)
    if not vs:
        raise HTTPException(404, "Not found.")
    live = next((v for v in vs if v.status == "active"), None)
    return {"id": connection_id, "versions": [VersionView(**{**v.__dict__, "approved_by": list(v.approved_by)}) for v in vs],
            "live": ps.configs.get(name, live.version) if live else None, "latest": ps.configs.get(name, vs[-1].version),
            "second_approval": ps.project.second_approval, "health": _services(request).health.history(project_id, connection_id)}


@router.get(BASE + "/{connection_id}/diff", response_model=list[str])
def diff(request: Request, project_id: str = PID, connection_id: str = CID, old: int = Query(ge=1, le=1_000_000), new: int = Query(ge=1, le=1_000_000),
         p: Principal = READ):
    ps = project_scope(request, project_id)
    name = f"connection/{connection_id}"
    try:
        return describe_changes(name, ps.configs.get(name, old), ps.configs.get(name, new))
    except ConfigError:
        raise HTTPException(404, "Not found.") from None


@router.post(BASE + "/{connection_id}/test")
def test_connection(request: Request, project_id: str = PID, connection_id: str = CID, p: Principal = MANAGE):
    """Open the source with its live settings (or the newest version when none is live) and see whether it still reads and maps. The result is
    recorded in the connection's health: rows sampled, and any columns added or removed since the last good check."""
    ps = project_scope(request, project_id)
    _throttle(request, p, "source_inspect", INSPECT_LIMIT_PER_MIN)
    name = f"connection/{connection_id}"
    vs = ps.configs.versions(name)
    if not vs:
        raise HTTPException(404, "Not found.")
    version = next((v for v in vs if v.status == "active"), vs[-1]).version
    health = _services(request).health
    try:
        src = SourceConfig.model_validate(ps.configs.get(name, version))
        _own_upload(request, project_id, src.connector)
        probe = probe_source(src)
    except (SourceProblem, HTTPException, ValueError) as e:
        text = e.detail if isinstance(e, HTTPException) else str(e)
        result = health.record(project_id, connection_id, version, "test", ok=False, error=str(text)[:300], actor=p.actor)
    else:
        result = health.record(project_id, connection_id, version, "test", ok=True, rows=probe["rows_sampled"], columns=probe["columns"], actor=p.actor)
    _log(request, p, "source_test", f"connection/{connection_id}:{version}", ok=result["ok"])
    return result


# ---- looking inside a source ----------------------------------------------------------------------------------------------
@router.post(BASE + "/inspect")
def inspect(request: Request, body: InspectIn, project_id: str = PID, p: Principal = MANAGE):
    """Open the source, list its columns, suggest a mapping, and show a few MASKED sample rows so a person can recognise the columns."""
    project_scope(request, project_id)
    _throttle(request, p, "source_inspect", INSPECT_LIMIT_PER_MIN)
    _own_upload(request, project_id, body.connector)
    try:
        info = inspect_source(body.connector)
    except SourceProblem as e:
        raise HTTPException(422, str(e)) from None
    _log(request, p, "source_inspect", body.connector.get("type", ""))
    return {"columns": info["columns"], "suggestions": info["suggestions"],
            "sample": [{k: mask_cell(v) for k, v in row.items()} for row in info["sample"].slice(0, SAMPLE_ROWS).to_pylist()]}


@router.post(BASE + "/preview")
def preview(request: Request, body: SourceConfig, project_id: str = PID, p: Principal = MANAGE):
    """Apply the mapping to a sample: which canonical fields get filled, and a few masked canonical rows."""
    project_scope(request, project_id)
    _throttle(request, p, "source_inspect", INSPECT_LIMIT_PER_MIN)
    _own_upload(request, project_id, body.connector)
    try:
        table = check_source(body)
    except SourceProblem as e:
        raise HTTPException(422, str(e)) from None
    n = table.num_rows
    filled = {f: sum(1 for v in table.column(f).to_pylist() if v not in (None, "")) for f in CANONICAL_FIELDS if f in body.fields}
    _log(request, p, "source_preview", body.source_id[:40])
    rows = [{k: (v if k == "source_id" else mask_cell(v)) for k, v in row.items()} for row in table.slice(0, SAMPLE_ROWS).to_pylist()]
    return {"sample_size": n, "filled": filled, "rows": rows}


# ---- propose, approve, make live ---------------------------------------------------------------------------------------------
@router.post(BASE)
def propose(request: Request, body: ProposeIn, project_id: str = PID, p: Principal = MANAGE):
    """Check the connection against the real source, then propose it as its next version. The connection id is its `source_id`."""
    ps = project_scope(request, project_id)
    _own_upload(request, project_id, body.connection.get("connector") or {})
    problems = check_sources({"sources": [body.connection]})
    if problems:
        raise HTTPException(422, "The connection is not valid: " + "; ".join(x["problem"] for x in problems[:8]))
    cid = body.connection["source_id"]
    try:
        version = ps.configs.propose(f"connection/{cid}", body.connection, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "propose", f"connection/{cid}:{version}")
    nxt = "make it live" if not ps.project.second_approval else "another person approves it, then makes it live"
    return {"id": cid, "version": version, "status": "proposed", "next": nxt, "second_approval": ps.project.second_approval}


@router.post(BASE + "/copy", status_code=201)
def copy(request: Request, body: CopyIn, project_id: str = PID, p: Principal = MANAGE):
    """Copy a connection from another project you can open into this one, as its own new version. The copy is independent: later changes to
    either do not affect the other. Uploaded files stay with the project that uploaded them, so those connections cannot be copied."""
    target = project_scope(request, project_id)
    source = project_scope(request, body.from_project)
    src_name = f"connection/{body.connection_id}"
    vs = source.configs.versions(src_name)
    if not vs:
        raise HTTPException(404, "Not found.")
    live = next((v for v in vs if v.status == "active"), vs[-1])
    content = dict(source.configs.get(src_name, live.version))
    if (content.get("connector") or {}).get("type") == "upload":
        raise HTTPException(422, "A connection to an uploaded file cannot be copied: upload the file to this project instead.")
    new_id = body.new_id or body.connection_id
    content["source_id"] = new_id
    try:
        version = target.configs.propose(f"connection/{new_id}", content, p.actor, f"copied from {body.from_project}/{body.connection_id} version {live.version}")
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "copy", f"connection/{new_id}:{version}", from_project=body.from_project)
    return {"id": new_id, "version": version, "status": "proposed"}


@router.post(BASE + "/{connection_id}/versions/{version}/approve")
def approve(request: Request, body: ReasonIn, project_id: str = PID, connection_id: str = CID, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE):
    ps = project_scope(request, project_id)
    try:
        ps.configs.approve(f"connection/{connection_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "approve", f"connection/{connection_id}:{version}")
    return {"id": connection_id, "version": version, "approved": True}


@router.post(BASE + "/{connection_id}/versions/{version}/decline")
def decline(request: Request, body: DeclineIn, project_id: str = PID, connection_id: str = CID, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE):
    ps = project_scope(request, project_id)
    try:
        ps.configs.decline(f"connection/{connection_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "decline", f"connection/{connection_id}:{version}")
    return {"id": connection_id, "version": version, "declined": True}


@router.post(BASE + "/{connection_id}/versions/{version}/activate")
def activate(request: Request, body: ReasonIn, project_id: str = PID, connection_id: str = CID, version: int = Path(ge=1, le=1_000_000), p: Principal = ACTIVATE):
    ps = project_scope(request, project_id)
    try:
        ps.configs.activate(f"connection/{connection_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "activate", f"connection/{connection_id}:{version}")
    return {"id": connection_id, "version": version, "active": True, "applies": "to runs started from now on"}


# ---- uploads ------------------------------------------------------------------------------------------------------------------
@router.post("/projects/{project_id}/uploads", status_code=201)
async def upload(request: Request, project_id: str = PID, filename: str = Query(min_length=1, max_length=255), p: Principal = MANAGE):
    """The file is the request body (any content type). It is checked, converted to a table and stored encrypted; the original bytes are deleted.
    The size cap is DEDUPE_UPLOAD_MAX_BYTES (enforced while the body arrives). The file belongs to this project."""
    project_scope(request, project_id)
    _throttle(request, p, "upload", UPLOAD_LIMIT_PER_MIN)
    sv = _services(request)
    raw = sv.uploads.incoming()
    try:
        with open(raw, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        info = await run_in_threadpool(sv.uploads.store, raw, filename, p.actor, project_id)
    except UploadError as e:
        raise HTTPException(422, str(e)) from None
    finally:
        raw.unlink(missing_ok=True)
    _log(request, p, "upload", info["upload_id"])
    return info


@router.get("/projects/{project_id}/uploads")
def list_uploads(request: Request, project_id: str = PID, p: Principal = MANAGE):
    project_scope(request, project_id)
    return _services(request).uploads.list(project_id)


__all__ = ["router", "mask_cell"]
