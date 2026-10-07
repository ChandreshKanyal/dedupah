"""The steward's inbox and decision history, the score band filter, deleting runs, uploads and projects, renaming, and the newest-first audit log."""
import json

import duckdb
import pytest
from api_world import PROJECT, app_with_run, hdr, pipeline_output

WHY = "checked both customer files side by side"
P = f"/projects/{PROJECT}"


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    d = tmp_path_factory.mktemp("apireview")
    out = pipeline_output(d, seed=75, households=80)
    pairs = duckdb.sql(f"SELECT cluster_l, cluster_r FROM '{out / 'review_queue.parquet'}' ORDER BY priority DESC LIMIT 4").fetchall()
    return {"out": out, "pairs": pairs}


@pytest.fixture
def api(output, tmp_path):
    return app_with_run(tmp_path, output["out"])


def test_inbox_counts_what_is_still_open_and_drops_as_people_decide(api, output):
    c, s, sv, run = api
    rid = run.rsplit("/", 1)[1]
    box = c.get("/review/inbox", headers=hdr(s, "steward", "u-asha")).json()
    row = next(x for x in box if x["project_id"] == PROJECT)
    assert row["run_id"] == rid and row["open"] > 0 and row["pending_approval"] == 0 and row["waiting_for_run"] == 0
    left, right = output["pairs"][0]
    r = c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right, "reason": WHY}, headers=hdr(s, "steward", "u-asha"))
    assert r.status_code == 200
    after = next(x for x in c.get("/review/inbox", headers=hdr(s, "steward", "u-asha")).json() if x["project_id"] == PROJECT)
    assert after["open"] == row["open"] - 1 and after["waiting_for_run"] == 1           # decided, not yet applied by a run
    home = c.get("/home", headers=hdr(s, "steward", "u-asha")).json()
    assert home["open_review_items"] == after["open"]                                    # the start page counts what is still open, not the run's total


def test_decision_history_shows_status_run_and_undo(api, output):
    c, s, sv, run = api
    rid = run.rsplit("/", 1)[1]
    left, right = output["pairs"][1]
    made = c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right, "reason": WHY}, headers=hdr(s, "steward", "u-asha")).json()
    rows = c.get(P + "/decisions?mine=true", headers=hdr(s, "steward", "u-asha")).json()
    assert len(rows) == 1 and rows[0]["action_id"] == made["action_id"] and rows[0]["run_id"] == rid and rows[0]["yours"]
    assert rows[0]["status"] == "waiting_for_run" and rows[0]["persons"] == [left, right] and rows[0]["kind"] == "different_person"
    assert c.get(P + "/decisions?mine=true", headers=hdr(s, "steward", "u-other")).json() == []
    undo = c.post(f"{P}/decisions/{made['action_id']}/undo", json={"reason": "clicked the wrong button"}, headers=hdr(s, "steward", "u-asha"))
    assert undo.status_code == 200
    assert c.get(P + "/decisions", headers=hdr(s, "viewer")).json()[0]["status"] == "undone"
    assert c.get(P + "/decisions?status=undone", headers=hdr(s, "viewer")).json()[0]["action_id"] == made["action_id"]


def test_queue_score_band(api):
    c, s, _, run = api
    h = hdr(s, "viewer")
    low = c.get(run + "/queue?limit=200&max_probability=0.5", headers=h).json()
    high = c.get(run + "/queue?limit=200&min_probability=0.5", headers=h).json()
    assert all(i["top_probability"] <= 0.5 for i in low) and all(i["top_probability"] >= 0.5 for i in high)


def test_the_run_says_when_a_newer_run_exists(api):
    c, s, sv, run = api
    body = c.get(run, headers=hdr(s, "viewer")).json()
    assert body["latest_run_id"] == run.rsplit("/", 1)[1]


def test_deleting_a_run_erases_its_files_keeps_its_history_and_decisions(api, output):
    c, s, sv, run = api
    rid = run.rsplit("/", 1)[1]
    left, right = output["pairs"][2]
    assert c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right, "reason": WHY}, headers=hdr(s, "steward", "u-asha")).status_code == 200
    assert c.post(run + "/delete", json={"reason": "short"}, headers=hdr(s, "analyst")).status_code == 422          # a reason is required
    assert c.post(run + "/delete", json={"reason": "old results, not needed"}, headers=hdr(s, "steward")).status_code == 403
    r = c.post(run + "/delete", json={"reason": "old results, not needed"}, headers=hdr(s, "analyst", "u-ana"))
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    assert not sv.workspace.run_dir(PROJECT, rid).exists()
    assert c.get(run, headers=hdr(s, "viewer")).status_code == 404 and c.get(P + "/runs", headers=hdr(s, "viewer")).json() == []
    assert c.post(run + "/delete", json={"reason": "old results, not needed"}, headers=hdr(s, "analyst")).status_code == 404
    assert len(c.get(P + "/decisions", headers=hdr(s, "viewer")).json()) == 1                                       # decisions belong to the project
    ev = [e for e in sv.seclog.events(limit=500) if e["event"] == "data_deleted"]
    assert ev and ev[-1]["actor"] == "u-ana" and ev[-1]["object_ref"] == f"{PROJECT}/{rid}"


def test_uploads_list_their_users_and_only_unused_files_can_be_deleted(api, tmp_path):
    c, s, sv, _ = api
    raw = tmp_path / "people.csv"
    raw.write_text("id,full_name,mobile\n1,Asha Rao,9876543210\n2,Ravi K,9876500000\n", encoding="utf-8")
    h = hdr(s, "platform_admin", "u-ops", **{"Content-Type": "application/octet-stream"})
    up = c.post(P + "/uploads?filename=people.csv", content=raw.read_bytes(), headers=h).json()
    uid = up["upload_id"]
    conn = {"source_id": "file1", "connector": {"type": "upload", "upload_id": uid}, "record_id": "id", "fields": {"full_name": "full_name"}, "attributes": []}
    assert c.post(P + "/connections", json={"connection": conn, "reason": "the uploaded list of people"}, headers=hdr(s, "platform_admin", "u-ops")).status_code == 200
    listing = c.get(P + "/uploads", headers=hdr(s, "platform_admin")).json()
    assert listing[0]["upload_id"] == uid and listing[0]["used_by"] == ["file1"]
    refused = c.delete(f"{P}/uploads/{uid}", headers=hdr(s, "platform_admin"))
    assert refused.status_code == 409 and "file1" in refused.json()["error"]["message"]
    orphan = c.post(P + "/uploads?filename=people.csv", content=raw.read_bytes(), headers=h).json()["upload_id"]
    assert c.delete(f"{P}/uploads/{orphan}", headers=hdr(s, "platform_admin")).status_code == 204
    assert not sv.uploads.path(orphan).exists()
    assert c.delete(f"{P}/uploads/{orphan}", headers=hdr(s, "platform_admin")).status_code == 404


def test_rename_and_delete_a_project(api):
    c, s, sv, _ = api
    h = hdr(s, "admin", "u-boss")
    r = c.patch(P + "/settings", json={"name": "Customer master", "description": "All sources"}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "Customer master" and r.json()["description"] == "All sources"
    assert c.patch(P + "/settings", json={"name": ""}, headers=h).status_code == 422
    assert c.post(P + "/delete", json={"confirm": "wrong", "reason": "the pilot is over"}, headers=h).status_code == 422
    sv.workspace.grant(PROJECT, "u-owner", "owner", "setup")                               # once a project has members, only its owner (or admin) deletes
    assert c.post(P + "/delete", json={"confirm": PROJECT, "reason": "the pilot is over"}, headers=hdr(s, "analyst", "u-ana")).status_code == 403
    gone = c.post(P + "/delete", json={"confirm": PROJECT, "reason": "the pilot is over"}, headers=hdr(s, "analyst", "u-owner"))
    assert gone.status_code == 200 and gone.json()["runs"] == 1
    assert c.get(P, headers=h).status_code == 404 and not sv.workspace.project_dir(PROJECT).exists()
    assert all(x["id"] != PROJECT for x in c.get("/projects", headers=h).json())
    ev = [e for e in sv.seclog.events(limit=500) if e["event"] == "data_deleted"]
    assert ev[-1]["object_ref"] == PROJECT and "pilot" in json.dumps(ev[-1]["detail"])


def test_audit_log_newest_first_with_paging(api):
    c, s, sv, _ = api
    for i in range(5):
        sv.seclog.record("startup", actor=f"t{i}", status=200)
    h = hdr(s, "auditor")
    page = c.get("/security/events?newest_first=true&limit=3", headers=h).json()
    assert [e["actor"] for e in page] == ["t4", "t3", "t2"]
    more = c.get(f"/security/events?newest_first=true&limit=3&before_id={page[-1]['id']}", headers=h).json()
    assert more[0]["actor"] == "t1"


def test_golden_record_guide_and_a_form_style_proposal(api):
    c, s, _, run = api
    g = c.get(P + "/golden-record-guide", headers=hdr(s, "analyst")).json()
    assert "full_name" in g["fields"] and "best_quality" in g["strategies"]
    assert g["last_run"]["run_id"] == run.rsplit("/", 1)[1] and g["last_run"]["trust_order"]       # how the latest run ranked its sources
    assert c.get(run, headers=hdr(s, "viewer")).json()["summary"]["golden"]["trust_order"] == g["last_run"]["trust_order"]
    content = c.get(P + "/config/survivorship/default", headers=hdr(s, "analyst")).json()["content"]
    content |= {"source_trust": ["kyc_documents"], "field_trust": {"mobile": ["bank_crm"]}, "source_dates": {"leads": "created_at"},
                "fields": {**content["fields"], "full_name": "best_quality"}, "shared_value_limit": 5}
    r = c.post(P + "/config/survivorship/propose", json={"content": content, "reason": "phones come from the bank CRM"}, headers=hdr(s, "analyst"))
    assert r.status_code == 200, r.text
    bad = content | {"field_trust": {"not_a_field": ["x"]}}
    assert c.post(P + "/config/survivorship/propose", json={"content": bad, "reason": "this one must be refused"}, headers=hdr(s, "analyst")).status_code == 422
