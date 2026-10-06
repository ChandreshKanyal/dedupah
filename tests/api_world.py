"""Shared helpers for the API tests: a workspace with a project and a finished run, tokens, live connections."""
import csv
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from test_pipeline import _setup

from dedupe.api import ApiSettings, create_app
from dedupe.api.devtoken import issue
from dedupe.pipeline import PipelineConfig, run_pipeline

SECRET = "t" * 40
PROJECT = "demo"


def pipeline_output(base: Path, seed: int = 73, households: int = 100) -> Path:
    """Run the real pipeline once on synthetic data; returns the output folder (to be imported as a run)."""
    syn, sources = _setup(base, seed=seed, households=households)
    out = base / "out"
    run_pipeline(PipelineConfig(sources, out))
    return out


def app_with_run(tmp: Path, output: Path | None, project: str = PROJECT, **settings_kw):
    """A fresh workspace in `tmp`, a project, and (when `output` is given) that output imported as a finished run.
    Returns (client, settings, services, run_url_prefix or None)."""
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp / "ws", database_url=f"sqlite:///{tmp / 'ws' / 'main.db'}", **settings_kw)
    app = create_app(s)
    sv = app.state.services
    sv.workspace.create(project, "Demo project", "", "setup")
    prefix = None
    if output is not None:
        run = sv.jobs.import_results(project, output, "setup")
        prefix = f"/projects/{project}/runs/{run.id}"
    return TestClient(app), s, sv, prefix


def hdr(s, roles, sub=None, **extra):
    roles = [roles] if isinstance(roles, str) else roles
    return {"Authorization": "Bearer " + issue(s, sub or f"u-{roles[0]}", roles), **extra}


def small_csv(path: Path, rows: int = 12) -> Path:
    pans = ["ABCDE1234F", "PQRST5678K", "LMNOP9012Z", "QWERT3456A", "ZXCVB7890B", "ASDFG1234H"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cust_id", "full_name", "dob", "mobile", "pan", "city", "internal_note"])
        for i in range(rows):
            w.writerow([f"C{i:03d}", f"Customer Number{i} Kumar", "1980-05-1" + str(i % 9), f"98765432{i:02d}", pans[i % 6], "Pune", "vip client"])
    return path


def csv_connection(path, cid: str = "crm") -> dict:
    return {"source_id": cid, "connector": {"type": "csv", "path": str(path)}, "record_id": "cust_id",
            "fields": {"full_name": "full_name", "dob": "dob", "mobile": "mobile", "pan": "pan", "city": "city"}, "attributes": ["internal_note"]}


def make_live(c, s, connection: dict, proposer="u-ops", approver="u-lee") -> int:
    """Propose a connection as one person and approve + activate it as another. Returns the live version."""
    r = c.post("/connections", json={"connection": connection, "reason": "connect this source for testing"}, headers=hdr(s, "platform_admin", proposer))
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    cid = connection["source_id"]
    assert c.post(f"/connections/{cid}/versions/{v}/approve", json={}, headers=hdr(s, "approver", approver)).status_code == 200
    assert c.post(f"/connections/{cid}/versions/{v}/activate", json={}, headers=hdr(s, "approver", approver)).status_code == 200
    return v


__all__ = ["pipeline_output", "app_with_run", "hdr", "small_csv", "csv_connection", "make_live", "SECRET", "PROJECT", "shutil"]
