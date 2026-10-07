"""Members-only projects (business-unit scoping) and cancelling a run."""
import time

from api_world import PROJECT, hdr, pipeline_output
from test_api_runs import P, fake, world

SLEEPER = "import time\ntime.sleep(120)\n"


def test_a_project_without_grants_is_open_and_the_first_grant_makes_it_members_only(tmp_path, tmp_path_factory):
    out = pipeline_output(tmp_path_factory.mktemp("acc_out"), seed=79, households=40)
    c, s, sv, run = world(tmp_path, fake("pass"), out)
    admin, alice, bob = hdr(s, "platform_admin", "u-ops"), hdr(s, "steward", "alice"), hdr(s, "steward", "bob")
    assert c.get(run + "/queue", headers=bob).status_code == 200                                   # no grants yet: open, as before
    assert c.post(P + "/access", json={"subject": "alice", "role": "steward"}, headers=admin).status_code == 201
    assert c.get(run + "/queue", headers=alice).status_code == 200                                 # a member
    for url in (run + "/queue", P, P + "/runs", run, P + "/config/decision"):
        assert c.get(url, headers=bob).status_code == 403, url                                     # an outsider: every project and run route
    assert c.post(run + "/decisions/unsure", json={"person_l": "P000000001", "person_r": "P000000002", "reason": "x"}, headers=bob).status_code == 403
    assert c.get(run + "/queue", headers=hdr(s, "auditor", "u-aud")).status_code == 200                # oversight sees every project
    assert c.get(P, headers=hdr(s, "platform_admin", "u-ops")).status_code == 200
    assert c.get(P, headers=hdr(s, "auditor", "u-aud")).status_code == 200
    assert [x["id"] for x in c.get("/projects", headers=alice).json()] == [PROJECT]                # lists hide it from outsiders
    assert c.get("/projects", headers=bob).json() == []
    assert c.get("/runs", headers=bob).json() == [] and c.get("/home", headers=bob).json()["projects"] == 0
    denied = [e for e in sv.seclog.events(limit=500, event_name="permission_denied") if e["actor"] == "bob"]
    assert denied and denied[-1]["detail"]["needs"] == f"member of {PROJECT}"


def test_only_a_platform_admin_manages_access_and_removing_the_last_grant_reopens_the_project(tmp_path):
    c, s, sv, _ = world(tmp_path, fake("pass"))
    admin = hdr(s, "platform_admin", "u-ops")
    for role in ("steward", "approver", "rule_admin", "auditor", "viewer"):
        assert c.post(P + "/access", json={"subject": "x", "role": "steward"}, headers=hdr(s, role, "u-" + role)).status_code == 403
        assert c.get(P + "/access", headers=hdr(s, role, "u-" + role)).status_code == 403
    assert c.post(P + "/access", json={"subject": "x", "role": "emperor"}, headers=admin).status_code == 422
    assert c.post(P + "/access", json={"subject": "x", "role": "steward", "extra": 1}, headers=admin).status_code == 422
    assert c.post(P + "/access", json={"subject": "alice", "role": "steward"}, headers=admin).json()["granted_by"] == "u-ops"
    assert c.post(P + "/access", json={"subject": "alice", "role": "steward"}, headers=admin).status_code == 201     # same grant twice: still one row
    assert [(g["subject"], g["role"]) for g in c.get(P + "/access", headers=admin).json()] == [("alice", "steward")]
    assert c.get(P, headers=hdr(s, "steward", "bob")).status_code == 403
    assert c.delete(P + "/access/alice/steward", headers=admin).status_code == 204
    assert c.delete(P + "/access/alice/steward", headers=admin).status_code == 404
    assert c.get(P, headers=hdr(s, "steward", "bob")).status_code == 200                                              # no grants left: open again
    assert c.post("/projects/nope-here/access", json={"subject": "x", "role": "steward"}, headers=admin).status_code == 404


def test_a_running_run_can_be_cancelled_and_the_machine_is_free_again(tmp_path):
    c, s, sv, _ = world(tmp_path, fake(SLEEPER))
    admin = hdr(s, "platform_admin", "u-ops")
    rid = c.post(P + "/runs", json={"connections": ["crm"]}, headers=admin).json()["id"]
    assert sv.jobs.active().id == rid
    for role in ("steward", "approver", "rule_admin", "auditor", "viewer"):
        assert c.post(f"{P}/runs/{rid}/cancel", headers=hdr(s, role, "u-" + role)).status_code == 403
    started = time.time()
    r = c.post(f"{P}/runs/{rid}/cancel", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and time.time() - started < 30
    assert sv.jobs.active() is None and rid not in sv.jobs._procs and rid not in sv.jobs._cancelled
    assert c.get(f"{P}/runs/{rid}", headers=admin).json()["status"] == "cancelled"
    assert c.get(f"{P}/runs/{rid}/queue", headers=hdr(s, "viewer")).status_code == 409                # a cancelled run has no results
    assert c.post(f"{P}/runs/{rid}/cancel", headers=admin).status_code == 409                          # already over
    assert c.post(f"{P}/runs/R000000000000/cancel", headers=admin).status_code == 404
    assert c.post(P + "/runs", json={"connections": ["crm"]}, headers=admin).status_code == 202        # the next run may start
    sv.jobs.cancel(PROJECT, sv.jobs.active().id, "cleanup")


def test_a_run_that_finishes_by_itself_is_not_called_cancelled_and_a_crash_is_not_a_cancel(tmp_path):
    c, s, sv, _ = world(tmp_path, fake("import sys\nsys.exit(3)\n"))
    admin = hdr(s, "platform_admin", "u-ops")
    rid = c.post(P + "/runs", json={"connections": ["crm"]}, headers=admin).json()["id"]
    assert sv.jobs.wait(PROJECT, rid, 30).status == "failed"
    assert c.post(f"{P}/runs/{rid}/cancel", headers=admin).status_code == 409
