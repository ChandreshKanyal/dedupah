"""Scheduled runs: "run these connections every day at 02:00".

A schedule belongs to one project and names the connections to read. When it is due, the scheduler starts an ordinary run (so it queues, is logged
and can be reviewed like any other) over the connections' live versions AT THAT MOMENT, as the actor `schedule:<id>`.

Safety rules
  - a due schedule is CLAIMED with one atomic database update (its next time moves on), so a second API process or a slow tick can never start it twice,
    and the run carries an idempotency key built from the schedule and the due time as a second line of defence;
  - after downtime a missed schedule runs ONCE when the server is back, never once per missed slot;
  - a schedule does not start a new run while its previous run is still waiting or running (no pile-up);
  - three runs in a row that fail, a connection that is no longer live, or a creator who can no longer sign in pause the schedule with the reason shown;
  - times are kept in UTC and computed in the schedule's own time zone (daylight-saving aware).
"""
import json
import re
import threading
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Boolean, Column, Index, Integer, MetaData, String, Table, Text, select, update

from dedupe.schema import create_and_upgrade

from .runs import ACTIVE, RunError, RunJobs, runs

SCHEDULE_ID = r"^S[0-9a-f]{12}$"
FREQUENCIES = ("hourly", "daily", "weekly")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_CONN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
PAUSE_AFTER_FAILURES = 3

_meta = MetaData()
schedules = Table(
    "project_schedules", _meta,
    Column("id", String(20), primary_key=True),
    Column("project_id", String(40), nullable=False),
    Column("name", String(100), nullable=False),
    Column("connections", Text, nullable=False),
    Column("frequency", String(10), nullable=False),
    Column("at_time", String(5), nullable=False),
    Column("weekday", Integer),
    Column("timezone", String(40), nullable=False, server_default="UTC"),
    Column("enabled", Boolean, nullable=False),
    Column("paused_reason", String(200)),
    Column("created_by", String(200), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("next_run_at", String(40)),
    Column("last_run_at", String(40)),
    Column("last_run_id", String(20)),
    Column("last_status", String(20)),
    Column("consecutive_failures", Integer, nullable=False, server_default="0"),
    Index("ix_project_schedules_due", "enabled", "next_run_at"),
)


class ScheduleError(ValueError):
    """A schedule that cannot be saved; the message is a fixed sentence for the person."""


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ScheduleError("unknown time zone: use a name such as Asia/Kolkata or UTC") from None


def next_after(after: datetime, frequency: str, at_time: str, weekday: int | None, tz: str) -> datetime:
    """The first moment strictly after `after` (UTC) that matches the schedule, in UTC. `hourly` uses only the minutes of `at_time`."""
    z = zone(tz)
    local = after.astimezone(z)
    hh, mm = (int(x) for x in at_time.split(":"))
    if frequency == "hourly":
        cand = local.replace(minute=mm, second=0, microsecond=0).astimezone(timezone.utc)
        if cand <= after:
            cand += timedelta(hours=1)
        return cand
    days = 0
    if frequency == "weekly":
        days = (weekday - local.weekday()) % 7
    cand = datetime.combine(local.date() + timedelta(days=days), time(hh, mm), tzinfo=z)
    if cand.astimezone(timezone.utc) <= after:
        cand = datetime.combine(local.date() + timedelta(days=days + (7 if frequency == "weekly" else 1)), time(hh, mm), tzinfo=z)
    return cand.astimezone(timezone.utc)


def validate(name: str, connections: list[str], frequency: str, at_time: str, weekday: int | None, tz: str) -> None:
    if not (name or "").strip() or len(name) > 100:
        raise ScheduleError("a schedule needs a name of up to 100 characters")
    if not connections or len(connections) > 20 or len(set(connections)) != len(connections) or not all(_CONN.match(c) for c in connections):
        raise ScheduleError("choose between 1 and 20 different connections")
    if frequency not in FREQUENCIES:
        raise ScheduleError("frequency must be hourly, daily or weekly")
    if not _TIME.match(at_time or ""):
        raise ScheduleError("the time must look like 02:30")
    if frequency == "weekly" and (weekday is None or not 0 <= weekday <= 6):
        raise ScheduleError("a weekly schedule needs a day of the week (0 = Monday ... 6 = Sunday)")
    zone(tz)


class Schedules:
    def __init__(self, jobs: RunJobs, is_active=lambda sub: True, clock=lambda: datetime.now(timezone.utc)):
        """`is_active(sub)`: may this person still start runs? (False pauses their schedules.) `clock` is injectable for tests."""
        self.jobs, self.engine, self._active, self._clock = jobs, jobs.engine, is_active, clock
        create_and_upgrade(self.engine, _meta)

    # ---- management ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def _view(r) -> dict:
        return {"id": r.id, "project_id": r.project_id, "name": r.name, "connections": json.loads(r.connections), "frequency": r.frequency, "at_time": r.at_time,
                "weekday": r.weekday, "timezone": r.timezone, "enabled": bool(r.enabled), "paused_reason": r.paused_reason, "created_by": r.created_by,
                "created_at": r.created_at, "next_run_at": r.next_run_at, "last_run_at": r.last_run_at, "last_run_id": r.last_run_id, "last_status": r.last_status,
                "consecutive_failures": r.consecutive_failures}

    def get(self, project_id: str, sid: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(schedules).where(schedules.c.id == sid, schedules.c.project_id == project_id)).first()
        return self._view(r) if r else None

    def for_project(self, project_id: str) -> "list[dict]":
        with self.engine.connect() as c:
            return [self._view(r) for r in c.execute(select(schedules).where(schedules.c.project_id == project_id).order_by(schedules.c.created_at))]

    def create(self, project_id: str, name: str, connections: list[str], frequency: str, at_time: str, weekday: int | None, tz: str, actor: str) -> dict:
        validate(name, connections, frequency, at_time, weekday, tz)
        now = self._clock()
        sid = "S" + uuid.uuid4().hex[:12]
        with self.engine.begin() as c:
            c.execute(schedules.insert().values(
                id=sid, project_id=project_id, name=name.strip(), connections=json.dumps(connections), frequency=frequency, at_time=at_time,
                weekday=weekday if frequency == "weekly" else None, timezone=tz, enabled=True, created_by=actor, created_at=_utc(now),
                next_run_at=_utc(next_after(now, frequency, at_time, weekday, tz)), consecutive_failures=0))
        return self.get(project_id, sid)

    def update(self, project_id: str, sid: str, **changes) -> dict:
        cur = self.get(project_id, sid)
        if cur is None:
            raise ScheduleError("no such schedule")
        new = {**cur, **{k: v for k, v in changes.items() if v is not None}}
        validate(new["name"], new["connections"], new["frequency"], new["at_time"], new["weekday"], new["timezone"])
        values = {"name": new["name"].strip(), "connections": json.dumps(new["connections"]), "frequency": new["frequency"], "at_time": new["at_time"],
                  "weekday": new["weekday"] if new["frequency"] == "weekly" else None, "timezone": new["timezone"], "enabled": bool(new["enabled"])}
        timing_changed = any(cur[k] != values[k] for k in ("frequency", "at_time", "weekday", "timezone"))
        if values["enabled"] and (not cur["enabled"] or timing_changed):
            values["next_run_at"] = _utc(next_after(self._clock(), values["frequency"], values["at_time"], values["weekday"], values["timezone"]))
            values["paused_reason"] = None
            values["consecutive_failures"] = 0 if not cur["enabled"] else cur["consecutive_failures"]
        if not values["enabled"]:
            values["next_run_at"] = None
            values["paused_reason"] = cur["paused_reason"] if not cur["enabled"] else "Paused by a person."
        with self.engine.begin() as c:
            c.execute(update(schedules).where(schedules.c.id == sid).values(**values))
        return self.get(project_id, sid)

    def delete(self, project_id: str, sid: str) -> bool:
        with self.engine.begin() as c:
            return c.execute(schedules.delete().where(schedules.c.id == sid, schedules.c.project_id == project_id)).rowcount > 0

    # ---- firing -------------------------------------------------------------------------------------------------------------
    def tick(self) -> "list[dict]":
        """Start every schedule that is due. Returns what happened, one item per due schedule: {"id", "outcome", "run_id"}."""
        now = self._clock()
        with self.engine.connect() as c:
            due = [self._view(r) for r in c.execute(select(schedules).where(schedules.c.enabled.is_(True), schedules.c.next_run_at.is_not(None),
                                                                         schedules.c.next_run_at <= _utc(now)))]
        return [self._fire(s, now) for s in due]

    def _pause(self, sid: str, reason: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(schedules).where(schedules.c.id == sid).values(enabled=False, next_run_at=None, paused_reason=reason[:200]))

    def _fire(self, s: dict, now: datetime) -> dict:
        nxt = _utc(next_after(now, s["frequency"], s["at_time"], s["weekday"], s["timezone"]))
        with self.engine.begin() as c:                                              # claim: only one caller can move the due time on
            claimed = c.execute(update(schedules).where(schedules.c.id == s["id"], schedules.c.enabled.is_(True), schedules.c.next_run_at == s["next_run_at"])
                                .values(next_run_at=nxt)).rowcount
        if not claimed:
            return {"id": s["id"], "outcome": "claimed_elsewhere", "run_id": None}
        if not self._active(s["created_by"]):
            self._pause(s["id"], "The person who set this schedule up can no longer sign in. Someone else must re-enable it.")
            return {"id": s["id"], "outcome": "paused_creator", "run_id": None}
        actor = f"schedule:{s['id']}"
        with self.engine.connect() as c:
            busy = c.execute(select(runs.c.id).where(runs.c.requested_by == actor, runs.c.status.in_(ACTIVE)).limit(1)).first()
        if busy:
            self._note(s["id"], "skipped", None)
            return {"id": s["id"], "outcome": "skipped_previous_still_going", "run_id": None}
        try:
            sources, used = self.jobs.build_request(s["project_id"], s["connections"])
            run, _ = self.jobs.start(s["project_id"], sources, used, actor, f"scheduled: {s['name']}", f"{s['id']}:{s['next_run_at']}")
        except RunError as e:
            self._failed(s["id"], str(e)[:200])
            return {"id": s["id"], "outcome": "could_not_start", "run_id": None}
        self._note(s["id"], "started", run.id, last_run_at=_utc(now))
        return {"id": s["id"], "outcome": "started", "run_id": run.id}

    def _note(self, sid: str, status: str, run_id: str | None, **extra) -> None:
        with self.engine.begin() as c:
            c.execute(update(schedules).where(schedules.c.id == sid).values(last_status=status, **({"last_run_id": run_id} if run_id else {}), **extra))

    def _failed(self, sid: str, reason: str, status: str = "could_not_start") -> None:
        with self.engine.begin() as c:
            row = c.execute(select(schedules.c.consecutive_failures).where(schedules.c.id == sid)).first()
            n = (row.consecutive_failures if row else 0) + 1
            c.execute(update(schedules).where(schedules.c.id == sid).values(last_status=status, consecutive_failures=n))
        if n >= PAUSE_AFTER_FAILURES:
            self._pause(sid, f"Paused after {n} failures in a row. Last problem: {reason}")

    def on_run_finished(self, run) -> None:
        """Called when any run ends: a scheduled run updates its schedule's outcome, and three failures in a row pause it."""
        if not run.requested_by.startswith("schedule:"):
            return
        sid = run.requested_by.split(":", 1)[1]
        if run.status == "succeeded":
            with self.engine.begin() as c:
                c.execute(update(schedules).where(schedules.c.id == sid).values(last_status="succeeded", last_run_id=run.id, consecutive_failures=0))
        elif run.status in ("failed", "interrupted"):
            self._note(sid, run.status, run.id)
            self._failed(sid, "the run did not finish", run.status)
        else:
            self._note(sid, run.status, run.id)


class SchedulerThread:
    """Calls `tick` every few seconds until stopped. One per API process; a second process is harmless (the claim is atomic)."""

    def __init__(self, schedules_: Schedules, interval_s: float = 30.0, on_error=None):
        self.s, self.interval, self.on_error = schedules_, interval_s, on_error
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.s.tick()
            except Exception as e:  # noqa: BLE001 - a bad tick must never kill the scheduler
                if self.on_error:
                    self.on_error(e)


__all__ = ["Schedules", "SchedulerThread", "ScheduleError", "SCHEDULE_ID", "FREQUENCIES", "next_after", "validate", "zone"]
