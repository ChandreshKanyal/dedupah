"""The questions of a new team member, answered in the product: one starter rule instead of three, the people split into matched and
unmatched (each matched one with the records and links behind it), review pairs given to one reviewer at a time, no silent overwriting of
another reviewer's decision, email notices (off unless the server sends email), and what the reviewers' decisions teach about the rules."""
import smtplib

import duckdb
import pytest
from api_world import PROJECT, app_with_run, hdr, pipeline_output

from dedupe.api.accounts import AccountError, clean_email
from dedupe.api.notify import Notifier
from dedupe.api.routes_read import _default_content
from dedupe.review.lessons import Decided, lessons

P = f"/projects/{PROJECT}"
WHY = "checked both customer files side by side"


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    d = tmp_path_factory.mktemp("questions")
    out = pipeline_output(d, seed=81, households=100)
    pairs = duckdb.sql(f"SELECT cluster_l, cluster_r FROM '{out / 'review_queue.parquet'}' ORDER BY priority DESC LIMIT 6").fetchall()
    return {"out": out, "pairs": pairs}


@pytest.fixture
def api(output, tmp_path):
    c, s, sv, run = app_with_run(tmp_path, output["out"])
    # a project with members: two stewards and a lead (decides and approves)
    for sub, role in (("u-asha", "steward"), ("u-ravi", "steward"), ("u-lead", "analyst")):
        sv.workspace.grant(PROJECT, sub, role, "setup")
    return c, s, sv, run


def test_the_starter_rules_say_name_and_birth_date_once():
    rules = [r for r in _default_content("decision")["rules"] if r["id"].startswith("match_name_dob")]
    assert [r["id"] for r in rules] == ["match_name_dob_contact"]
    name, dob, contact = rules[0]["when"]["all"]
    assert name["comparison"] == "name" and dob["comparison"] == "dob"
    assert [c["comparison"] for c in contact["any"]] == ["phone", "email", "address"]


def test_a_run_says_how_many_people_matched_nothing_and_lists_either_kind(api):
    c, s, sv, run = api
    h = hdr(s, "analyst", "u-lead")
    summary = c.get(run, headers=h).json()["summary"]
    assert summary["single_people"] + summary["merged_people"] == summary["people"] and summary["merged_people"] > 0
    rows = lambda built: c.get(run + f"/datasets/golden_records/rows?limit=100&built={built}", headers=h).json()   # noqa: E731
    several, single = rows("several"), rows("single")
    at = several["columns"].index("record_count")
    assert several["total"] == summary["merged_people"] and single["total"] == summary["single_people"]
    assert all(r[at] > 1 for r in several["rows"]) and all(r[single["columns"].index("record_count")] == 1 for r in single["rows"])
    assert c.get(run + "/datasets/golden_records/rows?built=many", headers=h).status_code == 422


def test_a_person_says_which_rule_or_score_joined_each_record(api, output):
    c, s, sv, run = api
    h = hdr(s, "analyst", "u-lead")
    person = duckdb.sql(f"SELECT cluster_id FROM '{output['out'] / 'golden' / 'golden_records.parquet'}' WHERE record_count > 2 LIMIT 1").fetchone()[0]
    records = {(r["source_id"], r["source_record_id"]) for r in c.get(run + f"/people/{person}", headers=h).json()["records"]}
    lk = c.get(run + f"/people/{person}/links", headers=h).json()
    assert lk["records"] == len(records) and len(lk["links"]) >= len(records) - 1           # enough links to join them all
    for x in lk["links"]:
        assert {tuple(x["a"]), tuple(x["b"])} <= records and x["how"] in ("rule", "score")
        assert (x["rule"] is None) == (x["how"] == "score") and x["rule"] not in x["also"]
    assert sum(lk["by"].values()) == len(lk["links"])
    assert c.get(run + "/people/P999999999/links", headers=h).status_code == 404


def test_a_pair_given_to_one_reviewer_is_theirs(api, output):
    c, s, sv, run = api
    (l1, r1), (l2, r2) = output["pairs"][:2]
    asha, ravi, lead = hdr(s, "steward", "u-asha"), hdr(s, "steward", "u-ravi"), hdr(s, ["steward", "approver"], "u-lead")
    names = {r["username"]: r for r in c.get(P + "/reviewers", headers=asha).json()}
    assert {"u-asha", "u-ravi", "u-lead"} <= set(names) and names["u-lead"]["lead"] and not names["u-asha"]["lead"]
    # a steward cannot hand pairs to someone else, but may take a free one
    assert c.post(P + "/review/assign", json={"to": "u-ravi", "items": [{"person_l": l1, "person_r": r1}]}, headers=asha).status_code == 403
    assert c.post(P + "/review/assign", json={"to": "u-asha", "items": [{"person_l": l1, "person_r": r1}]}, headers=asha).json()["pairs"] == 1
    # the lead gives the second pair to Ravi; Asha cannot take it from him
    assert c.post(P + "/review/assign", json={"to": "u-ravi", "items": [{"person_l": r2, "person_r": l2}]}, headers=lead).status_code == 200
    assert c.post(P + "/review/assign", json={"to": "u-asha", "items": [{"person_l": l2, "person_r": r2}]}, headers=asha).status_code == 409
    assert c.post(P + "/review/assign", json={"to": "nobody-here", "items": [{"person_l": l2, "person_r": r2}]}, headers=lead).status_code == 422
    mine = c.get(run + "/queue?assigned=me&limit=50", headers=asha).json()
    assert [(i["person_l"], i["person_r"]) for i in mine] == [(l1, r1)] and mine[0]["assigned_to"] == "u-asha"
    free = c.get(run + "/queue?assigned=free&limit=50", headers=asha).json()
    assert {(l1, r1), (l2, r2)}.isdisjoint({(i["person_l"], i["person_r"]) for i in free})
    assert [(i["person_l"], i["person_r"]) for i in c.get(run + "/queue?assigned=others&limit=50", headers=asha).json()] == [(l2, r2)]
    assert [(i["person_l"], i["person_r"]) for i in c.get(run + "/queue?assigned=me&limit=50", headers=ravi).json()] == [(l2, r2)]
    inbox = {x["project_id"]: x for x in c.get("/review/inbox", headers=asha).json()}
    assert inbox[PROJECT]["assigned_to_me"] == 1
    # Ravi's pair: Asha cannot decide it, the lead can
    refused = c.post(run + "/decisions/reject", json={"person_l": l2, "person_r": r2}, headers=asha)
    assert refused.status_code == 409 and "u-ravi" in refused.json()["error"]["message"]
    bulk = c.post(run + "/decisions/bulk", json={"decision": "reject", "items": [{"person_l": l2, "person_r": r2}]}, headers=asha).json()
    assert bulk["done"] == [] and "u-ravi" in bulk["failed"][0]["error"]
    assert c.post(run + "/decisions/reject", json={"person_l": l2, "person_r": r2}, headers=lead).status_code == 200
    # giving back
    assert c.post(P + "/review/assign", json={"to": None, "items": [{"person_l": l1, "person_r": r1}]}, headers=asha).json()["pairs"] == 1
    assert c.get(run + "/queue?assigned=me&limit=50", headers=asha).json() == []


def test_another_reviewers_decision_is_never_replaced_silently(api, output):
    c, s, sv, run = api
    l, r = output["pairs"][2]
    asha, ravi, lead = hdr(s, "steward", "u-asha"), hdr(s, "steward", "u-ravi"), hdr(s, ["steward", "approver"], "u-lead")
    assert c.post(run + "/decisions/reject", json={"person_l": l, "person_r": r, "reason": WHY}, headers=asha).status_code == 200
    again = c.post(run + "/decisions/approve", json={"person_l": l, "person_r": r, "reason": WHY, "confirm_id_conflict": True}, headers=ravi)
    assert again.status_code == 409 and "u-asha already decided this pair" in again.json()["error"]["message"]
    assert c.post(run + "/decisions/approve", json={"person_l": l, "person_r": r, "reason": WHY, "replace": True}, headers=ravi).status_code == 403
    store = sv.workspace.stores(PROJECT)[0]
    assert {j.actor for j in store.active(("same_person", "different_person"))} == {"u-asha"}
    # the same reviewer may change their own mind; a lead may replace it on purpose (both stay in the history)
    assert c.post(run + "/decisions/reject", json={"person_l": l, "person_r": r, "reason": WHY}, headers=ravi).status_code == 200      # same answer: fine
    done = c.post(run + "/decisions/approve", json={"person_l": l, "person_r": r, "reason": WHY, "confirm_id_conflict": True, "replace": True}, headers=lead)
    assert done.status_code == 200
    if not done.json()["pending_approval"]:
        assert {j.actor for j in store.active(("same_person",))} == {"u-lead"}


def test_email_notices_are_off_without_a_mail_server_and_hold_no_personal_data(api, output, monkeypatch):
    c, s, sv, run = api
    assert not sv.notifier.enabled and sv.notifier.send(["a@b.co"], "x", "y") is None
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            sent.append(("tls",))

        def login(self, user, password):
            sent.append(("login", user, password))

        def send_message(self, m):
            sent.append(("to", m["To"], m["Subject"], m.get_content()))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("DEDUPE_SMTP_PASSWORD", "from-the-environment")
    n = Notifier(host="mail.example", port=587, sender="dedupe@example.com", user="dedupe", portal_url="https://dedupe.example")
    n.send(["b@example.com", "a@example.com", "a@example.com"], "Review: 3 pair(s)", "Open " + n.link("/review/x"), wait=True)
    assert sent[0] == ("connect", "mail.example", 587) and sent[1] == ("tls",) and sent[2] == ("login", "dedupe", "from-the-environment")
    assert [x[1] for x in sent if x[0] == "to"] == ["a@example.com", "b@example.com"] and "https://dedupe.example/review/x" in sent[-1][3]
    # a run with pairs for people tells the reviewers who have an address (none here: dev sign-in has no accounts), never breaking the run
    from dedupe.api.routes_review import notify_run_finished
    sv.notifier = n
    notify_run_finished(sv, sv.jobs.latest_succeeded(PROJECT))
    assert clean_email(" Asha@Example.COM ") == "asha@example.com" and clean_email("") is None
    for bad in ("asha", "a@b", "a@b.co\nBcc: x@y.z", "a@b.co, c@d.co"):
        with pytest.raises(AccountError):
            clean_email(bad)


def test_reviewers_decisions_become_suggestions_not_silent_changes():
    content = {"auto_merge_at": 0.9, "review_at": 0.1, "use_score": True, "rules": [{"id": "ask_same_phone", "type": "force_review"},
                                                                                      {"id": "never_x", "type": "never_merge"}]}
    decided = ([Decided("same_person", "ask_same_phone", 0.95)] * 24 + [Decided("different_person", "ask_same_phone", 0.95)]
               + [Decided("same_person", "score", 0.85)] * 30 + [Decided("different_person", "score", 0.2)] * 25 + [Decided("same_person", "score", 0.6)] * 3)
    got = lessons(decided, content)
    kinds = {t["kind"]: t for t in got["suggestions"]}
    assert got["decisions"] == 83 and kinds["rule_could_merge"]["rule"] == "ask_same_phone"
    assert kinds["lower_auto_merge"]["value"] == 0.8 and kinds["raise_review"]["value"] == 0.3
    assert {b["from"] for b in got["by_band"]} == {0.8, 0.0, 0.5}
    # too few decisions, or answers that disagree, suggest nothing
    assert lessons(decided[:10], content)["suggestions"] == []
    mixed = [Decided("same_person", "ask_same_phone", 0.9)] * 12 + [Decided("different_person", "ask_same_phone", 0.9)] * 12
    assert lessons(mixed, content)["suggestions"] == []


def test_the_lessons_endpoint_counts_the_projects_decisions(api, output):
    c, s, sv, run = api
    l, r = output["pairs"][3]
    assert c.get(P + "/review/lessons", headers=hdr(s, "viewer", "u-lead")).json()["decisions"] == 0
    c.post(run + "/decisions/reject", json={"person_l": l, "person_r": r, "reason": WHY}, headers=hdr(s, "steward", "u-asha"))
    got = c.get(P + "/review/lessons", headers=hdr(s, "viewer", "u-lead")).json()
    assert got["decisions"] >= 1 and sum(x["different"] for x in got["by_rule"]) == got["decisions"]


def test_local_users_have_an_optional_email_and_reviewers_with_one_get_the_run_notice(tmp_path, output, monkeypatch):
    from fastapi.testclient import TestClient

    from dedupe.api import ApiSettings, accounts, create_app
    from dedupe.api.routes_review import notify_run_finished
    monkeypatch.setattr(accounts, "SCRYPT_N", 2**10)
    pw = "correct horse battery"
    s = ApiSettings(env="dev", auth_mode="local", local_secret="l" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}",
                    ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6)
    app = create_app(s)
    sv = app.state.services
    sv.accounts.create_user("admin", "The Admin", pw, ["admin"], "test", must_change=False)
    c = TestClient(app)
    h = {"Authorization": "Bearer " + c.post("/auth/login", json={"username": "admin", "password": pw}).json()["token"]}
    made = c.post("/users", json={"username": "asha", "display_name": "Asha", "password": pw, "roles": ["steward"], "email": "Asha@Example.com"}, headers=h)
    assert made.status_code == 201 and made.json()["email"] == "asha@example.com"
    assert c.post("/users", json={"username": "ravi", "password": pw, "roles": ["steward"], "email": "not an address"}, headers=h).status_code == 422
    c.post("/users", json={"username": "ravi", "password": pw, "roles": ["viewer"]}, headers=h)
    assert c.patch("/users/ravi", json={"email": "ravi@example.com"}, headers=h).json()["email"] == "ravi@example.com"
    assert c.patch("/users/ravi", json={"email": "x\ny@z.com"}, headers=h).status_code == 422
    sv.workspace.create(PROJECT, "Demo project", "", "admin")
    sv.jobs.import_results(PROJECT, output["out"], "admin")
    who = {r["username"]: r for r in c.get(P + "/reviewers", headers=h).json()}
    assert who["asha"]["has_email"] and "ravi" not in who and "email" not in who["asha"]          # a viewer does not decide; addresses stay private
    sent = []
    sv.notifier = type("N", (), {"enabled": True, "link": lambda self, path: "https://portal" + path,
                                 "send": lambda self, to, subject, body, wait=False: sent.append((sorted(to), subject, body))})()
    notify_run_finished(sv, sv.jobs.latest_succeeded(PROJECT))
    assert sent and sent[0][0] == ["asha@example.com"] and "https://portal/review/" + PROJECT in sent[0][2]
