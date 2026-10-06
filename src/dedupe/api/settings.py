"""API settings, all from environment variables (credentials and keys never live in files). Production refuses insecure settings."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class SettingsError(ValueError):
    pass


def _list(name: str, default: tuple = ()) -> tuple:
    v = os.environ.get(name, "").strip()
    return tuple(x.strip() for x in v.split(",") if x.strip()) if v else default


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    try:
        return int(v) if v else default
    except ValueError as e:
        raise SettingsError(f"{name} must be a whole number") from e


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    return default if not v else v in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ApiSettings:
    env: str = "dev"                                    # dev | production
    auth_mode: str = "dev"                              # dev (shared-secret test tokens) | oidc (customer identity provider)
    dev_secret: str | None = None                       # HS256 secret for dev tokens (>= 32 chars), never in production
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None                    # where the provider publishes its public signing keys
    oidc_algorithms: tuple = ("RS256", "ES256")         # asymmetric only; "none" and HS* are rejected for oidc
    roles_claim: str = "roles"                          # dotted path allowed, e.g. realm_access.roles
    role_map: dict = field(default_factory=dict)        # identity-provider group name -> our role name
    max_token_lifetime_s: int = 3600                    # tokens valid longer than this are refused (short-lived tokens only)
    leeway_s: int = 30
    max_body_bytes: int = 1_000_000
    max_page_size: int = 200
    rate_limit_per_min: int = 240                       # per authenticated user
    ip_rate_limit_per_min: int = 600                    # per client address, every request
    auth_fail_limit_per_min: int = 20                   # failed authentications per client address
    cors_origins: tuple = ()                            # none by default: the API is not meant to be called from other web origins
    allowed_hosts: tuple = ("*",)                       # production requires an explicit list
    trust_proxy: bool = False                           # read the client address from X-Forwarded-For (set only behind your own proxy)
    require_https: bool = False                         # production: refuse plain http (TLS terminates at a proxy that sets X-Forwarded-Proto)
    enable_docs: bool | None = None                     # /docs and /openapi.json: None = on in dev, off in production; True/False forces it
    database_url: str | None = None
    work: Path = Path("data/staging")

    @property
    def production(self) -> bool:
        return self.env == "production"

    @property
    def docs_enabled(self) -> bool:
        return (not self.production) if self.enable_docs is None else self.enable_docs

    @classmethod
    def from_env(cls) -> "ApiSettings":
        env = os.environ.get("DEDUPE_ENV", "dev").strip().lower() or "dev"
        prod = env == "production"
        try:
            role_map = json.loads(os.environ.get("DEDUPE_OIDC_ROLE_MAP", "") or "{}")
        except json.JSONDecodeError as e:
            raise SettingsError("DEDUPE_OIDC_ROLE_MAP must be a JSON object") from e
        s = cls(
            env=env, auth_mode=os.environ.get("DEDUPE_AUTH_MODE", "oidc" if prod else "dev").strip().lower(),
            dev_secret=os.environ.get("DEDUPE_DEV_AUTH_SECRET") or None,
            oidc_issuer=os.environ.get("DEDUPE_OIDC_ISSUER") or None, oidc_audience=os.environ.get("DEDUPE_OIDC_AUDIENCE") or None,
            oidc_jwks_url=os.environ.get("DEDUPE_OIDC_JWKS_URL") or None,
            oidc_algorithms=_list("DEDUPE_OIDC_ALGORITHMS", ("RS256", "ES256")),
            roles_claim=os.environ.get("DEDUPE_OIDC_ROLES_CLAIM", "roles"), role_map=role_map,
            max_token_lifetime_s=_int("DEDUPE_MAX_TOKEN_LIFETIME_S", 3600),
            max_body_bytes=_int("DEDUPE_MAX_BODY_BYTES", 1_000_000), max_page_size=_int("DEDUPE_MAX_PAGE_SIZE", 200),
            rate_limit_per_min=_int("DEDUPE_RATE_LIMIT_PER_MIN", 240), ip_rate_limit_per_min=_int("DEDUPE_IP_RATE_LIMIT_PER_MIN", 600),
            auth_fail_limit_per_min=_int("DEDUPE_AUTH_FAIL_LIMIT_PER_MIN", 20),
            cors_origins=_list("DEDUPE_CORS_ORIGINS"), allowed_hosts=_list("DEDUPE_ALLOWED_HOSTS", ("*",)),
            trust_proxy=_bool("DEDUPE_TRUST_PROXY", False), require_https=_bool("DEDUPE_REQUIRE_HTTPS", prod),
            enable_docs=_bool("DEDUPE_API_DOCS", not prod), database_url=os.environ.get("DEDUPE_DATABASE_URL") or None,
            work=Path(os.environ.get("DEDUPE_WORK_DIR", "data/staging")),
        )
        s.validate()
        return s

    def validate(self) -> None:
        """Raise SettingsError listing everything that is unsafe or incomplete."""
        problems = []
        if self.env not in ("dev", "production"):
            problems.append("DEDUPE_ENV must be 'dev' or 'production'")
        if self.auth_mode not in ("dev", "oidc"):
            problems.append("DEDUPE_AUTH_MODE must be 'dev' or 'oidc'")
        if self.auth_mode == "dev":
            if self.production:
                problems.append("dev tokens are not allowed in production: set DEDUPE_AUTH_MODE=oidc")
            if not self.dev_secret or len(self.dev_secret) < 32:
                problems.append("dev auth needs DEDUPE_DEV_AUTH_SECRET of at least 32 characters")
        else:
            for name, v in (("DEDUPE_OIDC_ISSUER", self.oidc_issuer), ("DEDUPE_OIDC_AUDIENCE", self.oidc_audience), ("DEDUPE_OIDC_JWKS_URL", self.oidc_jwks_url)):
                if not v:
                    problems.append(f"{name} is required for oidc")
            bad = [a for a in self.oidc_algorithms if a.upper().startswith("HS") or a.lower() == "none"]
            if bad or not self.oidc_algorithms:
                problems.append(f"oidc algorithms must be asymmetric (RS*, ES*, PS*); got {list(self.oidc_algorithms)}")
            if self.production and self.oidc_jwks_url and not self.oidc_jwks_url.startswith("https://"):
                problems.append("the JWKS URL must be https in production")
        if self.production:
            if "*" in self.allowed_hosts:
                problems.append("set DEDUPE_ALLOWED_HOSTS to the real host names in production")
            if not self.require_https:
                problems.append("production requires https (DEDUPE_REQUIRE_HTTPS)")
            if not self.database_url or self.database_url.startswith("sqlite"):
                problems.append("production needs DEDUPE_DATABASE_URL pointing at a server database (not sqlite)")
            if "*" in self.cors_origins:
                problems.append("CORS origin '*' is not allowed")
        if self.max_token_lifetime_s < 60 or self.max_page_size < 1 or self.max_body_bytes < 1024:
            problems.append("token lifetime, page size and body size limits are out of range")
        if problems:
            raise SettingsError("; ".join(problems))
