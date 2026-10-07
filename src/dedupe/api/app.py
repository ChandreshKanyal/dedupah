"""The HTTP API. Security first: every route declares its access through `guards`, requests pass the defences in `middleware`, errors never
echo the caller's input, and security-relevant events go to the tamper-evident security log (`seclog`).

    python -m dedupe.api            (development: DEDUPE_DEV_AUTH_SECRET and tokens from `python -m dedupe.api.devtoken`)
"""
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from dedupe import crypto
from dedupe.workspace import RunJobs, Uploads, Workspace

from .accounts import AccountStore
from .auth import Principal, TokenVerifier
from .guards import authenticated, public, require
from .middleware import BodyLimitMiddleware, ContextMiddleware, HeadersMiddleware, SlidingWindow
from .routes_admin import router as admin_router
from .routes_connections import router as connections_router
from .routes_projects import router as projects_router
from .routes_read import router as read_router
from .routes_ui import router as ui_router
from .routes_write import router as write_router
from .seclog import EVENTS, SecurityLog
from .settings import ApiSettings

log = logging.getLogger("dedupe.api")


@dataclass
class Services:
    settings: ApiSettings
    verifier: TokenVerifier
    seclog: SecurityLog
    limiter: SlidingWindow
    workspace: Workspace
    uploads: Uploads
    jobs: RunJobs
    accounts: AccountStore                         # local users and custom roles (main database)
    reviews: dict = field(default_factory=dict)    # (project, run, reveal) -> ReviewService, built on first use


class Me(BaseModel):
    sub: str
    username: str
    roles: list[str]
    permissions: list[str]


class SecurityEvent(BaseModel):
    id: int
    created_at: str
    event: str
    actor: str
    roles: str
    method: str
    route: str
    status: int | None
    object_ref: str
    detail: dict
    request_id: str
    client_ip: str
    project_id: str = ""


ANY_USER = Depends(authenticated())
READ_LOGS = Depends(require("logs.read"))
PUBLIC = Depends(public())


def _error(status: int, code: str, message: str, request: Request, headers: dict | None = None) -> JSONResponse:
    rid = request.scope.get("state", {}).get("request_id", "")
    return JSONResponse({"error": {"code": code, "message": message, "request_id": rid}}, status_code=status, headers=headers)


def create_app(settings: ApiSettings | None = None, *, verifier: TokenVerifier | None = None, seclog: SecurityLog | None = None,
               workspace: Workspace | None = None, command=None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    settings.validate()
    crypto.bind_work(settings.work)                   # a missing or wrong key stops the API at start-up, not on the first request
    db = settings.database_url or f"sqlite:///{settings.work}/api.db"
    ws = workspace or Workspace(settings.work, db, settings.project_database_url)
    os.environ["DEDUPE_UPLOAD_DIR"] = str(ws.upload_dir)      # where `upload` connections find their files (inspect, preview)
    jobs = RunJobs(ws, command) if command else RunJobs(ws)
    accounts = AccountStore(ws.engine)
    sv = Services(settings, verifier or TokenVerifier(settings, accounts=accounts), seclog or SecurityLog.open(ws.url), SlidingWindow(), ws,
                  Uploads(ws.engine, ws.upload_dir), jobs, accounts)
    jobs.on_finish = lambda run: sv.seclog.record("run_finished", status=None, object_ref=f"{run.project_id}/{run.id}",
                                                  detail={"exit_code": run.exit_code, "outcome": run.status})

    @asynccontextmanager
    async def lifespan(app):
        sv.seclog.record("startup", detail={"auth_mode": settings.auth_mode, "env": settings.env})
        if settings.auth_mode == "local":
            log.warning("LOCAL PASSWORD SIGN-IN IS ON. Prefer your identity provider (DEDUPE_AUTH_MODE=oidc) for real data.")
        if settings.auth_mode == "dev":
            log.warning("DEV AUTHENTICATION IS ON: tokens are signed with a shared secret. Never use this with real data.")
        yield

    docs = settings.docs_enabled
    app = FastAPI(title="Dedupe API", version="0.1.0", lifespan=lifespan, docs_url="/docs" if docs else None,
                  redoc_url=None, openapi_url="/openapi.json" if docs else None)
    app.state.services = sv

    def on_event(name, scope, rid, ip, detail):
        sv.seclog.record(name, method=scope.get("method", ""), status=None, detail=detail, request_id=rid, client_ip=ip)

    # last added = outermost: headers wrap everything (including the error responses of the layers inside), then context, then size cap
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST"],
                           allow_headers=["authorization", "content-type", "x-request-id"], allow_credentials=False)
    if "*" not in settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes, on_event=on_event, overrides={r"/projects/[a-z0-9-]{2,40}/uploads": settings.upload_max_bytes})
    app.add_middleware(ContextMiddleware, settings=settings, limiter=sv.limiter, on_event=on_event)
    app.add_middleware(HeadersMiddleware, production=settings.production)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}.get(exc.status_code, "error")
        return _error(exc.status_code, code, exc.detail if isinstance(exc.detail, str) else "Request failed.", request, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # which fields are wrong, never the values: a rejected value may be personal data
        fields = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "problem": e["type"]} for e in exc.errors()]
        body = {"error": {"code": "invalid_request", "message": "The request is not valid.", "fields": fields,
                          "request_id": request.scope.get("state", {}).get("request_id", "")}}
        return JSONResponse(body, status_code=422)

    # ---- routes (each one declares its access) -------------------------------------------------------------------
    @app.get("/health", dependencies=[PUBLIC])
    def health():
        return {"status": "ok"}

    @app.get("/me", response_model=Me)
    def me(p: Principal = ANY_USER):
        return Me(sub=p.sub, username=p.username, roles=sorted(p.roles), permissions=sorted(p.permissions))

    @app.get("/security/events", response_model=list[SecurityEvent])
    def security_events(after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1), event: str | None = Query(None, pattern=r"^[a-z_]{1,30}$"),
                        p: Principal = READ_LOGS):
        if event is not None and event not in EVENTS:
            return []
        return sv.seclog.events(after_id, min(limit, settings.max_page_size), event)

    @app.get("/security/verify")
    def security_verify(p: Principal = READ_LOGS):
        return sv.seclog.verify()

    app.include_router(admin_router)
    app.include_router(projects_router)
    app.include_router(read_router)
    app.include_router(write_router)
    app.include_router(connections_router)
    app.include_router(ui_router)
    return app
