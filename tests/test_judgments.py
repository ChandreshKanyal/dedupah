import itertools

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from dedupe.store import JudgmentError, JudgmentStore

A, B, C, D = ("bank_crm", "CIF1"), ("insurance", "POL9"), ("loans", "LN5"), ("leads", "LD2")


@pytest.fixture
def store(tmp_path):
    ticks = itertools.count(1)
    return JudgmentStore.open(f"sqlite:///{tmp_path / 'j.db'}", clock=lambda: f"2026-10-05T10:00:{next(ticks):02d}.000000Z")


def test_pair_order_does_not_matter_and_repeats_are_idempotent(store):
    first = store.same_person(A, B, "asha", "same PAN and DOB")
    assert store.same_person(B, A, "ravi") == first                      # same pair, other order, other person: nothing new is written
    assert len(store.history()) == 1 and store.pair_state(B, A) == "same_person"


def test_changing_a_mind_supersedes_the_earlier_decision_and_keeps_the_history(store):
    j1 = store.same_person(A, B, "asha", "looked identical")
    j2 = store.different_person(A, B, "ravi", "different PAN on closer look")
    assert store.pair_state(A, B) == "different_person"
    assert [j.id for j in store.active()] == [j2]
    status = {j.id: j.status for j in store.history(("same_person", "different_person"))}
    assert status == {j1: "revoked", j2: "active"}                       # nothing deleted: the audit trail still shows both
    assert [h.kind for h in store.history()] == ["same_person", "revoke", "different_person"]


def test_revoke_undoes_a_decision_and_unsure_snoozes_without_deciding(store):
    j = store.different_person(A, C, "asha", "namesakes")
    store.revoke(j, "asha", "mis-click")
    assert store.pair_state(A, C) is None
    with pytest.raises(JudgmentError):
        store.revoke(j, "asha")                                           # already revoked
    store.unsure(A, C, "asha", "need the KYC document")
    assert store.pair_state(A, C) == "unsure"
    store.same_person(A, C, "ravi")                                       # a real decision replaces 'unsure'
    assert store.pair_state(A, C) == "same_person" and not [x for x in store.active() if x.kind == "unsure"]
    with pytest.raises(JudgmentError):
        store.unsure(A, C, "asha")                                        # 'unsure' may not overwrite a decision in force


def test_four_eyes_decision_has_no_effect_until_a_different_person_approves(store):
    j = store.same_person(A, B, "asha", "high-risk merge", requires_approval=True)
    assert store.pair_state(A, B) is None and [x.id for x in store.pending_approval()] == [j] and store.active() == []
    with pytest.raises(JudgmentError):
        store.approve(j, "asha")                                          # the proposer cannot approve their own decision
    store.approve(j, "ravi", "agreed")
    assert store.pair_state(A, B) == "same_person" and store.pending_approval() == []
    with pytest.raises(JudgmentError):
        store.approve(j, "meena")                                         # no longer waiting


def test_declined_decision_never_takes_effect(store):
    j = store.different_person(A, B, "asha", "x", requires_approval=True)
    with pytest.raises(JudgmentError):
        store.decline(j, "ravi", "")                                      # a reason is required
    store.decline(j, "ravi", "evidence says same person")
    assert store.pair_state(A, B) is None and store.history(("different_person",))[0].status == "declined"


def test_split_and_field_override_are_validated_and_stored_canonically(store):
    j = store.split([[B, A], [C]], "asha", "two people merged by a shared phone")
    assert store.active(("split",))[0].subject == {"parts": [[list(A), list(B)], [list(C)]]}   # parts and records sorted
    for bad in ([[A]], [[A], [A]], [[A, B], [B]], [[A], []]):
        with pytest.raises(JudgmentError):
            store.split(bad, "asha", "x")
    with pytest.raises(JudgmentError):
        store.split([[A], [B]], "asha", "")
    o = store.override_field("email", "pooja@example.com", [A, B], "asha", "customer confirmed by phone")
    assert store.active(("field_override",))[0].subject == {"anchors": [list(A), list(B)], "field": "email", "value": "pooja@example.com"}
    store.override_field("email", None, [A], "ravi", "customer asked to remove it")      # null clears the field
    with pytest.raises(JudgmentError):
        store.override_field("shoe_size", "9", [A], "asha", "x")
    with pytest.raises(JudgmentError):
        store.override_field("email", "x@y.in", [], "asha", "no anchor")
    with pytest.raises(JudgmentError):
        store.override_field("email", "x@y.in", [A], "asha", "")
    assert j and o


def test_input_validation(store):
    with pytest.raises(JudgmentError):
        store.same_person(A, A, "asha")                                   # two different records are required
    with pytest.raises(JudgmentError):
        store.same_person(A, ("insurance", ""), "asha")
    with pytest.raises(JudgmentError):
        store.same_person(A, B, "")
    with pytest.raises(JudgmentError):
        store.revoke(999, "asha")


def test_evidence_snapshot_and_reason_are_kept_verbatim(store):
    ev = {"probability": 0.42, "contributions": {"name": 2.1, "dob": 5.0}, "model": "fs_model.json", "persons": ["P000000001", "P000000002"]}
    j = store.same_person(A, B, "asha", "reviewed side by side", evidence=ev)
    got = store.history()[0]
    assert got.id == j and got.evidence == ev and got.reason == "reviewed side by side" and got.actor == "asha"
    assert got.created_at.startswith("2026-10-05T10:00:")


def test_database_refuses_updates_and_deletes(store):
    store.same_person(A, B, "asha")
    for sql in ("UPDATE judgments SET actor = 'mallory'", "DELETE FROM judgments"):
        with pytest.raises(DBAPIError), store.engine.begin() as c:
            c.execute(text(sql))
    assert store.verify()["ok"] and len(store.history()) == 1


def test_hash_chain_detects_tampering_made_behind_the_applications_back(store):
    for pair in ((A, B), (A, C), (B, D)):
        store.same_person(*pair, "asha")
    assert store.verify() == {"ok": True, "rows": 3, "first_bad_id": None}
    with store.engine.begin() as c:                                       # an administrator drops the trigger and edits a row
        c.execute(text("DROP TRIGGER judgments_no_update"))
        c.execute(text("UPDATE judgments SET actor = 'mallory' WHERE id = 2"))
    assert store.verify() == {"ok": False, "rows": 1, "first_bad_id": 2}


def test_decisions_survive_closing_and_reopening_the_store(tmp_path):
    url = f"sqlite:///{tmp_path / 'p.db'}"
    s1 = JudgmentStore.open(url)
    s1.different_person(A, B, "asha", "twins")
    s1.engine.dispose()
    s2 = JudgmentStore.open(url)
    assert s2.pair_state(A, B) == "different_person" and s2.verify()["rows"] == 1
    s2.same_person(A, C, "ravi")
    assert s2.verify() == {"ok": True, "rows": 2, "first_bad_id": None}
