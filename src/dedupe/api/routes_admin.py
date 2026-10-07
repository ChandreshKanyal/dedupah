"""Sign-in with a username and password (local mode), and administration of users and roles.

    GET  /auth/config            public: which sign-in the screens should offer
    POST /auth/login             public: username + password -> a short-lived signed token (local mode only)
    POST /auth/password          change your own password (the only thing a user with a temporary password may do)
    GET  /roles                  every role and what it allows (anyone signed in; this is not secret)
    POST/PUT/DELETE /roles       custom roles (roles.manage)
    GET/POST/PATCH /users, POST /users/{u}/password   users (users.manage)

Rules: nobody can give more access than they hold; you cannot change your own roles, disable yourself or reset your own password through the
administrator routes (use /auth/password); the last enabled user who can manage users cannot be removed; a custom role in use cannot be deleted.
Passwords never appear in any log or response.
"""
import time
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from dedupe.workspace.projects import project_grants

from .accounts import ROLE_NAME, USERNAME, AccountError, can_grant
from .auth import DEV_AUDIENCE, LOCAL_ISSUER, Principal
from .guards import _context, _services, authenticated, public, require
from .roles import PERMISSIONS

router = APIRouter()
ANY = Depends(authenticated())
OPEN = Depends(public())
MANAGE_USERS = Depends(require("users.manage"))
MANAGE_ROLES = Depends(require("roles.manage"))
LOGIN_PER_MIN_PER_IP = 10
BAD_LOGIN = "Incorrect username or password."


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginOut(BaseModel):
    token: str
    expires_in: int
    must_change_password: bool


class PasswordIn(Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserIn(Strict):
    username: str = Field(pattern=USERNAME)
    display_name: str = Field("", max_length=100)
    password: str = Field(min_length=1, max_length=256)                  # a temporary password; the user must change it at first sign-in
    roles: list[str] = Field(min_length=1, max_length=10)


class UserPatch(Strict):
    display_name: str | None = Field(None, max_length=100)
    roles: list[str] | None = Field(None, min_length=1, max_length=10)
    disabled: bool | None = None


class ResetIn(Strict):
    password: str = Field(min_length=1, max_length=256)


class RoleIn(Strict):
    name: str = Field(pattern=ROLE_NAME)
    description: str = Field("", max_length=300)
    permissions: list[str] = Field(min_length=1, max_length=len(PERMISSIONS))


class RolePut(Strict):
    description: str = Field("", max_length=300)
    permissions: list[str] = Field(min_length=1, max_length=len(PERMISSIONS))


class UserView(BaseModel):
    username: str
    display_name: str
    roles: list[str]
    disabled: bool
    must_change_password: bool
    locked: bool
    last_login_at: str | None
    created_by: str
    created_at: str


def _view(u) -> UserView:
    return UserView(username=u.username, display_name=u.display_name, roles=list(u.roles), disabled=u.disabled, must_change_password=u.must_change,
                    locked=u.locked_until > time.time(), last_login_at=u.last_login_at, created_by=u.created_by, created_at=u.created_at)


def _local(request: Request):
    sv = _services(request)
    if sv.settings.auth_mode != "local":
        raise HTTPException(404, "Not found.")
    return sv


def _token(settings, username: str, epoch: int) -> tuple[str, int]:
    now = int(time.time())
    life = settings.max_token_lifetime_s
    payload = {"iss": LOCAL_ISSUER, "aud": DEV_AUDIENCE, "sub": username, "ep": epoch, "iat": now, "exp": now + life, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.local_secret, algorithm="HS256"), life


def _audit(request, p, status: int, ref: str, **detail):
    _services(request).seclog.record("config_change", actor=p.actor, roles=sorted(p.roles), status=status, object_ref=ref, detail=detail, **_context(request))


def _refuse(code: int, message: str):
    raise HTTPException(code, message)


def _known_permissions(permissions: list[str]) -> None:
    bad = [x for x in permissions if x not in PERMISSIONS]
    if bad:
        _refuse(422, "Unknown permission: " + ", ".join(bad)[:100])


def _may_assign(sv, p: Principal, roles: list[str]) -> None:
    try:
        wanted = sv.accounts.permissions_of(roles)
        sv.accounts._check_roles_exist(roles)
    except AccountError as e:
        _refuse(422, str(e))
    if not can_grant(p.permissions, wanted):
        _refuse(403, "You cannot give someone more access than you have yourself.")


def _may_manage(sv, p: Principal, target) -> None:
    if not can_grant(p.permissions, sv.accounts.permissions_of(target.roles)):
        _refuse(403, "You cannot manage a user who has more access than you.")


# ---- sign-in ---------------------------------------------------------------------------------------------------------------
@router.get("/auth/config")
def auth_config(request: Request, _: None = OPEN):
    mode = _services(request).settings.auth_mode
    return {"mode": mode, "password_login": mode == "local"}


@router.post("/auth/login", response_model=LoginOut)
def login(request: Request, body: LoginIn, _: None = OPEN):
    sv = _local(request)
    ctx = _context(request)
    if not sv.limiter.allow(f"login:{ctx['client_ip']}", LOGIN_PER_MIN_PER_IP):
        sv.seclog.record("rate_limited", status=429, detail={"scope": "login"}, **ctx)
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute.", headers={"Retry-After": "60"})
    user, why = sv.accounts.authenticate(body.username.strip().lower(), body.password)
    if user is None:
        sv.limiter.allow(f"authfail:{ctx['client_ip']}", 10**9)
        sv.seclog.record("auth_failed", actor="" if why == "unknown" else body.username.strip().lower(), status=401, detail={"reason": "bad_credentials", "why": why}, **ctx)
        raise HTTPException(401, BAD_LOGIN)
    token, life = _token(sv.settings, user.username, user.epoch)
    sv.seclog.record("login", actor=user.username, roles=list(user.roles), status=200, detail={"must_change_password": user.must_change}, **ctx)
    return LoginOut(token=token, expires_in=life, must_change_password=user.must_change)


@router.post("/auth/password", response_model=LoginOut)
def change_password(request: Request, body: PasswordIn, p: Principal = ANY):
    sv = _local(request)
    user, _why = sv.accounts.authenticate(p.sub, body.current_password)          # counts as an attempt: a stolen token cannot guess the password freely
    if user is None:
        raise HTTPException(403, "The current password is not correct.")
    if body.new_password == body.current_password:
        raise HTTPException(422, "Choose a password different from the current one.")
    try:
        fresh = sv.accounts.set_password(p.sub, body.new_password, must_change=False)
    except AccountError as e:
        raise HTTPException(422, str(e)) from None
    _audit(request, p, 200, p.sub, action="change_password")
    token, life = _token(sv.settings, p.sub, fresh.epoch)
    return LoginOut(token=token, expires_in=life, must_change_password=False)


# ---- roles -----------------------------------------------------------------------------------------------------------------
@router.get("/roles")
def list_roles(request: Request, p: Principal = ANY):
    sv = _services(request)
    return {"permissions": [{"name": k, "description": v} for k, v in PERMISSIONS.items()],
            "roles": [{"name": r.name, "description": r.description, "permissions": sorted(r.permissions), "builtin": r.builtin, "users": r.users,
                       "updated_by": r.updated_by, "updated_at": r.updated_at} for r in sv.accounts.roles()]}


@router.post("/roles", status_code=201)
def create_role(request: Request, body: RoleIn, p: Principal = MANAGE_ROLES):
    sv = _services(request)
    _known_permissions(body.permissions)
    if not can_grant(p.permissions, body.permissions):
        _refuse(403, "You cannot create a role with more access than you have yourself.")
    try:
        r = sv.accounts.create_role(body.name, body.description, body.permissions, p.actor)
    except AccountError as e:
        raise HTTPException(409 if "exists" in str(e) or "built-in" in str(e) else 422, str(e)) from None
    _audit(request, p, 201, r.name, action="create_role", permissions=sorted(r.permissions))
    return {"name": r.name, "permissions": sorted(r.permissions)}


@router.put("/roles/{role}")
def update_role(request: Request, body: RolePut, role: str = Path(pattern=ROLE_NAME), p: Principal = MANAGE_ROLES):
    sv = _services(request)
    _known_permissions(body.permissions)
    if not can_grant(p.permissions, body.permissions):
        _refuse(403, "You cannot give a role more access than you have yourself.")
    if not sv.accounts.administrators(perms_override={role: body.permissions}) and sv.accounts.administrators():
        _refuse(409, "That change would leave nobody able to manage users.")
    try:
        r = sv.accounts.update_role(role, body.description, body.permissions, p.actor)
    except AccountError as e:
        raise HTTPException(404 if "No such" in str(e) else 409 if "Built-in" in str(e) else 422, str(e)) from None
    _audit(request, p, 200, role, action="update_role", permissions=sorted(r.permissions))
    return {"name": r.name, "permissions": sorted(r.permissions)}


@router.delete("/roles/{role}", status_code=204)
def delete_role(request: Request, role: str = Path(pattern=ROLE_NAME), p: Principal = MANAGE_ROLES):
    sv = _services(request)
    with sv.workspace.engine.connect() as c:
        granted = c.execute(select(func.count()).select_from(project_grants).where(project_grants.c.role == role)).scalar() or 0
    try:
        sv.accounts.delete_role(role, in_use_elsewhere=granted)
    except AccountError as e:
        raise HTTPException(404 if "No such" in str(e) else 409, str(e)) from None
    _audit(request, p, 204, role, action="delete_role")


# ---- users -----------------------------------------------------------------------------------------------------------------
@router.get("/users", response_model=list[UserView])
def list_users(request: Request, p: Principal = MANAGE_USERS):
    return [_view(u) for u in _services(request).accounts.list_users()]


@router.post("/users", response_model=UserView, status_code=201)
def create_user(request: Request, body: UserIn, p: Principal = MANAGE_USERS):
    sv = _services(request)
    _may_assign(sv, p, body.roles)
    try:
        u = sv.accounts.create_user(body.username, body.display_name, body.password, body.roles, p.actor, must_change=True)
    except AccountError as e:
        raise HTTPException(409 if "exists" in str(e) else 422, str(e)) from None
    _audit(request, p, 201, u.username, action="create_user", roles=list(u.roles))
    return _view(u)


@router.patch("/users/{username}", response_model=UserView)
def patch_user(request: Request, body: UserPatch, username: str = Path(pattern=USERNAME), p: Principal = MANAGE_USERS):
    sv = _services(request)
    target = sv.accounts.get(username)
    if target is None:
        raise HTTPException(404, "Not found.")
    if username == p.sub and (body.roles is not None or body.disabled is not None):
        _refuse(409, "You cannot change your own roles or disable yourself.")
    _may_manage(sv, p, target)
    if body.roles is not None:
        _may_assign(sv, p, body.roles)
    new_roles = body.roles if body.roles is not None else list(target.roles)
    disabling = bool(body.disabled) and not target.disabled
    if (body.roles is not None or disabling) and sv.accounts.administrators():
        left = [u for u in sv.accounts.administrators(roles_override={username: new_roles}) if not (disabling and u == username)]
        if not left:
            _refuse(409, "That change would leave nobody able to manage users.")
    if body.roles is not None:
        sv.accounts.set_roles(username, body.roles)
    fields = {k: v for k, v in (("display_name", body.display_name), ("disabled", body.disabled)) if v is not None}
    u = sv.accounts.update(username, **fields)
    _audit(request, p, 200, username, action="update_user", roles=list(u.roles), disabled=u.disabled)
    return _view(u)


@router.post("/users/{username}/password", response_model=UserView)
def reset_password(request: Request, body: ResetIn, username: str = Path(pattern=USERNAME), p: Principal = MANAGE_USERS):
    sv = _services(request)
    target = sv.accounts.get(username)
    if target is None:
        raise HTTPException(404, "Not found.")
    if username == p.sub:
        _refuse(409, "Change your own password from your account menu.")
    _may_manage(sv, p, target)
    try:
        u = sv.accounts.set_password(username, body.password, must_change=True)
    except AccountError as e:
        raise HTTPException(422, str(e)) from None
    _audit(request, p, 200, username, action="reset_password")
    return _view(u)
