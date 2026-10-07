"""Read endpoints of a run inside a project: allow-listed responses, masking enforced by role, the unmask access log, paging caps, 404s."""
import json

import duckdb
import pytest
from api_world import PROJECT, app_with_run, hdr, pipeline_output

REASON = "customer called to dispute a merge"


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    d = tmp_path_factory.mktemp("apiread")
    out = pipeline_output(d, seed=73, households=100)
    gold = out / "golden" / "golden_records.parquet"
    pan_person, pan = duckdb.sql(f"SELECT cluster_id, pan FROM '{gold}' WHERE pan IS NOT NULL AND record_count > 1 LIMIT 1").fetchone()
    pairs = duckdb.sql(f"SELECT cluster_l, cluster_r FROM '{out / 'review_queue.parquet'}' ORDER BY priority DESC LIMIT 3").fetchall()
    return {"out": out, "pan_person": pan_person, "pan": pan, "pairs": pairs}


@pytest.fixture
def api(output, tmp_path):
    c, s, sv, run = app_with_run(tmp_path, output["out"], max_page_size=5, unmask_limit_per_min=4)
    configs = sv.workspace.stores(PROJECT)[1]
    cfg = json.loads(open("configs/decision_default.json", encoding="utf-8").read())
    configs.solo = True
    configs.activate("decision", configs.propose("decision", cfg, "asha", "first"), "asha")
    configs.solo = False
    configs.propose("decision", {**cfg, "auto_merge_at": 0.95}, "asha", "stricter")
    return c, s, sv, run


def test_a_run_shows_its_headline_numbers_and_configuration_but_no_evaluation_data(api):
    c, s, _, run = api
    r = c.get(run, headers=hdr(s, "viewer"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded" and body["project_id"] == PROJECT
    assert body["summary"]["records"] > 100 and body["summary"]["people"] > 50 and body["summary"]["review_items"] > 0
    assert set(body["summary"]) <= {"records", "sources", "candidate_pairs", "auto_merged_pairs", "review_pairs", "people", "review_items", "households", "seconds", "records_by_source",
                                    "scoring", "judgments"}                     # how scores were learned; counts of decisions applied (no personal data)
    assert body["configs"]["decision"]["source"] == "file" and "eval" not in r.text and "truth" not in r.text


def test_queue_is_paged_capped_and_allow_listed(api):
    c, s, _, run = api
    h = hdr(s, "viewer")
    page = c.get(run + "/queue?limit=1000", headers=h).json()
    assert 0 < len(page) <= 5                                                       # max_page_size is enforced by the server
    assert set(page[0]) == {"person_l", "person_r", "record_pairs", "top_probability", "mean_probability", "forced", "size_l", "size_r", "priority", "state"}
    assert page[0]["state"] == "open" and page[0]["person_l"].startswith("P")
    assert page == sorted(page, key=lambda i: -i["priority"])
    second = c.get(run + "/queue?limit=2&offset=2", headers=h).json()
    assert [(i["person_l"], i["person_r"]) for i in second] == [(i["person_l"], i["person_r"]) for i in page[2:4]]
    assert c.get(run + "/queue?offset=999999999", headers=h).status_code == 422 and c.get(run + "/queue?status=bogus", headers=h).status_code == 422
    assert c.get(run + "/queue?min_probability=2", headers=h).status_code == 422
    assert c.get(run + "/queue/summary", headers=h).json()["queue_items_in_last_run"] >= len(page)


def test_a_person_is_masked_by_default_for_everyone(api, output):
    c, s, _, run = api
    for role in ("viewer", "steward", "auditor"):
        r = c.get(f"{run}/people/{output['pan_person']}", headers=hdr(s, role))
        assert r.status_code == 200 and output["pan"] not in r.text, role                 # the full PAN appears nowhere in the response
        v = r.json()
        assert v["unmasked"] is False and v["golden"]["pan"].startswith("*") and v["golden"]["pan"].endswith(output["pan"][-3:])
        assert set(v) == {"person_id", "golden", "record_count", "sources", "conflict_fields", "records", "unmasked"}


def test_unmasking_needs_permission_a_reason_and_is_logged_without_the_data(api, output):
    c, s, sv, run = api
    pid, url = output["pan_person"], f"{run}/people/{output['pan_person']}"
    assert c.get(f"{url}?unmask=true&reason={REASON}", headers=hdr(s, "viewer")).status_code == 403
    assert c.get(f"{url}?unmask=true", headers=hdr(s, "steward")).status_code == 422
    r = c.get(f"{url}?unmask=true&reason={REASON}", headers=hdr(s, "steward", "u-steward"))
    assert r.status_code == 200 and r.json()["unmasked"] is True and r.json()["golden"]["pan"] == output["pan"]
    seen = [e for e in sv.seclog.events(limit=500) if e["event"] == "pii_unmasked"]
    assert [e["actor"] for e in seen] == ["u-steward"] and seen[0]["object_ref"].endswith(pid) and seen[0]["object_ref"].startswith(PROJECT + "/R")
    assert seen[0]["detail"]["reason"] == REASON and output["pan"] not in json.dumps(sv.seclog.events(limit=500))


def test_unmasking_is_rate_limited_to_slow_bulk_copying(api, output):
    c, s, _, run = api
    h = hdr(s, "steward", "u-bulk")
    url = f"{run}/people/{output['pan_person']}?unmask=true&reason={REASON}"
    assert [c.get(url, headers=h).status_code for _ in range(6)] == [200] * 4 + [429] * 2
    assert c.get(f"{run}/people/{output['pan_person']}", headers=h).status_code == 200


def test_compare_explains_a_pair(api, output):
    c, s, sv, run = api
    left, right = output["pairs"][0]
    r = c.get(f"{run}/people/{left}/compare/{right}", headers=hdr(s, "steward"))
    assert r.status_code == 200
    v = r.json()
    assert [p["person_id"] for p in v["persons"]] == [left, right] and v["record_pairs"] >= 1 and 0 < v["probability"] <= 1
    assert c.get(f"{run}/people/{left}/compare/{left}", headers=hdr(s, "viewer")).status_code == 422


def test_unknown_projects_runs_and_people_never_leak_anything(api):
    c, s, _, run = api
    h = hdr(s, "viewer")
    rid = run.rsplit("/", 1)[1]
    assert c.get(f"{run}/people/P999999999", headers=h).status_code == 404
    assert c.get(f"/projects/other/runs/{rid}/queue", headers=h).status_code == 404                     # a run is reachable only through its own project
    assert c.get(f"/projects/{PROJECT}/runs/R000000000000/queue", headers=h).status_code == 404
    assert c.get("/projects/BAD_ID/runs/R000000000000/queue", headers=h).status_code == 422
    for bad in ("x", "P12", "P00000000%27%20OR%201=1--"):
        r = c.get(f"{run}/people/{bad}", headers=h)
        assert r.status_code in (404, 422) and "OR 1=1" not in r.text


def test_a_decision_hides_the_item_from_the_queue_of_the_run(api, output):
    c, s, sv, run = api
    left, right = output["pairs"][2]
    assert (left, right) in [(i["person_l"], i["person_r"]) for i in c.get(run + "/queue?limit=5", headers=hdr(s, "viewer")).json()]
    assert c.post(run + "/decisions/reject", json={"person_l": left, "person_r": right, "reason": "not the same"}, headers=hdr(s, "steward")).status_code == 200
    assert (left, right) not in [(i["person_l"], i["person_r"]) for i in c.get(run + "/queue?limit=5", headers=hdr(s, "viewer")).json()]


def test_the_project_rules_and_their_history_are_readable(api):
    c, s, _, _ = api
    h, base = hdr(s, "rule_admin"), f"/projects/{PROJECT}"
    versions = c.get(base + "/config/decision", headers=h).json()
    assert [v["version"] for v in versions] == [1, 2] and versions[0]["status"] == "active" and versions[1]["status"] == "proposed"
    assert c.get(base + "/config/decision/versions/2", headers=h).json()["content"]["auto_merge_at"] == 0.95
    assert c.get(base + "/config/decision/diff?old=1&new=2", headers=h).json() == ["auto_merge_at 0.9 -> 0.95"]
    assert c.get(base + "/config-log/verify", headers=h).json()["ok"] is True
    assert c.get(base + "/config/colours", headers=h).status_code == 422 and c.get(base + "/config/sources", headers=h).status_code == 422
    assert c.get("/projects/nope/config/decision", headers=h).status_code == 404
    assert c.get(f"/projects/{PROJECT}", headers=h).json()["live_rules"] == {"decision": 1, "survivorship": None, "settings": None}


def test_a_run_that_is_not_finished_or_failed_has_no_results(api, output, tmp_path):
    c, s, sv, _ = api
    from dedupe.workspace.runs import runs as table
    with sv.jobs.engine.begin() as conn:
        conn.execute(table.insert().values(id="R00000000000a", project_id=PROJECT, created_at="2026-01-01", requested_by="x", status="failed", reason="",
                                           connections="[]", summary="{}"))
    r = c.get(f"/projects/{PROJECT}/runs/R00000000000a/queue", headers=hdr(s, "viewer"))
    assert r.status_code == 409 and "did not finish" in r.json()["error"]["message"]
