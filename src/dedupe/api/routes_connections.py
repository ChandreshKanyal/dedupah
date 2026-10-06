"""Connections (saved sources with their field mapping) and file uploads.

A connection is shared by all projects and versioned like the rules: whoever manages sources proposes it after a check against the real source,
a DIFFERENT person approves it, then it is made live; runs use only live versions. Its id is the `source_id` stamped on every record it
delivers, so decisions and person IDs stay attached to the same records run after run.

Connection settings are untrusted input (see `dedupe.mapping.check`): per-type allow-list, credentials only as the NAME of an environment
variable starting with DEDUPE_SRC_, files inside DEDUPE_DATA_ROOTS or uploaded through `/uploads`, SQL as one SELECT. Looking inside a source
reads real customer data: it is rate limited, logged (never a value), and values leave the server only shortened.
"""
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from dedupe.governance.diff import describe_changes
from dedupe.governance.store import ConfigError
from dedupe.mapping.canonical import CANONICAL_FIELDS
from dedupe.mapping.check import ALLOWED_SETTINGS, SETTING_HELP, SourceProblem, check_source, check_sources, inspect_source
from dedupe.mapping.config import SourceConfig
from dedupe.workspace import UploadError

from .auth import Principal
from .guards import _context, _services, require
from .routes_read import VersionView

router = APIRouter()
MANAGE = Depends(require("sources.manage"))
READ = Depends(require("config.read"))
APPROVE = Depends(require("config.approve"))
ACTIVATE = Depends(require("config.activate"))
CID = Path(pattern=r"^[A-Za-z0-9_-]{1,40}$")
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


def _log(request: Request, p: Principal, action: str, ref: str) -> None:
    _services(request).seclog.record("config_change", actor=p.actor, roles=p.roles, status=200, object_ref=ref, detail={"action": action}, **_context(request))


def _conflict(e: Exception) -> HTTPException:
    return HTTPException(409, str(e)[:300])


# ---- what the screens need to build their forms ----------------------------------------------------------------------------
@router.get("/connections/options")
def options(p: Principal = MANAGE):
    return {"connectors": {t: {"settings": sorted(ALLOWED_SETTINGS[t] - {"type"}), "help": h} for t, h in SETTING_HELP.items()},
            "canonical_fields": list(CANONICAL_FIELDS)}


# ---- the catalogue of connections -------------------------------------------------------------------------------------------
@router.get("/connections")
def list_connections(request: Request, p: Principal = READ):
    store = _services(request).connections
    out = []
    for name in store.names("connection/"):
        vs = store.versions(name)
        live = next((v for v in vs if v.status == "active"), None)
        content = store.get(name, live.version) if live else store.get(name, vs[-1].version)
        out.append({"id": name.split("/", 1)[1], "type": content["connector"].get("type"), "fields_mapped": len(content.get("fields") or {}),
                    "live_version": live.version if live else None, "latest_version": vs[-1].version, "latest_status": vs[-1].status,
                    "updated_at": vs[-1].proposed_at, "updated_by": vs[-1].proposed_by})
    return out


@router.get("/connections/{connection_id}")
def get_connection(request: Request, connection_id: str = CID, p: Principal = READ):
    store = _services(request).connections
    name = f"connection/{connection_id}"
    vs = store.versions(name)
    if not vs:
        raise HTTPException(404, "Not found.")
    live = next((v for v in vs if v.status == "active"), None)
    return {"id": connection_id, "versions": [VersionView(**{**v.__dict__, "approved_by": list(v.approved_by)}) for v in vs],
            "live": store.get(name, live.version) if live else None, "latest": store.get(name, vs[-1].version)}


@router.get("/connections/{connection_id}/diff", response_model=list[str])
def diff(request: Request, connection_id: str = CID, old: int = Query(ge=1, le=1_000_000), new: int = Query(ge=1, le=1_000_000), p: Principal = READ):
    store, name = _services(request).connections, f"connection/{connection_id}"
    try:
        return describe_changes(name, store.get(name, old), store.get(name, new))
    except ConfigError:
        raise HTTPException(404, "Not found.") from None


# ---- looking inside a source ----------------------------------------------------------------------------------------------
@router.post("/connections/inspect")
def inspect(request: Request, body: InspectIn, p: Principal = MANAGE):
    """Open the source, list its columns, suggest a mapping, and show a few MASKED sample rows so a person can recognise the columns."""
    _throttle(request, p, "source_inspect", INSPECT_LIMIT_PER_MIN)
    try:
        info = inspect_source(body.connector)
    except SourceProblem as e:
        raise HTTPException(422, str(e)) from None
    _log(request, p, "source_inspect", body.connector.get("type", ""))
    return {"columns": info["columns"], "suggestions": info["suggestions"],
            "sample": [{k: mask_cell(v) for k, v in row.items()} for row in info["sample"].slice(0, SAMPLE_ROWS).to_pylist()]}


@router.post("/connections/preview")
def preview(request: Request, body: SourceConfig, p: Principal = MANAGE):
    """Apply the mapping to a sample: which canonical fields get filled, and a few masked canonical rows."""
    _throttle(request, p, "source_inspect", INSPECT_LIMIT_PER_MIN)
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
@router.post("/connections")
def propose(request: Request, body: ProposeIn, p: Principal = MANAGE):
    """Check the connection against the real source, then propose it as its next version. The connection id is its `source_id`."""
    problems = check_sources({"sources": [body.connection]})
    if problems:
        raise HTTPException(422, "The connection is not valid: " + "; ".join(x["problem"] for x in problems[:8]))
    cid = body.connection["source_id"]
    try:
        version = _services(request).connections.propose(f"connection/{cid}", body.connection, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "propose", f"connection/{cid}:{version}")
    return {"id": cid, "version": version, "status": "proposed", "next": "another person approves it, then makes it live"}


@router.post("/connections/{connection_id}/versions/{version}/approve")
def approve(request: Request, body: ReasonIn, connection_id: str = CID, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE):
    try:
        _services(request).connections.approve(f"connection/{connection_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "approve", f"connection/{connection_id}:{version}")
    return {"id": connection_id, "version": version, "approved": True}


@router.post("/connections/{connection_id}/versions/{version}/decline")
def decline(request: Request, body: DeclineIn, connection_id: str = CID, version: int = Path(ge=1, le=1_000_000), p: Principal = APPROVE):
    try:
        _services(request).connections.decline(f"connection/{connection_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "decline", f"connection/{connection_id}:{version}")
    return {"id": connection_id, "version": version, "declined": True}


@router.post("/connections/{connection_id}/versions/{version}/activate")
def activate(request: Request, body: ReasonIn, connection_id: str = CID, version: int = Path(ge=1, le=1_000_000), p: Principal = ACTIVATE):
    try:
        _services(request).connections.activate(f"connection/{connection_id}", version, p.actor, body.reason)
    except ConfigError as e:
        raise _conflict(e) from None
    _log(request, p, "activate", f"connection/{connection_id}:{version}")
    return {"id": connection_id, "version": version, "active": True, "applies": "to runs started from now on"}


# ---- uploads ------------------------------------------------------------------------------------------------------------------
@router.post("/uploads", status_code=201)
async def upload(request: Request, filename: str = Query(min_length=1, max_length=255), p: Principal = MANAGE):
    """The file is the request body (any content type). It is checked, converted to a table and stored encrypted; the original bytes are deleted.
    The size cap is DEDUPE_UPLOAD_MAX_BYTES (enforced while the body arrives)."""
    _throttle(request, p, "upload", UPLOAD_LIMIT_PER_MIN)
    sv = _services(request)
    raw = sv.uploads.incoming()
    try:
        with open(raw, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        info = await run_in_threadpool(sv.uploads.store, raw, filename, p.actor)
    except UploadError as e:
        raise HTTPException(422, str(e)) from None
    finally:
        raw.unlink(missing_ok=True)
    _log(request, p, "upload", info["upload_id"])
    return info


@router.get("/uploads")
def list_uploads(request: Request, p: Principal = MANAGE):
    return _services(request).uploads.list()


__all__ = ["router", "mask_cell"]
