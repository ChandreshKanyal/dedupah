"""Development tokens: `python -m dedupe.api.devtoken --sub asha --roles steward,approver`. Needs DEDUPE_DEV_AUTH_SECRET. Refused in production."""
import argparse
import os
import time
import uuid

import jwt

from .auth import DEV_AUDIENCE, DEV_ISSUER
from .settings import ApiSettings, SettingsError


def issue(settings: ApiSettings, sub: str, roles: list[str], ttl_s: int = 900, username: str | None = None) -> str:
    if settings.production or settings.auth_mode != "dev":
        raise SettingsError("dev tokens are for development only")
    now = int(time.time())
    claims = {"iss": DEV_ISSUER, "aud": DEV_AUDIENCE, "sub": sub, "iat": now, "exp": now + ttl_s, "jti": uuid.uuid4().hex, "roles": roles}
    if username:
        claims["preferred_username"] = username
    return jwt.encode(claims, settings.dev_secret, algorithm="HS256")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sub", required=True)
    ap.add_argument("--roles", default="viewer")
    ap.add_argument("--ttl", type=int, default=900, help="seconds")
    a = ap.parse_args()
    if os.environ.get("DEDUPE_ENV", "dev").lower() == "production":
        raise SystemExit("dev tokens are refused in production")
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=os.environ.get("DEDUPE_DEV_AUTH_SECRET"))
    s.validate()
    print(issue(s, a.sub, [r for r in a.roles.split(",") if r], a.ttl))


if __name__ == "__main__":
    main()
