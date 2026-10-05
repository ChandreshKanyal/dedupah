import json
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dedupe.blocking import generate as block
from dedupe.ingest import ingest
from dedupe.scoring import COMPARISONS, Model, score, train
from dedupe.scoring.__main__ import load_tables
from dedupe.scoring.evaluate import evaluate
from dedupe.scoring.levels import level_matrix, materialize
from dedupe.scoring.train import adjusted_weights, fit_logistic
from dedupe.standardise.run import SCHEMA, standardise_row
from dedupe.standardise.run import run as standardise_run
from dedupe.synthetic import generate as gen_data

RAW = ["full_name", "father_name", "dob", "gender", "mobile", "mobile_alt", "email", "address", "address_alt", "city",
       "state", "pincode", "pan", "aadhaar", "aadhaar_last4", "passport_no", "dl_no", "voter_id", "related_name",
       "party_role", "relation_to_proposer"]
IDX = {c.name: i for i, c in enumerate(COMPARISONS)}


def _levels(pairs):
    """pairs: list of (raw_left, raw_right) dicts -> level matrix, via the real standardiser and SQL."""
    rows = []
    for i, raw in enumerate([r for p in pairs for r in p]):
        base = {k: None for k in RAW}
        base.update(raw, source_id="s", source_record_id=f"{i:04d}")
        rows.append(standardise_row(base))
    con = duckdb.connect()
    con.register("t", pa.Table.from_pylist(rows, schema=SCHEMA))
    con.execute("CREATE TABLE records AS SELECT row_number() OVER (ORDER BY source_record_id) - 1 AS rid, * FROM t")
    con.execute("CREATE TABLE cand AS SELECT 2*i AS l_rid, 2*i+1 AS r_rid FROM range(?) r(i)", [len(pairs)])
    materialize(con, "cand", "lv", with_tf=False)
    return level_matrix(con, "lv")


def test_name_levels():
    def n(a, b):
        return _levels([({"full_name": a}, {"full_name": b})])[0, IDX["name"]]

    assert n("Rajesh Kumar Sharma", "RAJESH KUMAR SHARMA") == 7
    assert n("Rajesh Sharma", "Sharma, Rajesh") == 7
    assert n("Mohd Ali Khan", "Muhammad Ali Khan") == 6 or n("Mohd Ali Khan", "Muhammad Ali Khan") == 7
    assert n("Rajesh Sharma", "Rajeesh Sarma") == 6  # spelling variants sound identical
    assert n("Rajesh Sharma", "Rajesh Kumar Sharma") == 5  # extra middle name
    assert n("R K Sharma", "Rajesh Kumar Sharma") == 4
    assert n("Rina", "Rina Mukherjee") == 3
    assert n("Rajesh Sharma", "Suresh Sharma") == 1
    assert n("Rajesh Sharma", "Anita Verma") == 0
    assert n("Rajesh Sharma", None) == -1


def test_dob_phone_address_gender_levels():
    L = _levels([
        ({"dob": "14/03/1985"}, {"dob": "1985-03-14"}),   # exact
        ({"dob": "01/01/1985"}, {"dob": "1985-01-01"}),   # placeholder
        ({"dob": "03/04/1985"}, {"dob": "04/03/1985"}),   # swapped day/month
        ({"dob": "03/04/1985"}, {"dob": "1985"}),          # year only
        ({"dob": "03/04/1985"}, {"dob": "03/04/1990"}),   # same day+month
        ({"dob": "03/04/1985"}, {"dob": "17/09/1990"}),   # different
        ({"dob": "03/04/1985"}, {}),                       # missing
    ])
    assert list(L[:, IDX["dob"]]) == [5, 4, 3, 2, 1, 0, -1]
    P = _levels([({"mobile": "98765 43211"}, {"mobile": "+919876543211"}), ({"mobile": "98765 43211"}, {"mobile": "98765 43212"}),
                 ({"mobile": "98765 43211", "mobile_alt": "9123456780"}, {"mobile": "9123456780"}), ({"mobile": "98765 43211"}, {})])
    assert list(P[:, IDX["phone"]]) == [2, 0, 1, -1]
    A = _levels([
        ({"address": "H.No 5, Station Rd, Patna 800001", "city": "Patna"}, {"address": "H.No 5, Station Road, Patna 800001", "city": "Patna"}),
        ({"address": "x, Patna 800001"}, {"address": "y, Patna 800001"}),
        ({"city": "Mumbai"}, {"city": "Bombay", "pincode": "400001"}),   # city only on one side
        ({"city": "Mumbai"}, {"city": "Pune"}),
        ({}, {"city": "Pune"}),
    ])
    assert A[0, IDX["address"]] == 4 and A[1, IDX["address"]] == 2 and A[2, IDX["address"]] == 5
    assert A[3, IDX["address"]] == 0 and A[4, IDX["address"]] == -1
    G = _levels([({"gender": "M"}, {"gender": "Male"}), ({"gender": "M"}, {"gender": "F"}), ({"gender": "M"}, {})])
    assert list(G[:, IDX["gender"]]) == [1, 0, -1]


def test_identifier_levels_and_ocr_repair():
    L = _levels([({"pan": "ABCPD1234E"}, {"pan": "ABCPDI234E"}), ({"pan": "ABCPD1234E"}, {"pan": "ABCPD1234F"}),
                 ({"aadhaar_last4": "1234"}, {"aadhaar_last4": "1234"})])
    assert list(L[:, IDX["pan"]]) == [1, 0, -1] and L[2, IDX["aadhaar"]] == 2


def test_logistic_fit_recovers_signs():
    rng = np.random.default_rng(0)
    X = rng.integers(0, 2, size=(2000, 3)).astype(float)
    y = (rng.random(2000) < 1 / (1 + np.exp(-(2.0 * X[:, 0] - 3.0 * X[:, 1] + 0.0 * X[:, 2] - 0.5)))).astype(float)
    w, b = fit_logistic(X, y, l2=0.5)
    assert w[0] > 1.2 and w[1] < -1.8 and abs(w[2]) < 0.6


def test_term_frequency_rewards_rare_values_and_clips():
    weights = {c.name: [0.0] + [1.0] * (c.n_levels - 1) for c in COMPARISONS}
    u = {c.name: [0.5] + [0.01] * (c.n_levels - 1) for c in COMPARISONS}
    L = np.full((3, len(COMPARISONS)), -1, dtype=np.int16)
    L[:, IDX["phone"]] = 2
    TF = np.full((3, len(COMPARISONS)), np.nan)
    TF[:, IDX["phone"]] = [1e-5, 0.01, 0.5]  # unique, typical, extremely common
    w = adjusted_weights(weights, u, L, TF)[:, IDX["phone"]]
    assert w[0] > w[1] > w[2]
    assert w[0] - 1.0 <= np.log(16) + 1e-9 and w[2] - 1.0 >= -np.log(16) - 1e-9


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    d = tmp_path_factory.mktemp("sc")
    gen_data(150, seed=33, out=d / "syn")
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(d / "syn" / os.path.basename(s["connector"]["path"]))
    (d / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    ingest(d / "cfg.json", d / "canon.parquet")
    standardise_run(d / "canon.parquet", d / "std.parquet")
    block(d / "std.parquet", d / "cand.parquet")
    con = duckdb.connect()
    load_tables(con, d / "std.parquet", d / "cand.parquet")
    model = train(con)
    model.save(d / "model.json")
    score(con, model, d / "scored.parquet")
    return d, con, model


def test_end_to_end_quality(scored):
    d, con, _ = scored
    rep = evaluate(con, d / "scored.parquet", d / "syn", 0.9)
    at09 = next(t for t in rep["table"] if t["threshold"] == 0.9)
    assert at09["precision"] > 0.97 and rep["best_f1"]["f1"] > 0.78
    assert all(c["actual_match_rate"] is None or c["actual_match_rate"] <= 1 for c in rep["calibration"])
    top = [c for c in rep["calibration"] if c["range"].startswith("0.99")][0]
    assert top["actual_match_rate"] > 0.97  # the high-confidence band is trustworthy


def test_explanations_add_up_and_model_roundtrips(scored):
    d, _, model = scored
    t = pq.read_table(d / "scored.parquet").to_pandas() if False else pq.read_table(d / "scored.parquet")
    w = np.column_stack([t.column(f"w_{c.name}").to_numpy() for c in COMPARISONS]).sum(axis=1)
    assert np.allclose(w + model.bias / np.log(2), t.column("match_weight").to_numpy(), atol=1e-3)
    p = t.column("probability").to_numpy()
    assert p.min() >= 0 and p.max() <= 1
    m2 = Model.load(d / "model.json")
    assert m2.weights == model.weights and m2.bias == model.bias


def test_same_pan_scores_high_conflicting_pan_scores_low(scored):
    d, _, _ = scored
    t = pq.read_table(d / "scored.parquet")
    p, lv = t.column("probability").to_numpy(), t.column("lv_pan").to_numpy()
    assert np.median(p[lv == 1]) > 0.99 and np.median(p[lv == 0]) < 0.1
