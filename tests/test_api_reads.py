"""Read endpoints on a real pipeline run: allow-listed responses, masking enforced by role, the unmask access log, paging caps, 404s."""
import json

import duckdb
import pytest
from fastapi.testclient import TestClient
from test_pipeline import _setup

from dedupe.api import ApiSettings, SecurityLog, create_app
from dedupe.api.devtoken import issue
from dedupe.governance import ConfigStore
from dedupe.pipeline import PipelineConfig, run_pipeline

SECRET = "r" * 40
REASON = "customer called to dispute a merge"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("apiread")
    syn, sources = _setup(d, seed=73, households=100)
    db = f"sqlite:///{d / 'api.db'}"
    run_pipeline(PipelineConfig(sources, d / "work", judgments_url=db))
    gold = d / "work" / "golden" / "golden_records.parquet"
    pan_person, pan = duckdb.sql(f"SELECT cluster_id, pan FROM '{gold}' WHERE pan IS NOT NULL AND record_count > 1 LIMIT 1").fetchone()
    pairs = duckdb.sql(f"SELECT cluster_l, cluster_r FROM '{d / 'work' / 'review_queue.parquet'}' ORDER BY priority DESC LIMIT 3").fetchall()
    configs = ConfigStore.open(db, solo=True)
    cfg = json.loads(open("configs/decision_default.json", encoding="utf-8").read())
    configs.activate("decision", configs.propose("decision", cfg, "asha", "first"), "asha")
    configs.propose("decision", {**cfg, "auto_merge_at": 0.95}, "asha", "stricter")
    return {"d": d, "db": db, "pan_person": pan_person, "pan": pan, "pairs": pairs}


@pytest.fixture
def api(world):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=world["d"] / "work", database_url=world["db"], max_page_size=5, unmask_limit_per_min=4)
    app = create_app(s)
    return TestClient(app), s, app.state.services


def hdr(s, role, sub=None):
    return {"Authorization": "Bearer " + issue(s, sub or f"u-{role}", [role])}


def test_latest_run_shows_counts_and_versions_but_no_evaluation_data(api):
    c, s, _ = api
    r = c.get("/runs/latest", headers=hdr(s, "viewer"))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"git_commit", "finished_at", "records", "candidate_pairs", "decisions", "clusters", "review_queue_items", "households",
                         "total_seconds", "peak_memory_mb", "stages", "configs"}
    assert body["records"] > 100 and set(body["decisions"]) == {"pairs", "auto_merge", "review", "reject"} and body["configs"]["decision"]["source"] == "file"
    assert "eval" not in r.text and "truth" not in r.text


def test_queue_is_paged_capped_and_allow_listed(api, world):
    c, s, _ = api
    h = hdr(s, "viewer")
    page = c.get("/queue?limit=1000", headers=h).json()
    assert 0 < len(page) <= 5                                                       # max_page_size is enforced by the server
    assert set(page[0]) == {"person_l", "person_r", "record_pairs", "top_probability", "mean_probability", "forced", "size_l", "size_r", "priority", "state"}
    assert page[0]["state"] == "open" and page[0]["person_l"].startswith("P")
    assert page == sorted(page, key=lambda i: -i["priority"])
    second = c.get("/queue?limit=2&offset=2", headers=h).json()
    assert [(i["person_l"], i["person_r"]) for i in second] == [(i["person_l"], i["person_r"]) for i in page[2:4]]
    assert c.get("/queue?offset=999999999", headers=h).status_code == 422 and c.get("/queue?status=bogus", headers=h).status_code == 422
    assert c.get("/queue?min_probability=2", headers=h).status_code == 422
    assert c.get("/queue/summary", headers=h).json()["queue_items_in_last_run"] >= len(page)


def test_a_person_is_masked_by_default_for_everyone(api, world):
    c, s, _ = api
    for role in ("viewer", "steward", "auditor"):
        r = c.get(f"/people/{world['pan_person']}", headers=hdr(s, role))
        assert r.status_code == 200 and world["pan"] not in r.text, role                 # the full PAN appears nowhere in the response
        v = r.json()
        assert v["unmasked"] is False and v["golden"]["pan"].startswith("*") and v["golden"]["pan"].endswith(world["pan"][-3:])
        assert v["records"] and set(v["records"][0]) == {"source_id", "source_record_id", "name_as_written", "name_clean", "dob", "gender", "mobile", "email",
                                                           "address", "city", "pincode", "pan", "aadhaar_last4", "passport_no", "dl_no", "voter_id"}
        assert set(v) == {"person_id", "golden", "record_count", "sources", "conflict_fields", "records", "unmasked"}


def test_unmasking_needs_permission_a_reason_and_is_logged_without_the_data(api, world):
    c, s, sv = api
    pid, url = world["pan_person"], f"/people/{world['pan_person']}"
    assert c.get(f"{url}?unmask=true&reason={REASON}", headers=hdr(s, "viewer")).status_code == 403          # a viewer cannot, even with a reason
    assert c.get(f"{url}?unmask=true", headers=hdr(s, "steward")).status_code == 422                             # a reason is required
    assert c.get(f"{url}?unmask=true&reason=short", headers=hdr(s, "steward")).status_code == 422
    r = c.get(f"{url}?unmask=true&reason={REASON}", headers=hdr(s, "steward", "u-steward"))
    assert r.status_code == 200 and r.json()["unmasked"] is True and r.json()["golden"]["pan"] == world["pan"]
    assert c.get(f"{url}?unmask=true&reason={REASON}", headers=hdr(s, "auditor")).json()["golden"]["pan"] == world["pan"]
    events = sv.seclog.events(limit=500)
    seen = [e for e in events if e["event"] == "pii_unmasked"]
    assert [e["actor"] for e in seen] == ["u-steward", "u-auditor"] and all(e["object_ref"] == pid for e in seen)
    assert seen[0]["detail"]["reason"] == REASON and "pan" in seen[0]["detail"]["fields"] and seen[0]["detail"]["view"] == "person"
    assert any(e["event"] == "permission_denied" and e["actor"] == "u-viewer" and e["object_ref"] == pid for e in events)
    assert world["pan"] not in json.dumps(events)                                      # the log records that a view happened, never what was seen
    assert not any(e["event"] == "pii_unmasked" and e["actor"] == "u-viewer" for e in events)                  # denied views are not "unmasked" events


def test_unmasking_is_rate_limited_to_slow_bulk_copying(api, world):
    c, s, sv = api
    h = hdr(s, "steward", "u-bulk")
    url = f"/people/{world['pan_person']}?unmask=true&reason={REASON}"
    assert [c.get(url, headers=h).status_code for _ in range(6)] == [200] * 4 + [429] * 2
    assert c.get(f"/people/{world['pan_person']}", headers=h).status_code == 200                                 # masked reads are unaffected
    assert any(e["event"] == "rate_limited" and e["detail"] == {"scope": "unmask"} for e in sv.seclog.events(limit=500))


def test_compare_explains_a_pair_and_unmasks_both_people_in_one_logged_view(api, world):
    c, s, sv = api
    left, right = world["pairs"][0]
    url = f"/people/{left}/compare/{right}"
    r = c.get(url, headers=hdr(s, "steward"))
    assert r.status_code == 200
    v = r.json()
    assert set(v) == {"persons", "record_pairs", "why_match", "why_not", "probability", "decided_by", "reason", "hard_conflicts", "existing_judgments", "unmasked"}
    assert [p["person_id"] for p in v["persons"]] == [left, right] and v["record_pairs"] >= 1 and 0 < v["probability"] <= 1
    assert all(set(x) == {"comparison", "level", "bits"} for x in v["why_match"] + v["why_not"]) and isinstance(v["hard_conflicts"], list)
    assert c.get(f"{url}?unmask=true&reason={REASON}", headers=hdr(s, "viewer")).status_code == 403
    ok = c.get(f"{url}?unmask=true&reason={REASON}", headers=hdr(s, "steward"))
    assert ok.status_code == 200 and ok.json()["unmasked"] and all(p["unmasked"] for p in ok.json()["persons"])
    assert any(e["event"] == "pii_unmasked" and e["object_ref"] == f"{left},{right}" and e["detail"]["view"] == "comparison" for e in sv.seclog.events(limit=500))
    assert c.get(f"/people/{left}/compare/{left}", headers=hdr(s, "viewer")).status_code == 422


def test_bad_or_unknown_people_never_leak_anything(api):
    c, s, _ = api
    h = hdr(s, "viewer")
    assert c.get("/people/P999999999", headers=h).status_code == 404
    assert c.get("/people/P999999999/compare/P999999998", headers=h).status_code == 404
    for bad in ("x", "P12", "P1234567890", "p000000001", "P00000000%27%20OR%201=1--", "P000000001;drop"):
        r = c.get(f"/people/{bad}", headers=h)
        assert r.status_code in (404, 422) and "OR 1=1" not in r.text and "drop" not in r.text.lower().replace("dropped", "")
    assert c.get("/people/P999999999", headers=h).json()["error"]["message"] == "Not found."


def test_a_decision_hides_the_item_from_the_queue_on_the_next_read(api, world):
    c, s, sv = api
    left, right = world["pairs"][2]
    before = [(i["person_l"], i["person_r"]) for i in c.get("/queue?limit=5", headers=hdr(s, "viewer")).json()]
    assert (left, right) in before                                                  # it is in the first page before anyone decides it
    sv.review.reject_match(left, right, "u-test", "not the same")
    after = [(i["person_l"], i["person_r"]) for i in c.get("/queue?limit=5&status=open", headers=hdr(s, "viewer")).json()]
    assert (left, right) not in after
    assert any((i["person_l"], i["person_r"]) == (left, right) for i in c.get("/queue?limit=5&status=all", headers=hdr(s, "viewer")).json())


def test_configuration_history_is_readable_with_the_config_permission(api):
    c, s, _ = api
    h = hdr(s, "rule_admin")
    versions = c.get("/config/decision", headers=h).json()
    assert [v["version"] for v in versions] == [1, 2] and versions[0]["status"] == "active" and versions[1]["status"] == "proposed"
    assert set(versions[0]) == {"name", "version", "status", "proposed_by", "proposed_at", "reason", "parent_version", "content_hash", "rollback",
                                "approved_by", "declined_by", "activated_at", "activated_by"}
    assert c.get("/config/decision/versions/2", headers=h).json()["content"]["auto_merge_at"] == 0.95
    assert c.get("/config/decision/diff?old=1&new=2", headers=h).json() == ["auto_merge_at 0.9 -> 0.95"]
    assert [r["kind"] for r in c.get("/config/decision/history", headers=h).json()] == ["propose", "activate", "propose"]
    assert c.get("/config-log/verify", headers=h).json()["ok"] is True
    assert c.get("/config/decision/versions/99", headers=h).status_code == 404
    assert c.get("/config/colours", headers=h).status_code == 422
    assert c.get("/config/decision", headers=hdr(s, "platform_admin")).status_code == 200
    assert c.get("/config/decision", headers={}).status_code == 401


def test_without_a_pipeline_run_the_endpoints_say_so(tmp_path):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp_path / "empty", database_url=f"sqlite:///{tmp_path / 'e.db'}")
    c = TestClient(create_app(s))
    for url in ("/runs/latest", "/queue", "/queue/summary", "/people/P000000001", "/people/P000000001/compare/P000000002"):
        r = c.get(url, headers=hdr(s, "auditor"))
        assert r.status_code == 404 and r.json()["error"]["message"] == "No pipeline run was found.", url
    assert SecurityLog.open(f"sqlite:///{tmp_path / 'e.db'}").verify()["ok"]
