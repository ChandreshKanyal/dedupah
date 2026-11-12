"""A project's own fields (dedupe.custom): records that are not customers (companies here) are deduplicated on columns the built-in
schema does not know, compared the way the project chose. Synthetic data only."""
import csv
import json
import random
from itertools import combinations

import pytest

from dedupe import crypto
from dedupe.custom import CustomField, build_spec
from dedupe.governance.settings import MatchingSettings
from dedupe.pipeline import PipelineConfig, run_pipeline

WORDS = ("shree ganesh lakshmi sagar bharat national royal supreme modern universal apex zenith pioneer sterling orient crystal diamond "
         "emerald galaxy horizon summit vertex nimbus aurora cobalt indigo saffron jasmine lotus banyan tulsi neem chandan kesar").split()
TRADES = "traders enterprises industries textiles exports foods chemicals motors pharma logistics steels polymers agro".split()
CITIES = [("Pune", "411001"), ("Mumbai", "400001"), ("Nagpur", "440001"), ("Indore", "452001"), ("Surat", "395003"), ("Jaipur", "302001")]
FIELDS = [{"name": "company", "kind": "company"}, {"name": "gstin", "kind": "code", "identifier": True},
          {"name": "turnover", "kind": "number", "tolerance": 5, "tolerance_is_percent": True},
          {"name": "founded", "kind": "date", "tolerance": 31}]


def _typo(rng, s):
    i = rng.randrange(1, len(s) - 1)
    return s[:i] + s[i + 1:] if rng.random() < 0.5 else s[:i] + s[i + 1] + s[i] + s[i + 2:]


def _companies(n=260, seed=5):
    """(rows, truth): every company once or several times, each copy written a little differently."""
    rng = random.Random(seed)
    rows, truth = [], {}
    names = set()
    for cid in range(n):
        while True:
            name = f"{rng.choice(WORDS)} {rng.choice(WORDS)} {rng.choice(TRADES)}"
            if name not in names:
                names.add(name)
                break
        city, pin = rng.choice(CITIES)
        gstin = f"27{''.join(rng.choice('ABCDEFGHJKLMNP') for _ in range(5))}{rng.randrange(1000, 9999)}{rng.choice('ABCDEF')}1Z{rng.randrange(1, 9)}"
        turnover = rng.randrange(10, 900) * 100000
        founded = f"{rng.randrange(1, 28):02d}/{rng.randrange(1, 12):02d}/{rng.randrange(1975, 2020)}"
        for _copy in range(rng.choice((1, 1, 2, 2, 3))):
            written = name
            how = rng.random()
            if how < 0.3:
                written = _typo(rng, written)
            elif how < 0.45:
                a, b, c = written.split()
                written = f"{b} {a} {c}"
            written = rng.choice(("{}", "M/s {}", "{} Pvt Ltd", "{} Private Limited", "{} LLP")).format(written.title())
            rid = f"r{len(rows)}"
            rows.append([rid, written, gstin if rng.random() < 0.6 else "", city, pin,
                         round(turnover * rng.uniform(0.98, 1.02)) if rng.random() < 0.8 else "", founded if rng.random() < 0.7 else ""])
            truth[rid] = cid
    return rows, truth


def _sources(tmp_path, rows):
    f = tmp_path / "companies.csv"
    with f.open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["id", "Company Name", "GST No", "city", "pin", "Turnover", "Founded"])
        w.writerows(rows)
    cfg = {"sources": [{"source_id": "vendors", "connector": {"type": "csv", "path": str(f)}, "record_id": "id",
                        "fields": {"city": "city", "pincode": "pin"},
                        "custom": {"company": "Company Name", "gstin": "GST No", "turnover": "Turnover", "founded": "Founded"}}]}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _quality(work, truth):
    got = dict(crypto.connect().execute(f"SELECT source_record_id, cluster_id FROM {crypto.rp(work / 'clusters' / 'clusters.parquet')}").fetchall())
    by = {}
    for rid, c in got.items():
        by.setdefault(c, []).append(rid)
    merged = {frozenset(p) for members in by.values() for p in combinations(members, 2)}
    true = {frozenset(p) for p in combinations(truth, 2) if truth[p[0]] == truth[p[1]]}
    return len(merged & true) / max(1, len(merged)), len(merged & true) / max(1, len(true))


def test_companies_are_deduplicated_on_the_projects_own_fields(tmp_path):
    rows, truth = _companies()
    s = run_pipeline(PipelineConfig(_sources(tmp_path, rows), tmp_path / "work", custom_fields=FIELDS))
    precision, recall = _quality(tmp_path / "work", truth)
    assert precision >= 0.97 and recall >= 0.85, (precision, recall, s["scoring"])
    assert s["scoring"]["mode"] == "id_anchored"            # the GST number seeds the learning, as a PAN does for people
    model = json.loads((tmp_path / "models" / "fs_model.json").read_text(encoding="utf-8"))["weights_bits_by_level"]
    assert model["x_gstin"] == {"identical": 13.2, "different": -4.0}
    assert model["x_company"]["identical"] > 0 > model["x_company"]["different"]


def test_without_an_identifier_the_fields_teach_themselves(tmp_path):
    rows, truth = _companies()
    fields = [f for f in FIELDS if f["name"] != "gstin"]
    s = run_pipeline(PipelineConfig(_sources(tmp_path, rows), tmp_path / "work", custom_fields=fields))
    assert s["scoring"]["mode"] == "unsupervised_em"
    precision, recall = _quality(tmp_path / "work", truth)
    assert precision >= 0.95 and recall >= 0.6, (precision, recall)


def test_a_weight_set_by_hand_is_used_as_written(tmp_path):
    rows, _ = _companies()
    run_pipeline(PipelineConfig(_sources(tmp_path, rows), tmp_path / "work", custom_fields=FIELDS,
                                fixed_weights={"turnover": {"*": 0}, "company": {"identical": 7.5}}))
    model = json.loads((tmp_path / "models" / "fs_model.json").read_text(encoding="utf-8"))["weights_bits_by_level"]
    assert set(model["x_turnover"].values()) == {0.0} and model["x_company"]["identical"] == 7.5
    w = crypto.connect().execute(f"SELECT max(abs(w_x_turnover)) FROM {crypto.rp(tmp_path / 'work' / 'scored_pairs.parquet')}").fetchone()[0]
    assert w == 0


def test_linking_two_files_never_pairs_records_of_one_file(tmp_path):
    rows, truth = _companies()
    half = len(rows) // 2
    cfg = json.loads(_sources(tmp_path, rows[:half]).read_text(encoding="utf-8"))
    second = tmp_path / "second.csv"
    with second.open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["id", "Company Name", "GST No", "city", "pin", "Turnover", "Founded"])
        w.writerows(rows[half:])
    cfg["sources"].append({**cfg["sources"][0], "source_id": "buyers", "connector": {"type": "csv", "path": str(second)}})
    (tmp_path / "sources.json").write_text(json.dumps(cfg), encoding="utf-8")
    run_pipeline(PipelineConfig(tmp_path / "sources.json", tmp_path / "work", custom_fields=FIELDS, compare_within_source=False))
    con = crypto.connect()
    same = con.execute(f"""SELECT count(*) FROM {crypto.rp(tmp_path / 'work' / 'candidates.parquet')} p
        JOIN {crypto.rp(tmp_path / 'work' / 'candidates.index.parquet')} a ON a.rid = p.l_rid
        JOIN {crypto.rp(tmp_path / 'work' / 'candidates.index.parquet')} b ON b.rid = p.r_rid WHERE a.source_id = b.source_id""").fetchone()[0]
    total = con.execute(f"SELECT count(*) FROM {crypto.rp(tmp_path / 'work' / 'candidates.parquet')}").fetchone()[0]
    assert same == 0 and total > 0


def test_a_rule_on_a_field_the_project_dropped_is_skipped_not_fatal(tmp_path):
    rows, _ = _companies(60)
    dec = json.loads(open("configs/decision_default.json", encoding="utf-8").read())
    dec["rules"].append({"id": "same_old_code", "type": "auto_merge", "when": {"comparison": "x_old_code", "is": "identical"}})
    (tmp_path / "decision.json").write_text(json.dumps(dec), encoding="utf-8")
    s = run_pipeline(PipelineConfig(_sources(tmp_path, rows), tmp_path / "work", custom_fields=FIELDS, decision_config=tmp_path / "decision.json"))
    assert s["decisions"]["rules_not_applied"] == ["same_old_code"]


def test_a_rule_can_use_a_custom_field(tmp_path):
    rows, _ = _companies(60)
    dec = {"auto_merge_at": 0.9, "review_at": 0.1, "use_score": False,
           "rules": [{"id": "same_gst", "type": "auto_merge", "when": {"comparison": "x_gstin", "is": "identical"}}]}
    (tmp_path / "decision.json").write_text(json.dumps(dec), encoding="utf-8")
    s = run_pipeline(PipelineConfig(_sources(tmp_path, rows), tmp_path / "work", custom_fields=FIELDS, decision_config=tmp_path / "decision.json"))
    assert s["decisions"]["auto_merge"] > 0 and "rules_not_applied" not in s["decisions"]


@pytest.mark.parametrize("kind,raw,want", [
    ("company", "M/s. Açme Traders Pvt. Ltd.", "acme traders"), ("company", "Pvt Ltd", "pvt ltd"), ("text", "  N/A ", None),
    ("name", "Dr. Ravi  KUMAR", "ravi kumar"), ("code", "27-aapfu 0939f/1zv", "27AAPFU0939F1ZV"), ("code", "0000", None),
    ("number", "Rs. 1,25,000.50", 125000.5), ("number", "(300)", -300.0), ("number", "12abc", None), ("date", "31/12/2031", "2031-12-31"),
    ("date", "soon", None), ("category", " Gold  Tier ", "gold tier"), ("phone", "+91 98201 23456", "9820123456"),
    ("email", "A.B+x@GMail.com", "ab@gmail.com")])
def test_cleaning_by_kind(kind, raw, want):
    f = CustomField(name="v", kind=kind)
    assert f.clean(raw)["x_v"] == want


def test_what_does_not_fit_together_is_refused_in_plain_words():
    for bad, said in [({"custom_fields": [{"name": "a"}, {"name": "a"}]}, "same name"),
                      ({"custom_fields": [{"name": "Bad Name"}]}, "lower-case"),
                      ({"custom_fields": [{"name": "a", "kind": "text", "identifier": True}]}, "only a code"),
                      ({"custom_blocking": [{"name": "r", "parts": [{"field": "nope"}]}]}, "unknown field"),
                      ({"custom_blocking": [{"name": "r", "parts": [{"field": "pan", "how": "sounds"}]}]}, "applies to a text"),
                      ({"fixed_weights": {"phone": {"no such level": 1}}}, "no level"),
                      ({"fixed_weights": {"phone": {"*": 99}}}, "between"),
                      ({"blocking_rules": []}, "no blocking rule")]:
        with pytest.raises(ValueError, match=said):
            MatchingSettings.model_validate(bad)
    assert MatchingSettings.model_validate({"blocking_rules": [], "custom_fields": [{"name": "sku", "kind": "code"}]}).spec().blocking


def test_nothing_a_user_types_reaches_a_query_unchecked():
    spec = build_spec([{"name": "a", "kind": "number", "tolerance": 2.5}, {"name": "b", "kind": "text", "ignore_words": ["co"]}],
                      [{"name": "r", "parts": [{"field": "b", "how": "prefix", "chars": 4}, {"field": "a"}]}])
    sql = " ".join([r.sql for r in spec.blocking] + [lv.sql for c in spec.comparisons for lv in c.levels])
    assert "'" not in sql.replace("' '", "").replace("'|'", "").replace("'day'", "").replace("'T|'", "").replace("'F|'", "")
