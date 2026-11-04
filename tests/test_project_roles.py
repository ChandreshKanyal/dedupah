"""The role a person is given in a project is what they may do there: added as viewer = a viewer in that project, whatever their own roles
allow elsewhere. The owner, administration (projects.all) and everyone in a project without members keep their own roles (agent review P1-02)."""
import pytest
from api_world import app_with_run, hdr, pipeline_output

ALICE, BOB = ("analyst", "alice"), ("analyst", "bob")


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    return pipeline_output(tmp_path_factory.mktemp("roles_out"), seed=93, households=30)


@pytest.fixture
def world(tmp_path, output):
    c, s, sv, run = app_with_run(tmp_path, output, project="p-one", ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6, export_limit_per_min=10**6)
    sv.workspace.grant("p-one", "alice", "owner", "alice")
    return c, s, sv, run


def test_a_member_added_as_viewer_is_a_viewer_in_that_project(world):
    """The review's attack: bob, an analyst everywhere else, added to alice's project as viewer, unmasked, exported, proposed and deleted."""
    c, s, sv, run = world
    alice, bob = hdr(s, *ALICE), hdr(s, *BOB)
    assert c.post("/projects/p-one/access", json={"subject": "bob", "role": "viewer"}, headers=alice).status_code == 201
    assert c.get(run + "/datasets/golden_records/rows?limit=1", headers=bob).status_code == 200                    # a viewer reads (masked)
    for method, url, body in (("get", run + "/datasets/golden_records/rows?unmask=true&limit=1", None),
                              ("post", run + "/export", {"dataset": "golden_records", "format": "csv", "reason": "viewer exporting"}),
                              ("post", "/projects/p-one/config/decision/propose", {"content": {}, "reason": "viewer changes the rules"}),
                              ("post", "/projects/p-one/runs", {"connections": []}),
                              ("post", run + "/delete", {"reason": "a viewer deleting a run"})):
        r = c.request(method.upper(), url, json=body, headers=bob)
        assert r.status_code == 403, (url, r.status_code, r.text[:200])
    assert sv.jobs.get("p-one", run.rsplit("/", 1)[1]).status == "succeeded"                                         # the run is still there
    view = next(x for x in c.get("/projects", headers=bob).json() if x["id"] == "p-one")
    assert "pii.unmask" not in view["you_can"] and "queue.read" in view["you_can"]                                    # the portal hides what he may not do
    assert c.get("/me", headers=bob).json()["permissions"].count("pii.unmask") == 1                                  # his own roles are unchanged elsewhere

    # the owner, and a second role given in the project, add up; the project role never adds what his own roles lack
    assert c.get(run + "/datasets/golden_records/rows?unmask=true&limit=1", headers=alice).status_code == 200
    assert c.post("/projects/p-one/access", json={"subject": "bob", "role": "steward"}, headers=alice).status_code == 201
    assert c.get(run + "/datasets/golden_records/rows?unmask=true&limit=1", headers=bob).status_code == 200
    viewer_everywhere = hdr(s, "viewer", "carol")
    assert c.post("/projects/p-one/access", json={"subject": "carol", "role": "steward"}, headers=alice).status_code == 201
    assert c.get(run + "/datasets/golden_records/rows?unmask=true&limit=1", headers=viewer_everywhere).status_code == 403
    assert c.post("/projects/p-one/access", json={"subject": "bob", "role": "analyst"}, headers=alice).status_code == 201
    assert c.post(run + "/export", json={"dataset": "golden_records", "format": "csv", "reason": "now an analyst here"}, headers=bob).status_code == 200


def test_a_viewer_member_sees_no_approvals_and_no_review_work_he_cannot_do(world):
    c, s, sv, run = world
    alice, bob = hdr(s, *ALICE), hdr(s, *BOB)
    c.post("/projects/p-one/access", json={"subject": "bob", "role": "viewer"}, headers=alice)
    progress = next(x for x in c.get("/home", headers=bob).json()["project_list"] if x["id"] == "p-one")["progress"]
    assert progress["pending_approvals"] is None
    assert all(r["pending_approval"] == 0 for r in c.get("/review/inbox", headers=bob).json())


def test_copying_from_another_project_needs_read_rights_there(world, tmp_path):
    c, s, sv, run = world
    alice, bob = hdr(s, *ALICE), hdr(s, *BOB)
    sv.workspace.stores("p-one")[1].propose("connection/crm", {"source_id": "crm", "connector": {"type": "csv", "path": str(tmp_path / "x.csv")},
                                                                 "fields": {"full_name": "full_name"}}, "alice", "test")
    c.post("/projects", json={"id": "bobs", "name": "Bob's"}, headers=bob)
    sv.accounts.create_role("nothing-to-read", "reads nothing", ["queue.read"], "admin")
    c.post("/projects/p-one/access", json={"subject": "bob", "role": "nothing-to-read"}, headers=alice)
    r = c.post("/projects/bobs/connections/copy", json={"from_project": "p-one", "connection_id": "crm"}, headers=bob)
    assert r.status_code == 403, r.text
    c.post("/projects/p-one/access", json={"subject": "bob", "role": "viewer"}, headers=alice)
    assert c.post("/projects/bobs/connections/copy", json={"from_project": "p-one", "connection_id": "crm"}, headers=bob).status_code == 201
