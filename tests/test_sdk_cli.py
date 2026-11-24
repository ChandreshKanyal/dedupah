"""The engine as a library and a terminal command: inspect, init, run, explain, and the API as a headless service. Synthetic data only."""
import json

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from dedupe import cli, defaults, sdk


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    from test_pipeline import gen_data
    d = tmp_path_factory.mktemp("sdk")
    gen_data(60, seed=91, out=d / "syn")
    return [d / "syn" / "bank_crm.csv", d / "syn" / "loans.csv"]


def test_the_packaged_rules_are_the_shipped_ones():
    from importlib import resources
    for file in defaults.FILES.values():
        packaged = json.loads(resources.files("dedupe.defaults").joinpath(file).read_text(encoding="utf-8"))
        assert packaged == json.loads(open(f"configs/{file}", encoding="utf-8").read()), f"src/dedupe/defaults/{file} differs from configs/{file}"
    careful, balanced = defaults.decision("careful"), defaults.decision("balanced")
    assert careful["use_score"] is False and balanced["use_score"] is True and careful["rules"] == balanced["rules"]
    with pytest.raises(ValueError):
        defaults.decision("reckless")


def test_inspect_maps_columns_from_names_and_values_without_printing_data(files, capsys):
    r = sdk.inspect(files[1])
    assert r["record_id"] == "loan_id" and r["mapping"]["full_name"]["column"] == "applicant_name" and r["mapping"]["pan"]["column"] == "pan"
    assert cli.main(["inspect", str(files[0])]) == 0
    out = capsys.readouterr().out
    assert "full_name" in out and "cust_id" in out and "@" not in out             # column names only: no email address or other value


def test_run_writes_the_outputs_asked_for_and_explains_a_person(files, tmp_path, capsys):
    out = tmp_path / "out"
    assert cli.main(["run", *map(str, files), "-o", str(out), "--outputs", "golden,map,review,clean,links", "--format", "csv"]) == 0
    assert "records became" in capsys.readouterr().out
    s = json.loads((out / "summary.json").read_text())
    assert s["people"] == s["built_from_several_records"] + s["no_match"] and s["built_from_several_records"] > 0
    assert {p.name for p in out.glob("*.csv")} == {"golden.csv", "map.csv", "review.csv", "clean.csv", "links.csv"}
    golden = pq.read_table(out / "work" / "sdk_outputs" / "golden.parquet")
    assert "aadhaar_token" not in golden.column_names and golden.num_rows == s["people"]
    mapped = pq.read_table(out / "work" / "sdk_outputs" / "map.parquet")
    assert mapped.num_rows == s["records"]
    several = [r["person_id"] for r in golden.to_pylist() if r["record_count"] > 1][0]
    links = sdk.explain(out, several)
    assert links["links"] and all(x["how"] in ("rule", "score") for x in links["links"])
    assert cli.main(["explain", str(out), several]) == 0 and several in capsys.readouterr().out
    assert cli.main(["explain", str(out), "P999999999"]) == 2


def test_a_job_file_fixes_the_mapping_and_mistakes_are_said_plainly(files, tmp_path, capsys):
    job = tmp_path / "job.json"
    assert cli.main(["init", *map(str, files), "-o", str(job), "--preset", "careful", "--format", "json", "--outputs", "golden"]) == 0
    j = json.loads(job.read_text())
    assert j["preset"] == "careful" and [x["source_id"] for x in j["sources"]] == ["bank_crm", "loans"]
    res = sdk.run(job=job, out=tmp_path / "o")
    assert res.summary["preset"] == "careful" and json.loads((tmp_path / "o" / "golden.json").read_text())
    j["sources"][0]["fields"]["full_name"] = "no_such_column"
    job.write_text(json.dumps(j))
    assert cli.main(["run", "--job", str(job), "-o", str(tmp_path / "o2")]) == 2
    assert "no_such_column" in capsys.readouterr().err
    assert cli.main(["run", str(tmp_path / "missing.csv")]) == 2
    with pytest.raises(sdk.SdkError):
        sdk.run([files[0]], out=tmp_path / "o3", outputs=("everything",))


def test_the_api_runs_headless_without_screens(tmp_path):
    from dedupe.api import ApiSettings, create_app
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="h" * 40, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'm.db'}", ui_enabled=False)
    c = TestClient(create_app(s))
    assert c.get("/health").json() == {"status": "ok"} and c.get("/ui").status_code == 404 and c.get("/projects").status_code == 401
