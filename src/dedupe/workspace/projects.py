"""Projects and where their data lives."""
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, event, select, update

from dedupe.governance.store import ConfigStore
from dedupe.schema import create_and_upgrade
from dedupe.store import JudgmentStore

PROJECT_ID = r"^[a-z0-9][a-z0-9-]{1,39}$"
_ID = re.compile(PROJECT_ID)

_meta = MetaData()
projects = Table(
    "projects", _meta,
    Column("id", String(40), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("description", String(500), nullable=False, default=""),
    Column("created_by", String(200), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("second_approval", Integer, nullable=False, server_default="1"),      # 1: a second person approves connection and rule changes; 0: one person may do all
)
project_grants = Table(
    "project_grants", _meta,
    Column("project_id", String(40), primary_key=True),
    Column("subject", String(200), primary_key=True),
    Column("role", String(40), primary_key=True),
    Column("granted_by", String(200), nullable=False),
    Column("granted_at", String(40), nullable=False),
)


class WorkspaceError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    description: str
    created_by: str
    created_at: str
    second_approval: bool = True


@dataclass(frozen=True)
class Grant:
    project_id: str
    subject: str
    role: str
    granted_by: str
    granted_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def engine_for(url: str):
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA journal_mode=WAL"))
    return engine


class Workspace:
    """The workspace root on disk, the main database (projects, runs, uploads, connections, security log) and one database per project."""

    def __init__(self, root: Path, url: str, project_url_template: str | None = None):
        self.root, self.url, self.template = Path(root), url, project_url_template
        if self.template and "{project}" not in self.template:
            raise WorkspaceError("the project database URL template must contain {project}")
        self.engine = engine_for(url)
        create_and_upgrade(self.engine, _meta)
        self._settings: dict[str, bool] = {}
        self._stores: dict[str, tuple[JudgmentStore, ConfigStore]] = {}
        self._lock = threading.Lock()

    # ---- places -------------------------------------------------------------------------------------------------------
    def project_dir(self, pid: str) -> Path:
        return self.root / "projects" / pid

    def identity_dir(self, pid: str) -> Path:
        return self.project_dir(pid) / "identity"

    def run_dir(self, pid: str, rid: str) -> Path:
        return self.project_dir(pid) / "runs" / rid

    @property
    def upload_dir(self) -> Path:
        return self.root / "uploads"

    def project_url(self, pid: str) -> str:
        """Development: a SQLite file inside the project folder. Production: the operator's template, e.g. postgresql://.../dedupe_{project}
        (one database per project, created by the operator; read from the environment, never stored)."""
        if self.template:
            return self.template.format(project=pid.replace("-", "_"))
        return f"sqlite:///{self.project_dir(pid) / 'project.db'}"

    # ---- projects -----------------------------------------------------------------------------------------------------
    def create(self, pid: str, name: str, description: str, actor: str, second_approval: bool = True) -> Project:
        if not _ID.match(pid or ""):
            raise WorkspaceError("a project id is 2-40 lower-case letters, digits or - (it appears in addresses and folder names)")
        name = (name or "").strip()
        if not name or len(name) > 120:
            raise WorkspaceError("a project needs a name of up to 120 characters")
        with self.engine.begin() as c:
            if c.execute(select(projects.c.id).where(projects.c.id == pid)).first():
                raise WorkspaceError("a project with this id already exists")
            c.execute(projects.insert().values(id=pid, name=name, description=(description or "")[:500], created_by=actor, created_at=_now(),
                                               second_approval=int(bool(second_approval))))
        self.project_dir(pid).mkdir(parents=True, exist_ok=True)
        self.stores(pid)                                           # create the project's tables now, not on first use
        return self.get(pid)

    def get(self, pid: str) -> Project | None:
        if not _ID.match(pid or ""):
            return None
        with self.engine.connect() as c:
            r = c.execute(select(projects).where(projects.c.id == pid)).first()
        return Project(r.id, r.name, r.description, r.created_by, r.created_at, bool(r.second_approval)) if r else None

    def list(self) -> list[Project]:
        with self.engine.connect() as c:
            return [Project(r.id, r.name, r.description, r.created_by, r.created_at, bool(r.second_approval))
                    for r in c.execute(select(projects).order_by(projects.c.name))]

    def stores(self, pid: str) -> tuple[JudgmentStore, ConfigStore]:
        """The project's own decision log and rules store (append-only, hash-chained, four eyes), opened once and kept."""
        with self._lock:
            if pid not in self._stores:
                url = self.project_url(pid)
                self._stores[pid] = (JudgmentStore.open(url), ConfigStore.open(url))
            judgments, configs = self._stores[pid]
        configs.solo = not self.second_approval(pid)          # one-person projects approve their own changes; the setting applies at once
        return judgments, configs

    def second_approval(self, pid: str) -> bool:
        """Does this project require a second person to approve connection and rule changes (and sensitive decisions)?"""
        if pid not in self._settings:
            with self.engine.connect() as c:
                r = c.execute(select(projects.c.second_approval).where(projects.c.id == pid)).first()
            self._settings[pid] = bool(r.second_approval) if r else True
        return self._settings[pid]

    def set_second_approval(self, pid: str, value: bool) -> None:
        with self.engine.begin() as c:
            c.execute(update(projects).where(projects.c.id == pid).values(second_approval=int(bool(value))))
        self._settings[pid] = bool(value)

    def is_owner(self, pid: str, subject: str) -> bool:
        return "owner" in self.roles_for(pid, subject)

    # ---- per-project access (business-unit scoping) --------------------------------------------------------------------
    # A project with NO grants at all stays open to anyone holding the matching global role (today's behaviour, so
    # existing deployments and the demo workspace are unaffected). The moment a project has at least one grant row,
    # it becomes "scoped": only its members (plus platform_admin and auditor, who always see everything) may act on
    # it. This is enforced in dedupe.api.scope, which calls `members()`/`roles_for()` below.
    def grant(self, pid: str, subject: str, role: str, actor: str) -> Grant:
        subject, role = (subject or "").strip(), (role or "").strip()
        if not subject or len(subject) > 200:
            raise WorkspaceError("a grant needs a subject (username) of up to 200 characters")
        if not role or len(role) > 40:
            raise WorkspaceError("a grant needs a role name")
        now = _now()
        with self.engine.begin() as c:
            c.execute(project_grants.delete().where(
                (project_grants.c.project_id == pid) & (project_grants.c.subject == subject) & (project_grants.c.role == role)))
            c.execute(project_grants.insert().values(project_id=pid, subject=subject, role=role, granted_by=actor, granted_at=now))
        return Grant(pid, subject, role, actor, now)

    def revoke(self, pid: str, subject: str, role: str) -> bool:
        with self.engine.begin() as c:
            r = c.execute(project_grants.delete().where(
                (project_grants.c.project_id == pid) & (project_grants.c.subject == subject) & (project_grants.c.role == role)))
            return r.rowcount > 0

    def members(self, pid: str) -> "list[Grant]":                  # quoted: this class has its own `list` method
        with self.engine.connect() as c:
            rows = c.execute(select(project_grants).where(project_grants.c.project_id == pid)
                              .order_by(project_grants.c.subject, project_grants.c.role))
            return [Grant(r.project_id, r.subject, r.role, r.granted_by, r.granted_at) for r in rows]

    def roles_for(self, pid: str, subject: str) -> frozenset[str]:
        with self.engine.connect() as c:
            rows = c.execute(select(project_grants.c.role).where(
                (project_grants.c.project_id == pid) & (project_grants.c.subject == subject)))
            return frozenset(r.role for r in rows)
