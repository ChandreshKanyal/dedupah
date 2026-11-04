"""Platform review, last round: stewards' decisions (P1-10), personal data in logs (P1-11), and where network sources may point (P1-12)."""
import pytest
from api_world import app_with_run, hdr, pipeline_output

from dedupe.api.seclog import scrub
from dedupe.connectors.guard import ConnectorConfigError, check_host
from dedupe.store import JudgmentStore

A, B = ("crm", "1"), ("loans", "7")


@pytest.fixture
def store(tmp_path):
    return JudgmentStore.open(f"sqlite:///{tmp_path / 'j.db'}")


def test_a_change_waiting_for_a_second_person_leaves_the_decision_in_force_until_it_is_approved(store):
    store.different_person(A, B, "steward-2", "different fathers")
    waiting = store.same_person(A, B, "steward-1", "same person, PAN typo", requires_approval=True)
    assert store.pair_state(A, B) == "different_person"                       # still in force while it waits
    store.decline(waiting, "approver-1", "the fathers differ")
    assert store.pair_state(A, B) == "different_person"                       # a declined change erases nothing
    waiting = store.same_person(A, B, "steward-1", "new evidence: same mobile", requires_approval=True)
    store.approve(waiting, "approver-1")
    assert store.pair_state(A, B) == "same_person" and [j.status for j in store.history(("different_person",))] == ["revoked"]


def test_one_steward_cannot_overturn_a_decision_two_people_made(store, tmp_path):
    first = store.same_person(A, B, "steward-1", "same person", requires_approval=True)
    store.approve(first, "approver-1")
    second = store.different_person(A, B, "steward-2", "I disagree", protect_approved=True)
    assert store.status(second) == "pending_approval" and store.pair_state(A, B) == "same_person"
    store.approve(second, "approver-2")
    assert store.pair_state(A, B) == "different_person"
    lone = JudgmentStore.open(f"sqlite:///{tmp_path / 'solo.db'}")              # a one-person project does not protect (nobody to ask)
    store_one = lone.same_person(A, B, "solo", "x")
    assert lone.status(lone.different_person(A, B, "solo", "y", protect_approved=True)) == "active" and lone.status(store_one) == "revoked"


def test_only_the_steward_who_decided_or_an_approver_undoes_a_decision(tmp_path):
    c, s, sv, run = app_with_run(tmp_path, pipeline_output(tmp_path / "out", seed=97, households=20), ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6)
    s1, s2, lead = hdr(s, "steward", "steward-1"), hdr(s, "steward", "steward-2"), hdr(s, "analyst", "lead")    # analyst: decides and approves
    item = c.get(run + "/queue?limit=1&status=all", headers=s1).json()[0]
    pair = {"person_l": item["person_l"], "person_r": item["person_r"], "reason": "different fathers"}
    action = c.post(run + "/decisions/reject", json=pair, headers=s1).json()["action_id"]
    project = run.split("/runs/")[0]
    r = c.post(f"{project}/decisions/{action}/undo", json={"reason": "I disagree"}, headers=s2)
    assert r.status_code == 409 and "approver" in r.text
    assert c.post(f"{project}/decisions/{action}/undo", json={"reason": "checked with the branch"}, headers=lead).status_code == 200
    again = c.post(run + "/decisions/reject", json=pair, headers=s1).json()["action_id"]
    assert c.post(f"{project}/decisions/{again}/undo", json={}, headers=s1).status_code == 200


@pytest.mark.parametrize("text", ["mobile 98765 43210", "mobile +91 98765-43210", "mobile 098765 43210", "Aadhaar 1234-5678-9012", "Aadhaar 1234.5678.9012",
                                  "passport K1234567", "voter id ABC1234567", "DL MH12 20110012345", "PAN ABCDE1234F", "mail a.b@x.co.in"])
def test_the_log_scrubber_removes_what_people_type_in_any_common_form(text):
    assert scrub(text).endswith("[redacted]"), scrub(text)


def test_the_log_scrubber_leaves_our_own_ids_whole():
    for text in ("upload U9876543210ab", "upload Uabcde1234fab", "person P000000123", "run R48d4d0446ed2", "schedule S6a0647abe964", "rows 12345"):
        assert scrub(text) == text


@pytest.mark.parametrize("host", ["169.254.169.254", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1", "168.63.129.16", "192.0.0.192",
                                  "0.0.0.0", "::", "metadata.google.internal"])
def test_metadata_and_any_addresses_are_never_a_source(host, monkeypatch):
    monkeypatch.delenv("DEDUPE_SOURCE_HOSTS", raising=False)
    with pytest.raises(ConnectorConfigError):
        check_host(host)


def test_in_production_the_server_itself_is_not_a_source_but_private_networks_are(monkeypatch):
    monkeypatch.delenv("DEDUPE_SOURCE_HOSTS", raising=False)
    assert check_host("127.0.0.1") and check_host("localhost")                # on a laptop a local database is normal
    monkeypatch.setenv("DEDUPE_ENV", "production")
    for host in ("127.0.0.1", "::1", "localhost"):
        with pytest.raises(ConnectorConfigError):
            check_host(host)
    assert check_host("10.0.0.5") and check_host("192.168.1.10") and check_host("172.17.0.1")
