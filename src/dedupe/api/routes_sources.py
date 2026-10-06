"""Sources and field mapping, managed from the screens: look inside a source, map its columns to the canonical fields, preview the result
(values masked), and propose the configuration. A proposed `sources` configuration goes through the same propose -> approve (another person)
-> activate steps as the rules, and a run uses the ACTIVE version.

Source settings are untrusted input (see `dedupe.mapping.check`): connector settings come from an allow-list, credentials are only the NAME of an
environment variable that starts with DEDUPE_SRC_, files stay inside DEDUPE_DATA_ROOTS, SQL is one SELECT. Inspecting and previewing read real
customer data, so they are logged (who, which source id, never a value), rate limited, and only masked fragments of values are returned.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from dedupe.governance.store import ConfigError
from dedupe.mapping.canonical import CANONICAL_FIELDS
from dedupe.mapping.check import ALLOWED_SETTINGS, SETTING_HELP, SourceProblem, check_source, check_sources, inspect_source
from dedupe.mapping.config import SourceConfig

from .auth import Principal
from .guards import _context, _services, require

router = APIRouter()
MANAGE = Depends(require("sources.manage"))
SAMPLE_ROWS = 5
INSPECT_LIMIT_PER_MIN = 20


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InspectIn(Strict):
    connector: dict[str, str] = Field(max_length=12)


class ProposeSourcesIn(Strict):
    content: dict
    reason: str = Field(min_length=10, max_length=300)


def mask_cell(v) -> str | None:
    if v is None or v == "":
        return None
    s = str(v)
    return "***" if len(s) <= 3 else s[:2] + "*" * min(len(s) - 3, 8) + s[-1]


def _masked_rows(table, skip=("source_id",)) -> list[dict]:
    return [{k: (v if k in skip else mask_cell(v)) for k, v in row.items()} for row in table.slice(0, SAMPLE_ROWS).to_pylist()]


def _throttle(request: Request, p: Principal) -> None:
    sv = _services(request)
    if not sv.limiter.allow(f"inspect:{p.sub}", INSPECT_LIMIT_PER_MIN):
        sv.seclog.record("rate_limited", actor=p.actor, roles=p.roles, status=429, detail={"scope": "source_inspect"}, **_context(request))
        raise HTTPException(429, "Too many requests to read sources.", headers={"Retry-After": "60"})


def _log(request: Request, p: Principal, action: str, ref: str) -> None:
    _services(request).seclog.record("config_change", actor=p.actor, roles=p.roles, status=200, object_ref=ref, detail={"action": action}, **_context(request))


@router.get("/sources/options")
def options(p: Principal = MANAGE):
    """What the screen needs to build its form: connector types with their settings, and the canonical fields to map onto."""
    return {"connectors": {t: {"settings": sorted(ALLOWED_SETTINGS[t] - {"type"}), "help": h} for t, h in SETTING_HELP.items()}, "canonical_fields": list(CANONICAL_FIELDS),
            "expressions": ["a column name", "{coalesce: [cols]}", "{concat: [cols], sep}", "{column, when: {col: value}}", "{const: value}"]}


@router.post("/sources/inspect")
def inspect(request: Request, body: InspectIn, p: Principal = MANAGE):
    """Open the source, list its columns, suggest a mapping, and show a few MASKED sample rows so a person can recognise the columns."""
    _throttle(request, p)
    try:
        info = inspect_source(body.connector)
    except SourceProblem as e:
        raise HTTPException(422, str(e)) from None
    _log(request, p, "source_inspect", body.connector.get("type", ""))
    return {"columns": info["columns"], "suggestions": info["suggestions"],
            "sample": [{k: mask_cell(v) for k, v in row.items()} for row in info["sample"].slice(0, SAMPLE_ROWS).to_pylist()]}


@router.post("/sources/preview")
def preview(request: Request, body: SourceConfig, p: Principal = MANAGE):
    """Apply the mapping to a sample: which canonical fields get filled, and a few masked canonical rows."""
    _throttle(request, p)
    try:
        table = check_source(body)
    except SourceProblem as e:
        raise HTTPException(422, str(e)) from None
    n = table.num_rows
    filled = {f: sum(1 for v in table.column(f).to_pylist() if v not in (None, "")) for f in CANONICAL_FIELDS if f in body.fields}
    _log(request, p, "source_preview", body.source_id[:40])
    return {"sample_size": n, "filled": filled, "rows": _masked_rows(table)}


@router.post("/sources/propose")
def propose(request: Request, body: ProposeSourcesIn, p: Principal = MANAGE):
    """Check the WHOLE configuration (every source reachable, every mapped column present) and propose it as the next `sources` version."""
    problems = check_sources(body.content)
    if problems:
        raise HTTPException(422, "The sources are not valid: " + "; ".join((f"{x['source_id']}: " if x["source_id"] else "") + x["problem"] for x in problems[:8]))
    sv = _services(request)
    if sv.configs is None:
        raise HTTPException(404, "Not found.")
    try:
        version = sv.configs.propose("sources", body.content, p.actor, body.reason)
    except ConfigError as e:
        raise HTTPException(409, str(e)[:300]) from None
    _log(request, p, "propose", f"sources:{version}")
    return {"name": "sources", "version": version, "status": "proposed", "next": "another person approves it, then it is made live; a run uses the live version"}
