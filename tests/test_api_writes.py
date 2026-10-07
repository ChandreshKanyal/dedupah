"""Write endpoints: decisions on a run as the verified caller, idempotency, four eyes per project, strict bodies, the project's rule workflow, logging."""
import json

import duckdb
import pytest
from api_world import PROJECT, app_with_run, hdr, pipeline_output

WHY = "checked both customer files side by side"
NEW_PAN = "ZZZZZ9999Z"
P = f"/projects/{PROJECT}"


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    d = tmp_path_factory.mktemp("apiwrite")
    out = pipeline_output(d, seed=74, households=100)
    pairs = duckdb.sql(f"SELECT cluster_l, cluster_r FROM '{out / 'review_queue.parquet'}' ORDER BY priority DESC LIMIT 6").fetchall()
    person = duckdb.sql(f"SELECT cluster_id FROM '{out / 'golden' / 'golden_records.parquet'}' WHERE record_count > 1 LIMIT 1").fetchone()[0]
    return {"out": out, "pairs": pairs, "person": person}


@pytest.fixture
def api(output, tmp_path):
    return app_with_run(tmp_path, output["out"])


def judgments(sv):
    return sv.workspace.stores(PROJECT)[0].history(("same_person", "different_person", "unsure", "split", "field_override"))


def test_a_decision_is_recorded_as_the_verified_caller_and_replays_with_the_same_key(api, output):
    c, s, sv, run = api
    left, right = output["pairs"][0]
    h = hdr(s, "steward", "u-asha", **{"Idempotency-Key": "retry-key-0001"})
    first = c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right, "reason": WHY}, headers=h)
    assert first.status_code == 200 and first.json()["replayed"] is False and first.json()["judgment_ids"]
    again = c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right, "reason": WHY}, headers=h)
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["judgment_ids"] == first.json()["judgment_ids"]
    made = judgments(sv)
    assert len(made) == len(first.json()["judgment_ids"]) and {j.actor for j in made} == {"u-asha"}
    assert c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right, "actor": "someone-else"}, headers=h).status_code == 422


def test_decisions_belong_to_their_project_only(api, output, tmp_path):
    c, s, sv, run = api
    sv.workspace.create("other", "Another project", "", "setup")
    left, right = output["pairs"][0]
    c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right}, headers=hdr(s, "steward"))
    assert judgments(sv) and sv.workspace.stores("other")[0].history() == []


def test_unsure_then_undo_restores_the_queue_item(api, output):
    c, s, sv, run = api
    left, right = output["pairs"][1]
    h = hdr(s, "steward")
    done = c.post(run + "/decisions/unsure", json={"person_l": left, "person_r": right}, headers=h).json()
    assert [i for i in c.get(run + "/queue?status=open&limit=5", headers=h).json() if (i["person_l"], i["person_r"]) == (left, right)] == []
    undo = c.post(f"{P}/decisions/{done['action_id']}/undo", json={"reason": "wrong pair"}, headers=h)
    assert undo.status_code == 200 and sorted(undo.json()["revoked"]) == sorted(done["judgment_ids"])
    assert c.post(f"{P}/decisions/{done['action_id']}/undo", json={}, headers=h).status_code == 409


def test_approving_a_pair_merges_or_explains_why_not(api, output):
    c, s, sv, run = api
    h = hdr(s, "steward")
    outcomes = [c.post(run + "/decisions/approve", json={"person_l": l, "person_r": r, "reason": WHY}, headers=h) for l, r in output["pairs"][2:5]]
    assert {o.status_code for o in outcomes} <= {200, 409} and any(o.status_code == 200 for o in outcomes)


def test_bulk_decides_each_pair_alone_and_is_capped(api, output):
    c, s, sv, run = api
    h = hdr(s, "steward")
    (l1, r1), (l2, r2) = output["pairs"][3:5]
    body = {"decision": "reject", "items": [{"person_l": l1, "person_r": r1}, {"person_l": "P999999998", "person_r": "P999999999"}, {"person_l": l2, "person_r": r2}]}
    r = c.post(run + "/decisions/bulk", json=body, headers=h)
    assert r.status_code == 200 and len(r.json()["done"]) == 2 and len(r.json()["failed"]) == 1
    assert c.post(run + "/decisions/bulk", json={"decision": "reject", "items": [{"person_l": l1, "person_r": r1}] * 101}, headers=h).status_code == 422


def test_bodies_are_strict(api, output):
    c, s, _, run = api
    h = hdr(s, "steward")
    left, right = output["pairs"][0]
    pair = {"person_l": left, "person_r": right}
    assert c.post(run + "/decisions/reject", json={**pair, "extra": 1}, headers=h).status_code == 422
    assert c.post(run + "/decisions/reject", json=pair, headers={**h, "Idempotency-Key": "short"}).status_code == 422
    person = output["person"]
    assert c.post(f"{run}/people/{person}/override", json={"field": "colour", "value": "x", "reason": WHY}, headers=h).status_code == 422
    bad = c.post(f"{run}/people/{person}/split", json={"parts": [[{"source_id": "nope", "source_record_id": "1"}], [{"source_id": "nope", "source_record_id": "2"}]],
                                                     "reason": WHY}, headers=h)
    assert bad.status_code == 409 and "nope" not in bad.text


def test_a_sensitive_override_waits_for_a_second_person(api, output):
    c, s, sv, run = api
    person = output["person"]
    r = c.post(f"{run}/people/{person}/override", json={"field": "pan", "value": NEW_PAN, "reason": WHY}, headers=hdr(s, "steward", "u-sam"))
    assert r.status_code == 200 and r.json()["pending_approval"] is True
    jid = r.json()["judgment_ids"][0]
    own = c.get(P + "/approvals", headers=hdr(s, "steward", "u-sam")).json()          # the steward sees their own waiting decision, cannot approve it
    assert [(x["id"], x["yours"], x["field"]) for x in own] == [(jid, True, "pan")] and NEW_PAN not in json.dumps(own)
    assert c.get(P + "/approvals", headers=hdr(s, "steward", "u-other")).json() == []     # somebody else's waiting decisions stay with the approvers
    approver = hdr(s, "approver", "u-lee")
    assert [x["id"] for x in c.get(P + "/approvals", headers=approver).json()] == [jid]
    assert c.post(f"{P}/approvals/{jid}/approve", json={}, headers=hdr(s, ["steward", "approver"], "u-sam")).status_code == 409
    assert c.post(f"{P}/approvals/{jid}/approve", json={"reason": "second look done"}, headers=approver).status_code == 200
    assert c.get(P + "/approvals", headers=approver).json() == []
    events = json.dumps(sv.seclog.events(limit=100))
    assert NEW_PAN not in events and WHY not in events


def test_the_project_rule_workflow_proposes_approves_activates_dry_runs_and_rolls_back(api):
    c, s, sv, run = api
    cfg = json.loads(open("configs/decision_default.json", encoding="utf-8").read())
    admin, approver = hdr(s, "rule_admin", "u-ria"), hdr(s, "approver", "u-lee")
    assert c.post(P + "/config/decision/propose", json={"content": cfg, "reason": "start from the default"}, headers=admin).json()["version"] == 1
    assert c.post(P + "/config/decision/versions/1/approve", json={}, headers=hdr(s, ["rule_admin", "approver"], "u-ria")).status_code == 409
    assert c.post(P + "/config/decision/versions/1/approve", json={}, headers=approver).status_code == 200
    done = c.post(P + "/config/decision/versions/1/activate", json={}, headers=approver)
    assert done.status_code == 200 and done.json()["applies"] == "from the next run of this project"
    v2 = c.post(P + "/config/decision/propose", json={"content": {**cfg, "auto_merge_at": 0.97}, "reason": "stricter merge threshold"}, headers=admin).json()["version"]
    dry = c.post(f"{P}/config/decision/versions/{v2}/dry-run", json={}, headers=admin)
    assert dry.status_code == 200 and dry.json()["run_id"] == run.rsplit("/", 1)[1] and dry.json()["zones_after"]["auto_merge"] <= dry.json()["zones_before"]["auto_merge"]
    c.post(f"{P}/config/decision/versions/{v2}/approve", json={}, headers=approver)
    c.post(f"{P}/config/decision/versions/{v2}/activate", json={}, headers=approver)
    back = c.post(P + "/config/decision/rollback", json={"to_version": 1, "reason": "too strict on the dry run"}, headers=approver)
    assert back.status_code == 200 and sv.workspace.stores(PROJECT)[1].active("decision").version == v2 + 1
    r = c.post(P + "/config/decision/propose", json={"content": {"auto_merge_at": "SECRET-VALUE-123"}, "reason": "trying a bad value"}, headers=admin)
    assert r.status_code == 422 and "SECRET-VALUE-123" not in r.text
