"""Write endpoints on a real pipeline run: decisions as the verified caller, idempotency, four eyes, strict bodies, config workflow, logging."""
import json

import duckdb
import pytest
from fastapi.testclient import TestClient
from test_pipeline import _setup

from dedupe.api import ApiSettings, create_app
from dedupe.api.devtoken import issue
from dedupe.pipeline import PipelineConfig, run_pipeline

SECRET = "w" * 40
WHY = "checked both customer files side by side"
NEW_PAN = "ZZZZZ9999Z"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("apiwrite")
    syn, sources = _setup(d, seed=74, households=100)
    run_pipeline(PipelineConfig(sources, d / "work", judgments_url=f"sqlite:///{d / 'run.db'}"))
    pairs = duckdb.sql(f"SELECT cluster_l, cluster_r FROM '{d / 'work' / 'review_queue.parquet'}' ORDER BY priority DESC LIMIT 6").fetchall()
    gold = d / "work" / "golden" / "golden_records.parquet"
    person = duckdb.sql(f"SELECT cluster_id FROM '{gold}' WHERE record_count > 1 LIMIT 1").fetchone()[0]
    return {"d": d, "pairs": pairs, "person": person}


@pytest.fixture
def api(world, tmp_path):
    db = f"sqlite:///{tmp_path / 'api.db'}"                       # a fresh database per test: decisions never leak between tests
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=world["d"] / "work", database_url=db)
    app = create_app(s)
    return TestClient(app), s, app.state.services


def hdr(s, roles, sub=None, **extra):
    roles = [roles] if isinstance(roles, str) else roles
    return {"Authorization": "Bearer " + issue(s, sub or f"u-{roles[0]}", roles), **extra}


def judgments(sv):
    return sv.review.store.history(("same_person", "different_person", "unsure", "split", "field_override"))


def test_a_decision_is_recorded_as_the_verified_caller_and_replays_with_the_same_key(api, world):
    c, s, sv = api
    left, right = world["pairs"][0]
    h = hdr(s, "steward", "u-asha", **{"Idempotency-Key": "retry-key-0001"})
    first = c.post("/decisions/reject", json={"person_l": left, "person_r": right, "reason": WHY}, headers=h)
    assert first.status_code == 200 and first.json()["replayed"] is False and first.json()["judgment_ids"]
    again = c.post("/decisions/reject", json={"person_l": left, "person_r": right, "reason": WHY}, headers=h)
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["judgment_ids"] == first.json()["judgment_ids"]
    made = judgments(sv)
    assert len(made) == len(first.json()["judgment_ids"]) and {j.actor for j in made} == {"u-asha"}      # the caller's verified identity
    assert c.post("/decisions/reject", json={"person_l": left, "person_r": right, "actor": "someone-else"}, headers=h).status_code == 422   # no typed identity


def test_unsure_then_undo_restores_the_queue_item(api, world):
    c, s, sv = api
    left, right = world["pairs"][1]
    h = hdr(s, "steward")
    done = c.post("/decisions/unsure", json={"person_l": left, "person_r": right}, headers=h).json()
    assert [i for i in c.get("/queue?status=open&limit=5", headers=h).json() if (i["person_l"], i["person_r"]) == (left, right)] == []
    undo = c.post(f"/decisions/{done['action_id']}/undo", json={"reason": "wrong pair"}, headers=h)
    assert undo.status_code == 200 and sorted(undo.json()["revoked"]) == sorted(done["judgment_ids"])
    assert c.post(f"/decisions/{done['action_id']}/undo", json={}, headers=h).status_code == 409          # nothing left to undo
    assert c.post("/decisions/abc/undo", json={}, headers=h).status_code == 422


def test_approving_a_pair_merges_or_explains_why_not(api, world):
    c, s, sv = api
    h = hdr(s, "steward")
    outcomes = [c.post("/decisions/approve", json={"person_l": l, "person_r": r, "reason": WHY}, headers=h) for l, r in world["pairs"][2:5]]
    assert {o.status_code for o in outcomes} <= {200, 409}
    assert any(o.status_code == 200 for o in outcomes)
    for o in outcomes:
        if o.status_code == 409:
            assert "conflicting identifiers" in o.json()["error"]["message"]                  # the reason is stated, nothing else leaks


def test_bulk_decides_each_pair_alone_and_is_capped(api, world):
    c, s, sv = api
    h = hdr(s, "steward")
    (l1, r1), (l2, r2) = world["pairs"][3:5]
    body = {"decision": "reject", "items": [{"person_l": l1, "person_r": r1}, {"person_l": "P999999998", "person_r": "P999999999"}, {"person_l": l2, "person_r": r2}]}
    r = c.post("/decisions/bulk", json=body, headers=h)
    assert r.status_code == 200 and len(r.json()["done"]) == 2 and len(r.json()["failed"]) == 1
    assert r.json()["failed"][0]["pair"] == "P999999998,P999999999"
    big = {"decision": "reject", "items": [{"person_l": l1, "person_r": r1}] * 101}
    assert c.post("/decisions/bulk", json=big, headers=h).status_code == 422
    assert c.post("/decisions/bulk", json={"decision": "delete", "items": []}, headers=h).status_code == 422


def test_bodies_are_strict(api, world):
    c, s, _ = api
    h = hdr(s, "steward")
    left, right = world["pairs"][0]
    pair = {"person_l": left, "person_r": right}
    assert c.post("/decisions/reject", json={**pair, "extra": 1}, headers=h).status_code == 422
    assert c.post("/decisions/reject", json={"person_l": "P1", "person_r": right}, headers=h).status_code == 422
    assert c.post("/decisions/reject", json={**pair, "reason": "x" * 301}, headers=h).status_code == 422
    assert c.post("/decisions/reject", json=pair, headers={**h, "Idempotency-Key": "short"}).status_code == 422
    person = world["person"]
    assert c.post(f"/people/{person}/split", json={"parts": [[{"source_id": "a", "source_record_id": "1"}]], "reason": WHY}, headers=h).status_code == 422
    assert c.post(f"/people/{person}/override", json={"field": "colour", "value": "x", "reason": WHY}, headers=h).status_code == 422
    assert c.post(f"/people/{person}/override", json={"field": "city", "value": "Pune", "reason": "short"}, headers=h).status_code == 422
    bad = c.post(f"/people/{person}/split", json={"parts": [[{"source_id": "nope", "source_record_id": "1"}], [{"source_id": "nope", "source_record_id": "2"}]],
                                                  "reason": WHY}, headers=h)
    assert bad.status_code == 409 and "nope" not in bad.text                                  # a record that is not the person's: refused, not echoed


def test_a_sensitive_override_waits_for_a_second_person(api, world):
    c, s, sv = api
    person = world["person"]
    steward = hdr(s, "steward", "u-sam")
    r = c.post(f"/people/{person}/override", json={"field": "pan", "value": NEW_PAN, "reason": WHY}, headers=steward)
    assert r.status_code == 200 and r.json()["pending_approval"] is True
    jid = r.json()["judgment_ids"][0]
    assert c.get("/approvals", headers=steward).status_code == 403                                  # a steward cannot see or give approvals
    approver = hdr(s, "approver", "u-lee")
    assert [p["id"] for p in c.get("/approvals", headers=approver).json()] == [jid]
    both = hdr(s, ["steward", "approver"], "u-sam")                                                  # two roles, same person: still not allowed
    assert c.post(f"/approvals/{jid}/approve", json={}, headers=both).status_code == 409
    assert c.post(f"/approvals/{jid}/approve", json={"reason": "second look done"}, headers=approver).status_code == 200
    assert c.get("/approvals", headers=approver).json() == []
    assert c.post(f"/approvals/{jid}/approve", json={}, headers=approver).status_code == 409        # no longer pending
    assert c.post("/approvals/999/decline", json={"reason": "not needed"}, headers=approver).status_code == 409


def test_changes_are_logged_without_the_data_that_changed(api, world):
    c, s, sv = api
    person, (left, right) = world["person"], world["pairs"][0]
    h = hdr(s, "steward", "u-asha")
    c.post(f"/people/{person}/override", json={"field": "pan", "value": NEW_PAN, "reason": WHY}, headers=h)
    c.post("/decisions/reject", json={"person_l": left, "person_r": right, "reason": "private customer note " + "x" * 5}, headers=h)
    events = [e for e in sv.seclog.events(limit=100) if e["event"] == "judgment_change"]
    assert [(e["actor"], e["detail"]["action"]) for e in events] == [("u-asha", "override_field"), ("u-asha", "reject_match")]
    text = json.dumps(sv.seclog.events(limit=100))
    assert NEW_PAN not in text and "private customer note" not in text and WHY not in text
    assert sv.seclog.verify()["ok"]


def test_the_config_workflow_proposes_approves_activates_and_rolls_back(api, world):
    c, s, sv = api
    cfg = json.loads(open("configs/decision_default.json", encoding="utf-8").read())
    admin = hdr(s, "rule_admin", "u-ria")
    v1 = c.post("/config/decision/propose", json={"content": cfg, "reason": "start from the default"}, headers=admin)
    assert v1.status_code == 200 and v1.json() == {"name": "decision", "version": 1, "status": "proposed"}
    assert c.post("/config/decision/propose", json={"content": cfg, "reason": "again, the same thing"}, headers=admin).status_code == 409   # identical
    approver = hdr(s, "approver", "u-lee")
    assert c.post("/config/decision/versions/1/activate", json={}, headers=approver).status_code == 409                                  # not approved yet
    own = hdr(s, ["rule_admin", "approver"], "u-ria")
    assert c.post("/config/decision/versions/1/approve", json={}, headers=own).status_code == 409                                         # your own proposal
    assert c.post("/config/decision/versions/1/approve", json={}, headers=admin).status_code == 403
    assert c.post("/config/decision/versions/1/approve", json={"reason": "reviewed"}, headers=approver).status_code == 200
    assert c.post("/config/decision/versions/1/activate", json={}, headers=admin).status_code == 403
    done = c.post("/config/decision/versions/1/activate", json={"reason": "go live"}, headers=approver)
    assert done.status_code == 200 and done.json()["applies"] == "from the next pipeline run"
    v2 = c.post("/config/decision/propose", json={"content": {**cfg, "auto_merge_at": 0.95}, "reason": "stricter merge threshold"}, headers=admin).json()["version"]
    dry = c.post(f"/config/decision/versions/{v2}/dry-run", json={}, headers=admin)
    assert dry.status_code == 200 and {"pairs", "zones_before", "zones_after", "moved"} <= set(dry.json())
    assert c.post(f"/config/decision/versions/{v2}/decline", json={"reason": "too strict for now"}, headers=approver).status_code == 200
    assert c.post(f"/config/decision/versions/{v2}/activate", json={}, headers=approver).status_code == 409                              # declined
    v3 = c.post("/config/decision/propose", json={"content": {**cfg, "auto_merge_at": 0.97}, "reason": "even stricter, to compare"}, headers=admin).json()["version"]
    c.post(f"/config/decision/versions/{v3}/approve", json={}, headers=approver)
    c.post(f"/config/decision/versions/{v3}/activate", json={}, headers=approver)
    back = c.post("/config/decision/rollback", json={"to_version": 1, "reason": "v3 was worse in the dry run"}, headers=approver)
    assert back.status_code == 200 and back.json()["rolled_back_to"] == 1 and back.json()["version"] == v3 + 1
    assert sv.configs.active("decision").version == v3 + 1
    assert c.post("/config/decision/rollback", json={"to_version": 2, "reason": "never was live"}, headers=approver).status_code == 409
    actions = [e["detail"]["action"] for e in sv.seclog.events(limit=200) if e["event"] == "config_change"]
    assert actions[:3] == ["propose", "approve", "activate"] and actions[-1] == "rollback" and "dry_run" in actions


def test_invalid_configurations_name_the_field_but_not_the_value(api):
    c, s, _ = api
    admin = hdr(s, "rule_admin")
    r = c.post("/config/decision/propose", json={"content": {"auto_merge_at": "SECRET-VALUE-123"}, "reason": "trying a bad value"}, headers=admin)
    assert r.status_code == 422 and "auto_merge_at" in r.json()["error"]["message"] and "SECRET-VALUE-123" not in r.text
    assert c.post("/config/colours/propose", json={"content": {}, "reason": "unknown config"}, headers=admin).status_code == 422
    assert c.post("/config/settings/versions/1/dry-run", json={}, headers=admin).status_code in (404, 422)


def test_without_a_pipeline_run_decisions_say_so(tmp_path):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp_path / "empty", database_url=f"sqlite:///{tmp_path / 'e.db'}")
    c = TestClient(create_app(s))
    r = c.post("/decisions/reject", json={"person_l": "P000000001", "person_r": "P000000002"}, headers=hdr(s, "steward"))
    assert r.status_code == 404 and r.json()["error"]["message"] == "No pipeline run was found."


def test_the_configuration_store_is_unchanged_by_failed_proposals(api):
    c, s, sv = api
    c.post("/config/decision/propose", json={"content": {"auto_merge_at": "x"}, "reason": "a bad proposal here"}, headers=hdr(s, "rule_admin"))
    assert sv.configs.versions("decision") == []
