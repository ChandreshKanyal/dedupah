import json
import os
import shutil

import duckdb
import pytest

from dedupe.pipeline import PipelineConfig, run_pipeline
from dedupe.review import ReviewError, ReviewPolicy, ReviewService
from dedupe.store import JudgmentStore
from dedupe.synthetic import generate as gen_data


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    d = tmp_path_factory.mktemp("rv")
    syn = d / "syn"
    gen_data(120, seed=91, out=syn)
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(syn / os.path.basename(s["connector"]["path"]))
    (d / "sources.json").write_text(json.dumps(cfg), encoding="utf-8")
    url = f"sqlite:///{d / 'base.db'}"
    run_pipeline(PipelineConfig(d / "sources.json", d / "work", judgments_url=url))
    return d


class Env:
    """A private copy of the baseline run (own work folder, own judgment store) so tests cannot disturb each other."""

    def __init__(self, base, tmp_path, policy=None):
        self.sources, self.work = base / "sources.json", tmp_path / "work"
        shutil.copytree(base / "work", self.work)
        self.url = f"sqlite:///{tmp_path / 'j.db'}"
        self.store = JudgmentStore.open(self.url)
        self.svc = ReviewService(self.work, self.store, policy)

    def rerun(self):
        return run_pipeline(PipelineConfig(self.sources, self.work, judgments_url=self.url))

    def person_of(self, key):
        return duckdb.sql(f"select cluster_id from '{self.work / 'clusters' / 'clusters.parquet'}' where source_id = '{key[0]}' and source_record_id = '{key[1]}'").fetchone()[0]

    def members(self, person):
        return duckdb.sql(f"select source_id, source_record_id from '{self.work / 'clusters' / 'clusters.parquet'}' where cluster_id = '{person}' order by 1, 2").fetchall()

    def golden(self, person, field):
        return duckdb.sql(f"select value, source_id, strategy from '{self.work / 'golden' / 'golden_values.parquet'}' where cluster_id = '{person}' and field = '{field}'").fetchone()


@pytest.fixture
def env(baseline, tmp_path):
    return Env(baseline, tmp_path)


def _item_keys(item):
    return item["cluster_l"], item["cluster_r"]


def test_queue_is_ranked_paged_and_filterable(env):
    items = env.svc.queue(limit=1000)
    assert len(items) >= 5
    pri = [i["priority"] for i in items]
    assert pri == sorted(pri, reverse=True) and all(i["state"] == "open" for i in items)
    assert [_item_keys(i) for i in env.svc.queue(limit=3, offset=2)] == [_item_keys(i) for i in items[2:5]]
    hi = env.svc.queue(limit=1000, min_probability=0.5)
    assert 0 < len(hi) < len(items) and all(i["top_probability"] >= 0.5 for i in hi)
    with pytest.raises(ReviewError):
        env.svc.queue(status="whatever")


def test_explain_shows_both_people_the_evidence_and_masks_sensitive_values(env):
    item = env.svc.queue(limit=1)[0]
    e = env.svc.explain(*_item_keys(item))
    assert [p["person_id"] for p in e["persons"]] == list(_item_keys(item)) and e["record_pairs"] >= 1
    assert 0 < e["probability"] <= 1 and isinstance(e["hard_conflicts"], list) and e["best_pair"]
    assert all(c["bits"] > 0.5 for c in e["why_match"]) and all(c["bits"] < -0.5 for c in e["why_not"]) and (e["why_match"] or e["why_not"])
    shown = [r["pan"] for p in e["persons"] for r in p["records"] if r["pan"]]
    assert all(v.startswith("*") for v in shown)                         # masked by default
    assert all(v.startswith("*") for p in e["persons"] for v in [p["golden"].get("pan")] if v)
    full = ReviewService(env.work, env.store, ReviewPolicy(reveal_sensitive=True)).explain(*_item_keys(item), reveal=True)
    assert any(r["pan"] and not r["pan"].startswith("*") for p in full["persons"] for r in p["records"]) or not shown
    still_masked = env.svc.explain(*_item_keys(item), reveal=True)       # the caller asking is not enough: the policy must allow it
    assert all(r["pan"].startswith("*") for p in still_masked["persons"] for r in p["records"] if r["pan"])


def test_approve_reject_split_and_override_take_effect_on_the_next_run(env):
    items = env.svc.queue(limit=1000)
    a_item, r_item = items[0], items[1]
    before = {p: env.members(p) for p in (*_item_keys(a_item), *_item_keys(r_item))}
    approved = env.svc.approve_match(*_item_keys(a_item), actor="asha", reason="same PAN seen on the document")
    rejected = env.svc.reject_match(*_item_keys(r_item), actor="asha", reason="different DOB and city")
    assert not approved["pending_approval"] and len(rejected["judgment_ids"]) >= 1
    j = env.store.history(("same_person",))[0]
    assert j.evidence["action_id"] == approved["action_id"] and j.evidence["persons"] == list(_item_keys(a_item)) and j.evidence["top_evidence"]
    # decided items leave the queue immediately, before any rerun
    open_now = {_item_keys(i) for i in env.svc.queue(limit=1000)}
    assert _item_keys(a_item) not in open_now and _item_keys(r_item) not in open_now
    # a person with 3+ records is split, and a golden field is overridden
    big = next(p for p in duckdb.sql(f"select cluster_id from '{env.work / 'clusters' / 'clusters.parquet'}' group by 1 having count(*) >= 3").fetchall())[0]
    keys = env.members(big)
    split = env.svc.split_person(big, [[keys[0]], keys[1:]], "asha", "two different people share these records")
    other = next(p for p in duckdb.sql(f"select cluster_id from '{env.work / 'clusters' / 'clusters.parquet'}' group by 1 having count(*) >= 2").fetchall())[0]
    if other == big:
        other = _item_keys(a_item)[0]
    ov = env.svc.override_field(other, "occupation", "Retired", "ravi", "customer told us at the branch")
    assert split["judgment_ids"] and ov["judgment_ids"]
    env.rerun()
    # approved: both people are now one person; rejected: still two people; split: the first record left; override: golden value fixed
    assert env.person_of(before[_item_keys(a_item)[0]][0]) == env.person_of(before[_item_keys(a_item)[1]][0])
    assert env.person_of(before[_item_keys(r_item)[0]][0]) != env.person_of(before[_item_keys(r_item)[1]][0])
    assert env.person_of(keys[0]) != env.person_of(keys[1])
    assert env.golden(env.person_of(env.members(other)[0]), "occupation") == ("Retired", "steward", "steward_override")
    assert _item_keys(r_item) not in {_item_keys(i) for i in env.svc.queue(limit=10**5)}     # a rejected pair does not come back


def test_undo_restores_the_queue_item_after_the_next_run(env):
    item = env.svc.queue(limit=1)[0]
    act = env.svc.reject_match(*_item_keys(item), actor="asha", reason="looked different")
    assert _item_keys(item) not in {_item_keys(i) for i in env.svc.queue(limit=10**5)}
    env.svc.undo_action(act["action_id"], "asha", "mis-click")
    assert _item_keys(item) in {_item_keys(i) for i in env.svc.queue(limit=10**5)}               # back immediately
    env.rerun()
    assert _item_keys(item) in {_item_keys(i) for i in env.svc.queue(limit=10**5)}               # and after the pipeline run
    with pytest.raises(ReviewError):
        env.svc.undo_action(act["action_id"], "asha")                                           # nothing left to undo


def test_unsure_snoozes_an_item_and_it_can_be_listed_separately(env):
    item = env.svc.queue(limit=1)[0]
    env.svc.mark_unsure(*_item_keys(item), actor="asha", reason="need the KYC document")
    assert _item_keys(item) not in {_item_keys(i) for i in env.svc.queue(limit=10**5)}
    snoozed = env.svc.queue(status="unsure")
    assert [_item_keys(i) for i in snoozed] == [_item_keys(item)] and snoozed[0]["state"] == "unsure"
    assert _item_keys(item) in {_item_keys(i) for i in env.svc.queue(status="all", limit=10**5)}


def test_four_eyes_policy_holds_decisions_until_a_second_person_approves(baseline, tmp_path):
    env = Env(baseline, tmp_path, ReviewPolicy(four_eyes_merge_min_records=2, four_eyes_split=True))
    item = env.svc.queue(limit=1)[0]
    rec_l, rec_r = env.members(item["cluster_l"])[0], env.members(item["cluster_r"])[0]
    r = env.svc.approve_match(*_item_keys(item), actor="asha", reason="same person")
    assert r["pending_approval"] and [p["id"] for p in env.svc.pending()] == r["judgment_ids"]
    with pytest.raises(Exception, match="four eyes"):
        env.svc.approve_pending(r["judgment_ids"][0], "asha")                      # proposer cannot approve
    env.rerun()
    assert env.person_of(rec_l) != env.person_of(rec_r)                            # still pending: no effect
    env.svc.approve_pending(r["judgment_ids"][0], "ravi", "agreed")
    env.rerun()
    assert env.person_of(rec_l) == env.person_of(rec_r)                            # approved: merged
    # overrides of identifier fields wait for a second approver by default
    person = env.person_of(duckdb.sql(f"select source_id, source_record_id from '{env.work / 'clusters' / 'clusters.parquet'}' limit 1").fetchone())
    o = env.svc.override_field(person, "dob", "1990-01-01", "asha", "customer showed passport")
    assert o["pending_approval"] and env.store.active(("field_override",)) == []


def test_identifier_conflict_needs_explicit_confirmation_and_a_second_approver(env, monkeypatch):
    item = env.svc.queue(limit=1)[0]
    monkeypatch.setattr(env.svc, "_hard_conflicts", lambda con, a, b: ["different_pan"])
    with pytest.raises(ReviewError, match="conflicting identifiers"):
        env.svc.approve_match(*_item_keys(item), actor="asha", reason="x")
    r = env.svc.approve_match(*_item_keys(item), actor="asha", reason="KYC officer verified originals", confirm_id_conflict=True)
    assert r["pending_approval"]                                                   # a second pair of eyes by default
    assert env.store.history(("same_person",))[0].evidence["confirmed_id_conflict"] is True


def test_bulk_actions_report_failures_without_stopping(env):
    items = env.svc.queue(limit=3)
    pairs = [_item_keys(i) for i in items] + [("P999999991", "P999999992")]
    res = env.svc.bulk_reject(pairs, "asha", "bulk: clearly different")
    assert len(res["done"]) == 3 and len(res["failed"]) == 1 and "no record pair" in res["failed"][0]["error"]
    assert all(_item_keys(i) not in {_item_keys(j) for j in env.svc.queue(limit=10**5)} for i in items)
    for d in res["done"]:
        env.svc.undo_action(d["action_id"], "asha")
    assert len(env.svc.queue(limit=10**5)) >= 3


def test_summary_and_input_validation(env):
    item = env.svc.queue(limit=1)[0]
    env.svc.approve_match(*_item_keys(item), actor="asha")
    s = env.svc.summary()
    assert s["active_judgments"] == {"same_person": 1} and s["chain_ok"] and s["pending_approval"] == 0 and s["queue_items_in_last_run"] > 0
    with pytest.raises(ReviewError):
        env.svc.person("P999999991")
    with pytest.raises(ReviewError):
        env.svc.split_person(item["cluster_l"], [[("nope", "x")], [("nope", "y")]], "asha", "bad")
    with pytest.raises(Exception, match="reason"):
        env.svc.override_field(item["cluster_l"], "email", "a@b.in", "asha", "")
