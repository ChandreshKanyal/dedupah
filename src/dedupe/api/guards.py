"""Route guards. EVERY route must declare its access through one of these (a test fails if one does not):

    public()            no login (health check only)
    authenticated()     any logged-in person
    require("perm")     a logged-in person whose roles include the permission

Authentication and authorization happen here, before the route's own code runs. A guard returns the verified `Principal`;
routes use it as the `actor` of everything they write, so identities in the audit logs are verified, not typed.
"""
from fastapi import HTTPException, Request

from .auth import AuthError, Principal
from .roles import PERMISSIONS


def _services(request: Request):
    return request.app.state.services


def _context(request: Request) -> dict:
    return {"method": request.method, "route": getattr(request.scope.get("route"), "path", "") or "",
            "request_id": request.scope["state"].get("request_id", ""), "client_ip": request.scope["state"].get("client_ip", "")}


def authenticate(request: Request) -> Principal:
    sv = _services(request)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    ctx = _context(request)
    ip_key = f"authfail:{ctx['client_ip']}"
    if sv.limiter.count(ip_key) >= sv.settings.auth_fail_limit_per_min:      # too many recent failures from this address
        sv.seclog.record("rate_limited", status=429, detail={"scope": "authentication"}, **ctx)
        raise HTTPException(429, "Too many failed attempts.", headers={"Retry-After": "60"})
    try:
        if scheme.lower() != "bearer" or not token:
            raise AuthError("missing")
        principal = sv.verifier.verify(token.strip())
    except AuthError as e:
        sv.limiter.allow(ip_key, 10**9)                                       # count this failure
        sv.seclog.record("auth_failed", status=401, detail={"reason": e.reason}, **ctx)
        raise HTTPException(401, "Invalid or expired token.", headers={"WWW-Authenticate": "Bearer"}) from None
    if not sv.limiter.allow(f"user:{principal.sub}", sv.settings.rate_limit_per_min):
        sv.seclog.record("rate_limited", actor=principal.actor, roles=principal.roles, status=429, detail={"scope": "user"}, **ctx)
        raise HTTPException(429, "Too many requests.", headers={"Retry-After": "60"})
    request.state.principal = principal
    return principal


def public():
    def guard() -> None:
        return None
    guard.access = ("public", None)
    return guard


def authenticated():
    def guard(request: Request) -> Principal:
        return authenticate(request)
    guard.access = ("authenticated", None)
    return guard


def require(permission: str):
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")

    def guard(request: Request) -> Principal:
        principal = authenticate(request)
        if permission not in principal.permissions:
            _services(request).seclog.record("permission_denied", actor=principal.actor, roles=principal.roles, status=403,
                                             detail={"needs": permission}, **_context(request))
            raise HTTPException(403, "You do not have permission to do this.")
        return principal
    guard.access = ("permission", permission)
    return guard
