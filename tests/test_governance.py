"""Versioned, audited configuration: workflow, four eyes, tamper evidence, rollback, dry run, and use by the pipeline."""
import json

import pytest
from sqlalchemy import text
from test_pipeline import _setup

from dedupe.decision import DecisionConfig
from dedupe.governance import ConfigError, ConfigStore, describe_changes, dry_run
from dedupe.governance.__main__ import main as cli
from dedupe.pipeline import PipelineConfig, run_pipeline

DEFAULT = json.loads(open("configs/decision_default.json", encoding="utf-8").read())


def _variant(**changes):
    return {**DEFAULT, **changes}


@pytest.fixture
def store(tmp_path):
    return ConfigStore.open(f"sqlite:///{tmp_path / 'cfg.db'}")


def _live(store, content=None, name="decision"):
    v = store.propose(name, content or DEFAULT, "asha", "first version")
    store.approve(name, v, "ravi")
    store.activate(name, v, "ravi")
    return v


def test_a_change_is_a_draft_until_someone_else_approves_it(store):
    v = store.propose("decision", DEFAULT, "asha", "first version")
    assert v == 1 and store.active("decision") is None and store.versions("decision")[0].status == "proposed"
    with pytest.raises(ConfigError, match="needs approval"):
        store.activate("decision", v, "asha")                       # not even the proposer can push it live alone
    with pytest.raises(ConfigError, match="four eyes"):
        store.approve("decision", v, "asha")
    store.approve("decision", v, "ravi")
    assert store.versions("decision")[0].status == "approved"
    store.activate("decision", v, "asha")                           # approved by someone else: anyone may activate
    act = store.active("decision")
    assert act.version == 1 and act.content["auto_merge_at"] == 0.9 and len(act.hash) == 64
    store.activate("decision", v, "asha")                           # idempotent


def test_solo_mode_is_explicit_and_development_only(tmp_path):
    solo = ConfigStore.open(f"sqlite:///{tmp_path / 'solo.db'}", solo=True)
    v = solo.propose("decision", DEFAULT, "dev", "try it")
    solo.activate("decision", v, "dev")
    assert solo.active("decision").version == v


def test_invalid_empty_or_unchanged_proposals_are_refused(store):
    _live(store)
    with pytest.raises(ConfigError, match="reason"):
        store.propose("decision", _variant(auto_merge_at=0.95), "asha", "")
    with pytest.raises(ConfigError, match="invalid decision config"):
        store.propose("decision", _variant(auto_merge_at=0.05), "asha", "bad thresholds")
    with pytest.raises(ConfigError, match="unknown config"):
        store.propose("colour", {}, "asha", "x")
    with pytest.raises(ConfigError, match="identical to version 1"):
        store.propose("decision", DEFAULT, "asha", "no change")
    with pytest.raises(ConfigError, match="unknown blocking rule"):
        store.propose("settings", {"blocking_rules": ["pan", "nonsense"]}, "asha", "typo")
    with pytest.raises(ConfigError, match="invalid decision config"):
        store.propose("decision", _variant(rules=[{"id": "x", "type": "auto_merge", "when": {"comparison": "pan", "is": "same PAN"}, "priority_offset": -1}]),
                      "asha", "offset on the wrong rule type")


def test_versions_are_immutable_and_the_log_is_tamper_evident(store):
    _live(store)
    v2 = store.propose("decision", _variant(auto_merge_at=0.95), "asha", "stricter")
    assert v2 == 2 and store.active("decision").version == 1     # a draft changes nothing that is live
    assert store.verify() == {"ok": True, "rows": 4, "first_bad_id": None}
    with store.engine.begin() as c:
        with pytest.raises(Exception, match="append-only"):
            c.execute(text("UPDATE config_log SET actor = 'mallory' WHERE id = 1"))
    with store.engine.begin() as c:
        with pytest.raises(Exception, match="append-only"):
            c.execute(text("DELETE FROM config_log WHERE id = 1"))
    with store.engine.begin() as c:                              # edit behind the application's back: drop the trigger first
        c.execute(text("DROP TRIGGER config_log_no_update"))
        c.execute(text("UPDATE config_log SET reason = 'edited' WHERE id = 2"))
    assert store.verify() == {"ok": False, "rows": 1, "first_bad_id": 2}


def test_decline_rollback_and_history(store):
    v1 = _live(store)
    v2 = store.propose("decision", _variant(auto_merge_at=0.95), "asha", "stricter")
    store.decline("decision", v2, "ravi", "too strict for retail")
    with pytest.raises(ConfigError, match="declined"):
        store.activate("decision", v2, "ravi")
    v3 = store.propose("decision", _variant(auto_merge_at=0.97), "asha", "even stricter")
    store.approve("decision", v3, "ravi")
    store.activate("decision", v3, "ravi")
    assert [v.status for v in store.versions("decision")] == ["superseded", "declined", "active"]
    with pytest.raises(ConfigError, match="never live"):
        store.rollback("decision", v2, "ravi", "restore the declined one")
    with pytest.raises(ConfigError, match="already the live"):
        store.rollback("decision", v3, "ravi", "no-op")
    v4 = store.rollback("decision", v1, "ravi", "stricter version caused a review backlog")
    act = store.active("decision")
    assert v4 == 4 and act.version == 4 and act.content["auto_merge_at"] == 0.9 and act.hash == store.versions("decision")[0].content_hash
    assert store.versions("decision")[-1].rollback and store.versions("decision")[-1].parent_version == v1
    with pytest.raises(ConfigError, match="rollback"):
        store.activate("decision", v3, "ravi")                    # an old version comes back only through rollback (new version, logged)
    kinds = [h["kind"] for h in store.history("decision")]
    assert kinds == ["propose", "approve", "activate", "propose", "decline", "propose", "approve", "activate", "rollback"]
    assert store.verify()["ok"]


def test_every_config_name_is_versioned_independently(store):
    _live(store)
    s = store.propose("settings", {"blocking_cap": 40}, "asha", "tighter cap")
    assert s == 1 and store.active("settings") is None and store.active("decision").version == 1
    assert store.get("settings", 1)["blocking_cap"] == 40 and store.get("settings", 1)["household_threshold"] == 5.0   # defaults filled in


def test_changes_are_described_in_plain_words():
    old = DecisionConfig.model_validate(DEFAULT).model_dump(mode="json")
    new = json.loads(json.dumps(old))
    new["auto_merge_at"] = 0.95
    rules = new["rules"]
    rules[0]["enabled"] = False
    rules[1]["when"] = {"comparison": "pan", "is": "same PAN"}
    removed = rules.pop(2)
    rules.append({"id": "brand_new", "type": "force_review", "reason": "", "enabled": True, "priority_offset": 0.0, "when": {"comparison": "pan", "is": "same PAN"}})
    out = describe_changes("decision", old, new)
    assert "auto_merge_at 0.9 -> 0.95" in out
    assert f"rule {rules[0]['id']!r}: disabled" in out
    assert f"rule {rules[1]['id']!r}: condition changed" in out
    assert f"rule {removed['id']!r} removed ({removed['type']})" in out
    assert "rule 'brand_new' added (force_review)" in out
    assert describe_changes("settings", {"blocking_cap": 50, "a": {"b": 1}}, {"blocking_cap": 40, "a": {"b": 1}}) == ["blocking_cap: 50 -> 40"]
    assert describe_changes("decision", None, new) == ["first version"]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Run A: file configs. Run B: the same data under a store whose ACTIVE decision and settings versions differ from the files."""
    d = tmp_path_factory.mktemp("gov")
    syn, sources = _setup(d, seed=72)
    a = run_pipeline(PipelineConfig(sources, d / "work_a", truth=syn))
    store = ConfigStore.open(f"sqlite:///{d / 'cfg.db'}")
    rules = [dict(r, enabled=False) if r["id"] in ("review_possible_namesake", "review_possible_twins") else r for r in DEFAULT["rules"]]
    candidate = _variant(auto_merge_at=0.99, rules=rules)
    v = store.propose("decision", candidate, "asha", "stricter auto-merge, namesake rules off")
    store.approve("decision", v, "ravi")
    store.activate("decision", v, "ravi")
    s = store.propose("settings", {"blocking_rules": ["pan", "aadhaar", "phone", "email", "surname_dob"], "blocking_cap": 30}, "asha", "lean blocking")
    store.approve("settings", s, "ravi")
    store.activate("settings", s, "ravi")
    b = run_pipeline(PipelineConfig(sources, d / "work_b", truth=syn, config_store_url=f"sqlite:///{d / 'cfg.db'}"))
    only = ConfigStore.open(f"sqlite:///{d / 'decision_only.db'}", solo=True)          # run C: the decision change alone, blocking untouched
    only.activate("decision", only.propose("decision", candidate, "asha", "decision change only"), "asha")
    c = run_pipeline(PipelineConfig(sources, d / "work_c", config_store_url=f"sqlite:///{d / 'decision_only.db'}"))
    return d, a, b, candidate, c


def test_a_run_records_exactly_which_configuration_it_used(runs):
    d, a, b, *_ = runs
    assert a["configs"]["decision"]["source"] == "file" and a["configs"]["decision"]["version"] is None
    assert b["configs"]["decision"]["source"] == "store" and b["configs"]["decision"]["version"] == 1
    assert b["configs"]["settings"]["version"] == 1 and b["configs"]["survivorship"]["source"] == "file"
    assert a["configs"]["decision"]["hash"] != b["configs"]["decision"]["hash"]
    eff = b["configs"]["effective_settings"]
    assert eff["blocking_cap"] == 30 and eff["blocking_rules"] == ["pan", "aadhaar", "phone", "email", "surname_dob"]
    assert a["configs"]["effective_settings"]["blocking_cap"] == 50 and "name_city_age" in a["configs"]["effective_settings"]["blocking_rules"]
    for n in ("decision", "survivorship", "settings"):
        assert (d / "work_b" / "config_used" / f"{n}.json").exists()
    assert json.loads((d / "work_b" / "config_used" / "decision.json").read_text(encoding="utf-8"))["auto_merge_at"] == 0.99
    assert b["blocking"]["candidate_pairs"] < a["blocking"]["candidate_pairs"]             # fewer blocking rules, fewer candidates
    assert b["decisions"]["auto_merge"] <= a["decisions"]["auto_merge"]


def test_dry_run_predicts_exactly_what_the_real_run_does(runs):
    d, a, _, candidate, c = runs
    rep = dry_run(d / "work_a", DecisionConfig.model_validate(candidate))
    assert rep["pairs"] == sum(a["decisions"][z] for z in ("auto_merge", "review", "reject"))
    assert rep["zones_before"] == {z: a["decisions"][z] for z in ("auto_merge", "review", "reject")}
    assert rep["zones_after"] == {z: c["decisions"][z] for z in ("auto_merge", "review", "reject")}     # exact, not approximate
    assert rep["zones_after"] != rep["zones_before"] and sum(rep["moved"].values()) > 0
    for key, ex in rep["examples"].items():
        assert key in rep["moved"] and 0 < len(ex) <= 5
    # an unchanged config moves nothing
    same = dry_run(d / "work_a", DecisionConfig.model_validate(DEFAULT))
    assert same["moved"] == {} and same["zones_before"] == same["zones_after"] and same["changed_by_rule"] == {}


def test_dry_run_needs_a_previous_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="last pipeline run"):
        dry_run(tmp_path, DecisionConfig.model_validate(DEFAULT))


def test_command_line_workflow(tmp_path, capsys):
    f, url = tmp_path / "d.json", f"sqlite:///{tmp_path / 'cli.db'}"
    f.write_text(json.dumps(DEFAULT), encoding="utf-8")
    base = ["--url", url]
    assert cli(base + ["propose", "decision", "--file", str(f), "--actor", "asha", "--reason", "first"]) == 0
    assert cli(base + ["activate", "decision", "1", "--actor", "asha"]) == 2                  # four eyes
    assert "needs approval" in capsys.readouterr().err
    assert cli(base + ["approve", "decision", "1", "--actor", "ravi"]) == 0
    assert cli(base + ["activate", "decision", "1", "--actor", "ravi"]) == 0
    f.write_text(json.dumps(_variant(review_at=0.2)), encoding="utf-8")
    assert cli(base + ["propose", "decision", "--file", str(f), "--actor", "asha", "--reason", "less review"]) == 0
    capsys.readouterr()
    assert cli(base + ["diff", "decision", "1", "2"]) == 0
    assert "review_at 0.1 -> 0.2" in capsys.readouterr().out
    assert cli(base + ["list", "decision"]) == 0 and "active" in capsys.readouterr().out
    assert cli(base + ["verify"]) == 0
    assert cli(base + ["impact", "--work", str(tmp_path / "nowhere"), "--version", "2"]) == 2   # no previous run to compare with
