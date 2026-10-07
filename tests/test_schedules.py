"""Scheduled runs: when they are due (time zones, daylight saving), that they start exactly once, once after downtime, never pile up, pause themselves
when they keep failing or their creator is gone, and who may manage them."""
import sys
from datetime import datetime, timedelta, timezone

import pytest
from api_world import PROJECT, csv_connection, hdr, make_live, small_csv
from fastapi.testclient import TestClient

from dedupe.api import ApiSettings, create_app
from dedupe.workspace.schedules import ScheduleError, next_after, validate

P = f"/projects/{PROJECT}"
UTC = timezone.utc
ADMIN = ("admin", "u-root")


def at(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def quick(code="pass"):
    return lambda run_dir, sources, registry: [sys.executable, "-c", code]


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


START = at(2026, 10, 7, 12, 0)


def world(tmp_path, command, start=START):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="s" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}",
                    ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6)
    app = create_app(s, command=command)
    sv = app.state.services
    sv.workspace.create(PROJECT, "Demo", "", "setup")
    clock = Clock(start)
    sv.schedules._clock = clock
    c = TestClient(app)
    make_live(c, s, csv_connection(small_csv(tmp_path / "crm.csv")))
    return c, s, sv, clock


def new(c, s, **kw):
    body = {"name": "nightly", "connections": ["crm"], "frequency": "daily", "at_time": "02:00", "timezone": "UTC", **kw}
    return c.post(P + "/schedules", json=body, headers=hdr(s, *ADMIN))


def test_next_time_is_the_first_matching_moment_after_now_in_the_schedules_own_time_zone():
    base = at(2026, 10, 7, 12, 0)                                                                           # a Wednesday
    assert next_after(base, "daily", "02:00", None, "UTC") == at(2026, 10, 8, 2, 0)
    assert next_after(at(2026, 10, 7, 1, 0), "daily", "02:00", None, "UTC") == at(2026, 10, 7, 2, 0)
    assert next_after(at(2026, 10, 7, 2, 0), "daily", "02:00", None, "UTC") == at(2026, 10, 8, 2, 0)          # strictly after
    assert next_after(base, "daily", "02:00", None, "Asia/Kolkata") == at(2026, 10, 7, 20, 30)               # 02:00 in India is 20:30 UTC the day before
    assert next_after(base, "weekly", "09:00", 0, "UTC") == at(2026, 10, 12, 9, 0)                           # next Monday
    assert next_after(base, "weekly", "09:00", 2, "UTC") == at(2026, 10, 14, 9, 0)                           # today is Wednesday and 09:00 has passed
    assert next_after(base, "weekly", "13:00", 2, "UTC") == at(2026, 10, 7, 13, 0)
    assert next_after(at(2026, 10, 7, 12, 20), "hourly", "00:15", None, "UTC") == at(2026, 10, 7, 13, 15)
    assert next_after(at(2026, 10, 7, 12, 10), "hourly", "00:15", None, "UTC") == at(2026, 10, 7, 12, 15)
    # daylight saving: 02:30 New York does not exist on 2027-03-14; the result is still a real moment after "now", within a day
    after = at(2027, 3, 13, 12, 0)
    nxt = next_after(after, "daily", "02:30", None, "America/New_York")
    assert after < nxt <= after + timedelta(hours=25)
    summer, winter = next_after(at(2026, 7, 1, 0, 0), "daily", "09:00", None, "America/New_York"), next_after(at(2026, 12, 1, 0, 0), "daily", "09:00", None, "America/New_York")
    assert (summer.hour, winter.hour) == (13, 14)                                                         # 09:00 local is a different UTC hour in summer and winter


def test_a_schedule_must_be_well_formed():
    ok = ("nightly", ["crm"], "daily", "02:00", None, "UTC")
    validate(*ok)
    for bad in (("", ["crm"], "daily", "02:00", None, "UTC"), ("x", [], "daily", "02:00", None, "UTC"), ("x", ["crm", "crm"], "daily", "02:00", None, "UTC"),
                ("x", ["a b"], "daily", "02:00", None, "UTC"), ("x", ["crm"], "monthly", "02:00", None, "UTC"), ("x", ["crm"], "daily", "25:00", None, "UTC"),
                ("x", ["crm"], "weekly", "02:00", None, "UTC"), ("x", ["crm"], "weekly", "02:00", 9, "UTC"), ("x", ["crm"], "daily", "02:00", None, "Mars/Olympus")):
        with pytest.raises(ScheduleError):
            validate(*bad)


def test_a_due_schedule_starts_one_ordinary_run_exactly_once(tmp_path):
    c, s, sv, clock = world(tmp_path, quick())
    created = new(c, s)
    assert created.status_code == 201 and created.json()["next_run_at"] == "2026-10-08T02:00:00+00:00" and created.json()["enabled"] is True
    sid = created.json()["id"]
    assert sv.schedules.tick() == []                                                                       # not due yet
    clock.now = at(2026, 10, 8, 2, 0, 5)
    first = sv.schedules.tick()
    assert [(f["id"], f["outcome"]) for f in first] == [(sid, "started")]
    run = sv.jobs.wait(PROJECT, first[0]["run_id"], 30)
    assert run.status == "succeeded" and run.requested_by == f"schedule:{sid}" and run.reason == "scheduled: nightly" and run.connections == [{"id": "crm", "version": 1}]
    assert sv.schedules.tick() == []                                                                       # the due time moved on: nothing to start again
    after = c.get(P + "/schedules", headers=hdr(s, "viewer")).json()[0]
    assert after["next_run_at"] == "2026-10-09T02:00:00+00:00" and after["last_run_id"] == run.id and after["last_status"] == "succeeded"
    stale = dict(after, next_run_at="2026-10-08T02:00:00+00:00")                                           # a second process holding the old due time
    assert sv.schedules._fire(stale, clock.now)["outcome"] == "claimed_elsewhere"
    assert len(sv.jobs.recent(PROJECT)) == 1


def test_after_downtime_a_missed_schedule_runs_once_not_once_per_missed_slot(tmp_path):
    c, s, sv, clock = world(tmp_path, quick())
    new(c, s)
    clock.now = at(2026, 10, 14, 9, 0)                                                                     # the server was off for six nights
    fired = sv.schedules.tick()
    assert [f["outcome"] for f in fired] == ["started"] and sv.schedules.tick() == []
    sv.jobs.wait(PROJECT, fired[0]["run_id"], 30)
    assert c.get(P + "/schedules", headers=hdr(s, "viewer")).json()[0]["next_run_at"] == "2026-10-15T02:00:00+00:00"


def test_a_schedule_does_not_pile_up_runs_while_its_last_one_is_still_going(tmp_path):
    gate = tmp_path / "go"
    code = f"import pathlib,time\nfor _ in range(600):\n  time.sleep(0.05)\n  if pathlib.Path(r'{gate}').exists(): break\n"
    c, s, sv, clock = world(tmp_path, quick(code))
    new(c, s, frequency="hourly", at_time="00:00")
    clock.now = at(2026, 10, 7, 13, 0, 1)
    first = sv.schedules.tick()
    assert first[0]["outcome"] == "started"
    clock.now = at(2026, 10, 7, 14, 0, 1)
    assert [f["outcome"] for f in sv.schedules.tick()] == ["skipped_previous_still_going"]
    assert c.get(P + "/schedules", headers=hdr(s, "viewer")).json()[0]["last_status"] == "skipped"
    gate.write_text("x")
    sv.jobs.wait(PROJECT, first[0]["run_id"], 30)
    clock.now = at(2026, 10, 7, 15, 0, 1)
    assert [f["outcome"] for f in sv.schedules.tick()] == ["started"]
    assert len(sv.jobs.recent(PROJECT)) == 2


def test_three_failed_runs_in_a_row_pause_the_schedule_and_it_can_be_resumed(tmp_path):
    c, s, sv, clock = world(tmp_path, quick("import sys\nsys.exit(3)\n"))
    sid = new(c, s, frequency="hourly", at_time="00:00").json()["id"]
    for hour in (13, 14, 15):
        clock.now = at(2026, 10, 7, hour, 0, 1)
        out = sv.schedules.tick()
        assert out and out[0]["outcome"] == "started"
        assert sv.jobs.wait(PROJECT, out[0]["run_id"], 30).status == "failed"
    s3 = c.get(P + "/schedules", headers=hdr(s, "viewer")).json()[0]
    assert s3["enabled"] is False and s3["next_run_at"] is None and "3 failures in a row" in s3["paused_reason"] and s3["last_status"] == "failed"
    clock.now = at(2026, 10, 7, 16, 0, 1)
    assert sv.schedules.tick() == []                                                                       # paused: nothing starts
    back = c.patch(f"{P}/schedules/{sid}", json={"enabled": True}, headers=hdr(s, *ADMIN)).json()
    assert back["enabled"] is True and back["paused_reason"] is None and back["consecutive_failures"] == 0 and back["next_run_at"] == "2026-10-07T17:00:00+00:00"


def test_a_connection_that_is_no_longer_live_or_a_creator_who_is_gone_is_handled_without_a_run(tmp_path):
    c, s, sv, clock = world(tmp_path, quick())
    admin = hdr(s, *ADMIN)
    other = {**csv_connection(small_csv(tmp_path / "late.csv"), cid="late")}
    assert c.post(P + "/connections", json={"connection": other, "reason": "a second source, not live yet"}, headers=admin).status_code == 200     # proposed, never made live
    new(c, s, name="not-live", connections=["late"], frequency="hourly", at_time="00:00")
    clock.now = at(2026, 10, 7, 13, 0, 1)
    out = sv.schedules.tick()
    assert out[0]["outcome"] == "could_not_start" and sv.jobs.recent(PROJECT) == []
    shown = c.get(P + "/schedules", headers=hdr(s, "viewer")).json()[0]
    assert shown["last_status"] == "could_not_start" and shown["consecutive_failures"] == 1 and shown["enabled"] is True
    sv.schedules._active = lambda sub: False                                                                 # the person who made it can no longer sign in
    clock.now = at(2026, 10, 7, 14, 0, 1)
    assert sv.schedules.tick()[0]["outcome"] == "paused_creator"
    assert "can no longer sign in" in c.get(P + "/schedules", headers=hdr(s, "viewer")).json()[0]["paused_reason"]


def test_managing_schedules_needs_the_right_permission_a_known_connection_and_project_access(tmp_path):
    c, s, sv, clock = world(tmp_path, quick())
    admin, viewer = hdr(s, *ADMIN), hdr(s, "viewer")
    body = {"name": "nightly", "connections": ["crm"], "frequency": "daily", "at_time": "02:00", "timezone": "Asia/Kolkata"}
    assert c.post(P + "/schedules", json=body, headers=viewer).status_code == 403                           # viewers can look, not change
    assert c.post(P + "/schedules", json={**body, "connections": ["nope"]}, headers=admin).status_code == 422
    assert c.post(P + "/schedules", json={**body, "timezone": "Nowhere/City"}, headers=admin).status_code == 422
    assert c.post(P + "/schedules", json={**body, "frequency": "weekly"}, headers=admin).status_code == 422           # weekly needs a day
    assert c.post(P + "/schedules", json={**body, "extra": 1}, headers=admin).status_code == 422
    sid = c.post(P + "/schedules", json=body, headers=admin).json()["id"]
    assert [x["id"] for x in c.get(P + "/schedules", headers=viewer).json()] == [sid]
    off = c.patch(f"{P}/schedules/{sid}", json={"enabled": False}, headers=admin).json()
    assert off["enabled"] is False and off["next_run_at"] is None and off["paused_reason"] == "Paused by a person."
    moved = c.patch(f"{P}/schedules/{sid}", json={"enabled": True, "at_time": "03:30", "frequency": "weekly", "weekday": 0}, headers=admin).json()
    assert moved["frequency"] == "weekly" and moved["weekday"] == 0 and moved["next_run_at"] == "2026-10-11T22:00:00+00:00"        # Monday 03:30 India = Sunday 22:00 UTC
    assert c.patch(f"{P}/schedules/S000000000000", json={"enabled": False}, headers=admin).status_code == 404
    c.post(P + "/access", json={"subject": "u-root", "role": "admin"}, headers=admin)                       # the project becomes members-only
    assert c.get(P + "/schedules", headers=hdr(s, "analyst", "outsider")).status_code == 403
    assert c.delete(f"{P}/schedules/{sid}", headers=viewer).status_code == 403
    assert c.delete(f"{P}/schedules/{sid}", headers=admin).status_code == 204 and c.delete(f"{P}/schedules/{sid}", headers=admin).status_code == 404
    actions = [e["detail"]["action"] for e in sv.seclog.events(limit=300, event_name="config_change") if e["object_ref"].startswith("schedule/")]
    assert actions == ["create_schedule", "change_schedule", "change_schedule", "delete_schedule"]


def test_the_background_loop_keeps_ticking_through_errors_and_stops_cleanly():
    import time as _time

    from dedupe.workspace.schedules import SchedulerThread

    class Fake:
        def __init__(self):
            self.ticks, self.fail_first = 0, True

        def tick(self):
            self.ticks += 1
            if self.fail_first:
                self.fail_first = False
                raise RuntimeError("boom")

    fake, errors = Fake(), []
    loop = SchedulerThread(fake, interval_s=0.02, on_error=errors.append)
    loop.start()
    deadline = _time.time() + 5
    while fake.ticks < 4 and _time.time() < deadline:
        _time.sleep(0.02)
    loop.stop()
    assert fake.ticks >= 4 and len(errors) == 1                                       # one bad tick did not end the loop
    n = fake.ticks
    _time.sleep(0.1)
    assert fake.ticks == n and not loop._thread.is_alive()                            # stopped for good
