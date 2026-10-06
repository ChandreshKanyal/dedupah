"""Token verification. The customer's identity provider (OIDC) signs short-lived tokens; we verify them and read identity and roles.

Verified on every request: signature (with an allow-list of algorithms: never 'none', never a shared-secret algorithm for the identity
provider, which blocks the classic key-confusion attack), issuer, audience, expiry, not-before, required claims, and a maximum token
lifetime. Why a token was refused is written to the security log; the caller only ever sees "invalid or expired token".

`dev` mode (shared-secret HS256 tokens from `dedupe.api.devtoken`) exists for tests and demos and is refused in production.
"""
from dataclasses import dataclass

import jwt

from .roles import ROLES, permissions_for
from .settings import ApiSettings

DEV_ISSUER, DEV_AUDIENCE = "dedupe-dev", "dedupe-api"
MAX_TOKEN_CHARS = 8192


class AuthError(Exception):
    """Authentication failed. `reason` is for the security log only, never for the client."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Principal:
    sub: str                      # the identity provider's stable id for the person: this is the `actor` in every audit log
    username: str                # display only
    roles: frozenset
    jti: str | None = None

    @property
    def actor(self) -> str:
        return self.sub

    @property
    def permissions(self) -> frozenset:
        return permissions_for(self.roles)


def _claim(payload: dict, path: str):
    cur = payload
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


_REASONS = {
    jwt.ExpiredSignatureError: "expired", jwt.ImmatureSignatureError: "not_yet_valid", jwt.InvalidAudienceError: "bad_audience",
    jwt.InvalidIssuerError: "bad_issuer", jwt.InvalidSignatureError: "bad_signature", jwt.InvalidAlgorithmError: "bad_algorithm",
    jwt.MissingRequiredClaimError: "missing_claim", jwt.DecodeError: "malformed",
}


class TokenVerifier:
    def __init__(self, settings: ApiSettings, key_resolver=None):
        """`key_resolver(token) -> key` is injectable for tests; by default signing keys are fetched (and cached) from the provider's JWKS."""
        self.s = settings
        if settings.auth_mode == "oidc":
            self._algs = [a for a in settings.oidc_algorithms]
            if key_resolver is None:
                client = jwt.PyJWKClient(settings.oidc_jwks_url, cache_keys=True, lifespan=300, timeout=5)
                key_resolver = lambda tok: client.get_signing_key_from_jwt(tok).key  # noqa: E731
            self._key = key_resolver
            self._iss, self._aud = settings.oidc_issuer, settings.oidc_audience
        else:
            self._algs, self._key, self._iss, self._aud = ["HS256"], (lambda _tok: settings.dev_secret), DEV_ISSUER, DEV_AUDIENCE

    def verify(self, token: str) -> Principal:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise AuthError("malformed")
        try:
            key = self._key(token)
            payload = jwt.decode(token, key, algorithms=self._algs, audience=self._aud, issuer=self._iss, leeway=self.s.leeway_s,
                                 options={"require": ["exp", "iat", "sub", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise AuthError(next((r for t, r in _REASONS.items() if isinstance(e, t)), "invalid")) from None
        except Exception:                      # key fetch failure, unknown key id, ...
            raise AuthError("key_unavailable") from None
        if payload["exp"] - payload["iat"] > self.s.max_token_lifetime_s:
            raise AuthError("lifetime_too_long")
        sub = payload["sub"]
        if not isinstance(sub, str) or not sub.strip() or len(sub) > 200:
            raise AuthError("bad_subject")
        return Principal(sub=sub, username=str(payload.get("preferred_username") or payload.get("name") or sub)[:100],
                         roles=self._roles(payload), jti=payload.get("jti"))

    def _roles(self, payload: dict) -> frozenset:
        raw = _claim(payload, self.s.roles_claim)
        items = raw.replace(",", " ").split() if isinstance(raw, str) else raw if isinstance(raw, list) else []
        mapped = {self.s.role_map.get(str(x), str(x)) for x in items}
        return frozenset(r for r in mapped if r in ROLES)       # unknown roles carry no rights
