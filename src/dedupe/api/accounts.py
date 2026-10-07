"""Local accounts: users with passwords, and roles the administrator defines.

This is the OPTIONAL "username and password" sign-in (`DEDUPE_AUTH_MODE=local`). Customers with a corporate identity provider should keep
using OIDC (it brings MFA, password rules and leaver handling); this exists for small deployments and for testing.

Rules enforced here
  - Passwords are stored only as a salted scrypt hash (the cost parameters are stored with each hash, so they can be raised later).
  - Password policy: 12-128 characters, not containing the username, not a very common password, not a single repeated character.
  - Five wrong passwords in a row lock the account for 15 minutes; unknown users and wrong passwords look identical to the caller, in message and time.
  - A password change or reset, disabling, or "sign out everywhere" ends every earlier token of that user (their `epoch` counter moves on).
  - Built-in roles cannot be changed; custom roles hold any subset of the permissions in `roles.PERMISSIONS`.
  - Nobody can grant more than they hold themselves (checked by the routes using `can_grant`).

The command line for the FIRST administrator is in `accounts_cli.py` (python -m dedupe.api.accounts_cli create-admin --username admin).
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, delete, insert, select, update
from sqlalchemy.engine import Engine

from .roles import BUILTIN_DESCRIPTIONS, PERMISSIONS, ROLES

USERNAME = r"^[a-z0-9][a-z0-9._-]{2,63}$"
ROLE_NAME = r"^[a-z][a-z0-9_-]{1,39}$"
_USER, _ROLE = re.compile(USERNAME), re.compile(ROLE_NAME)
MIN_LEN, MAX_LEN = 12, 128
MAX_FAILURES, LOCK_SECONDS = 5, 15 * 60
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1                 # OWASP's smallest recommended scrypt setting; tests lower SCRYPT_N
COMMON = {"password1234", "password12345", "123456789012", "1234567890123", "qwertyuiop12", "qwertyuiop123", "administrator", "welcome12345",
          "changeme1234", "letmein12345", "iloveyou1234", "abcdefghijkl", "passw0rd1234", "password@123", "admin@123456", "dedupe123456"}

_meta = MetaData()
users = Table(
    "users", _meta,
    Column("username", String(64), primary_key=True),
    Column("display_name", String(100), nullable=False, default=""),
    Column("password_hash", String(300), nullable=False),
    Column("disabled", Integer, nullable=False, default=0),
    Column("must_change", Integer, nullable=False, default=1),
    Column("failed_count", Integer, nullable=False, default=0),
    Column("locked_until", Float, nullable=False, default=0.0),
    Column("epoch", Integer, nullable=False, default=1),                 # in every token; bumped to end all earlier sessions
    Column("created_by", String(200), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("last_login_at", String(40)),
)
user_roles = Table(
    "user_roles", _meta,
    Column("username", String(64), primary_key=True),
    Column("role", String(40), primary_key=True),
)
custom_roles = Table(
    "custom_roles", _meta,
    Column("name", String(40), primary_key=True),
    Column("description", String(300), nullable=False, default=""),
    Column("permissions", Text, nullable=False, default="[]"),
    Column("created_by", String(200), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_by", String(200), nullable=False),
    Column("updated_at", String(40), nullable=False),
)


class AccountError(ValueError):
    """A request the accounts rules refuse; the message is safe to show to the administrator."""


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    roles: tuple
    disabled: bool
    must_change: bool
    locked_until: float
    epoch: int
    created_by: str
    created_at: str
    last_login_at: str | None = None


@dataclass(frozen=True)
class RoleInfo:
    name: str
    description: str
    permissions: frozenset
    builtin: bool
    users: int = 0
    updated_by: str = ""
    updated_at: str = ""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


# ---- passwords ------------------------------------------------------------------------------------------------------------
def hash_password(password: str, n: int | None = None) -> str:
    n = n or SCRYPT_N
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=256 * 1024 * 1024)
    return "scrypt${}${}${}${}${}".format(n, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        want = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(want), maxmem=256 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def check_password_policy(password: str, username: str = "") -> None:
    if not isinstance(password, str) or not (MIN_LEN <= len(password) <= MAX_LEN):
        raise AccountError(f"A password needs {MIN_LEN} to {MAX_LEN} characters.")
    low = password.lower()
    if username and username.lower() in low:
        raise AccountError("The password must not contain the username.")
    if low in COMMON or len(set(password)) < 5:
        raise AccountError("That password is too easy to guess. Use a longer phrase with more variety.")


# ---- the store -------------------------------------------------------------------------------------------------------------
class AccountStore:
    def __init__(self, engine: Engine):
        self.engine = engine
        _meta.create_all(engine)
        self._dummy = hash_password(secrets.token_urlsafe(16))        # verified against when the user is unknown, so timing does not reveal who exists

    # -- users --
    def _user(self, r, roles: tuple) -> User:
        return User(r.username, r.display_name, roles, bool(r.disabled), bool(r.must_change), r.locked_until, r.epoch, r.created_by, r.created_at,
                    r.last_login_at)

    def get(self, username: str) -> User | None:
        if not _USER.match(username or ""):
            return None
        with self.engine.connect() as c:
            r = c.execute(select(users).where(users.c.username == username)).first()
            if r is None:
                return None
            roles = tuple(sorted(x.role for x in c.execute(select(user_roles.c.role).where(user_roles.c.username == username))))
        return self._user(r, roles)

    def list_users(self) -> "list[User]":
        with self.engine.connect() as c:
            rows = list(c.execute(select(users).order_by(users.c.username)))
            by_user: dict[str, list[str]] = {}
            for x in c.execute(select(user_roles)):
                by_user.setdefault(x.username, []).append(x.role)
        return [self._user(r, tuple(sorted(by_user.get(r.username, [])))) for r in rows]

    def count(self) -> int:
        return len(self.list_users())

    def create_user(self, username: str, display_name: str, password: str, roles: list[str], actor: str, must_change: bool = True) -> User:
        if not _USER.match(username or ""):
            raise AccountError("A username is 3-64 characters: lower-case letters, digits, dot, dash or underscore, starting with a letter or digit.")
        display_name = (display_name or "").strip()[:100]
        check_password_policy(password, username)
        roles = sorted(set(roles))
        self._check_roles_exist(roles)
        with self.engine.begin() as c:
            if c.execute(select(users.c.username).where(users.c.username == username)).first():
                raise AccountError("A user with this username already exists.")
            c.execute(insert(users).values(username=username, display_name=display_name, password_hash=hash_password(password), disabled=0,
                                           must_change=int(must_change), failed_count=0, locked_until=0.0, epoch=1,
                                           created_by=actor, created_at=_now()))
            for r in roles:
                c.execute(insert(user_roles).values(username=username, role=r))
        return self.get(username)

    def set_roles(self, username: str, roles: list[str]) -> User:
        roles = sorted(set(roles))
        self._check_roles_exist(roles)
        with self.engine.begin() as c:
            c.execute(delete(user_roles).where(user_roles.c.username == username))
            for r in roles:
                c.execute(insert(user_roles).values(username=username, role=r))
        return self.get(username)

    def update(self, username: str, **fields) -> User:
        allowed = {k: v for k, v in fields.items() if k in ("display_name", "disabled")}
        if "display_name" in allowed:
            allowed["display_name"] = (allowed["display_name"] or "").strip()[:100]
        if "disabled" in allowed:
            allowed["disabled"] = int(bool(allowed["disabled"]))
            allowed["epoch"] = users.c.epoch + 1                    # a disabled user's open sessions end at once
        if allowed:
            with self.engine.begin() as c:
                c.execute(update(users).where(users.c.username == username).values(**allowed))
        return self.get(username)

    def set_password(self, username: str, password: str, must_change: bool) -> User:
        check_password_policy(password, username)
        with self.engine.begin() as c:
            c.execute(update(users).where(users.c.username == username).values(
                password_hash=hash_password(password), must_change=int(must_change), failed_count=0, locked_until=0.0, epoch=users.c.epoch + 1))
        return self.get(username)

    def sign_out_everywhere(self, username: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(users).where(users.c.username == username).values(epoch=users.c.epoch + 1))

    def authenticate(self, username: str, password: str, now: float | None = None) -> tuple[User | None, str]:
        """(user, "ok") or (None, reason) with reason in unknown | disabled | locked | bad_password (reasons go to the security log only)."""
        now = time.time() if now is None else now
        with self.engine.connect() as c:
            r = c.execute(select(users).where(users.c.username == username)).first() if _USER.match(username or "") else None
        stored = r.password_hash if r is not None else self._dummy
        matches = verify_password(password if isinstance(password, str) else "", stored)           # always do the work, whatever the account state
        if r is None:
            return None, "unknown"
        if r.disabled:
            return None, "disabled"
        if r.locked_until > now:
            return None, "locked"
        with self.engine.begin() as c:
            if not matches:
                failed = r.failed_count + 1
                c.execute(update(users).where(users.c.username == username).values(
                    failed_count=0 if failed >= MAX_FAILURES else failed, locked_until=now + LOCK_SECONDS if failed >= MAX_FAILURES else 0.0))
                return None, "bad_password"
            c.execute(update(users).where(users.c.username == username).values(failed_count=0, locked_until=0.0, last_login_at=_now()))
        return self.get(username), "ok"

    # -- roles --
    def _custom(self) -> dict:
        with self.engine.connect() as c:
            return {r.name: r for r in c.execute(select(custom_roles))}

    def role_names(self) -> set:
        return set(ROLES) | set(self._custom())

    def _check_roles_exist(self, roles: list[str]) -> None:
        unknown = [r for r in roles if r not in self.role_names()]
        if unknown:
            raise AccountError("Unknown role: " + ", ".join(sorted(unknown))[:100])

    def permissions_of(self, roles) -> frozenset:
        custom = self._custom()
        out: set = set()
        for r in roles:
            if r in ROLES:
                out |= ROLES[r]
            elif r in custom:
                out |= set(json.loads(custom[r].permissions))
        return frozenset(p for p in out if p in PERMISSIONS)

    def roles(self) -> "list[RoleInfo]":
        custom, counts = self._custom(), {}
        with self.engine.connect() as c:
            for x in c.execute(select(user_roles.c.role)):
                counts[x.role] = counts.get(x.role, 0) + 1
        out = [RoleInfo(n, BUILTIN_DESCRIPTIONS.get(n, ""), perms, True, counts.get(n, 0)) for n, perms in ROLES.items()]
        out += [RoleInfo(n, r.description, frozenset(json.loads(r.permissions)), False, counts.get(n, 0), r.updated_by, r.updated_at)
                for n, r in sorted(custom.items())]
        return out

    def _role_args(self, name: str, description: str, permissions) -> tuple[str, str, list]:
        description = (description or "").strip()[:300]
        perms = sorted(set(permissions or []))
        bad = [p for p in perms if p not in PERMISSIONS]
        if bad:
            raise AccountError("Unknown permission: " + ", ".join(bad)[:100])
        if not perms:
            raise AccountError("A role needs at least one permission.")
        return name, description, perms

    def create_role(self, name: str, description: str, permissions, actor: str) -> RoleInfo:
        if not _ROLE.match(name or ""):
            raise AccountError("A role name is 2-40 characters: lower-case letters, digits, dash or underscore, starting with a letter.")
        if name in ROLES:
            raise AccountError("That name is used by a built-in role.")
        _, description, perms = self._role_args(name, description, permissions)
        with self.engine.begin() as c:
            if c.execute(select(custom_roles.c.name).where(custom_roles.c.name == name)).first():
                raise AccountError("A role with this name already exists.")
            now = _now()
            c.execute(insert(custom_roles).values(name=name, description=description, permissions=json.dumps(perms), created_by=actor, created_at=now,
                                                  updated_by=actor, updated_at=now))
        return next(r for r in self.roles() if r.name == name)

    def update_role(self, name: str, description: str, permissions, actor: str) -> RoleInfo:
        if name in ROLES:
            raise AccountError("Built-in roles cannot be changed. Create a custom role instead.")
        _, description, perms = self._role_args(name, description, permissions)
        with self.engine.begin() as c:
            if not c.execute(select(custom_roles.c.name).where(custom_roles.c.name == name)).first():
                raise AccountError("No such role.")
            c.execute(update(custom_roles).where(custom_roles.c.name == name).values(
                description=description, permissions=json.dumps(perms), updated_by=actor, updated_at=_now()))
        return next(r for r in self.roles() if r.name == name)

    def delete_role(self, name: str, in_use_elsewhere: int = 0) -> None:
        if name in ROLES:
            raise AccountError("Built-in roles cannot be deleted.")
        with self.engine.begin() as c:
            if not c.execute(select(custom_roles.c.name).where(custom_roles.c.name == name)).first():
                raise AccountError("No such role.")
            if c.execute(select(user_roles.c.username).where(user_roles.c.role == name)).first() or in_use_elsewhere:
                raise AccountError("Some users or projects still use this role. Move them to another role first.")
            c.execute(delete(custom_roles).where(custom_roles.c.name == name))

    # -- safety rules --
    def administrators(self, roles_override: dict | None = None, perms_override: dict | None = None) -> "list[str]":
        """Enabled users whose roles hold users.manage. `roles_override` {username: roles} and `perms_override` {role: permissions} let a caller ask
        'who would remain if this change were made'."""
        custom = {n: set(json.loads(r.permissions)) for n, r in self._custom().items()}
        custom.update({k: set(v) for k, v in (perms_override or {}).items()})
        out = []
        for u in self.list_users():
            if u.disabled:
                continue
            roles = (roles_override or {}).get(u.username, u.roles)
            held: set = set()
            for r in roles:
                held |= set(ROLES[r]) if r in ROLES else custom.get(r, set())
            if "users.manage" in held:
                out.append(u.username)
        return out


def can_grant(holder_permissions, wanted) -> bool:
    """Nobody may give away more than they hold."""
    return set(wanted) <= set(holder_permissions)
