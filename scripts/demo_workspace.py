"""Build the demo workspace in data/workspace: an administrator, two private projects with their own connections, one real run each, a database
destination with a results export, and a weekly schedule. Takes about two minutes (it runs the real pipeline twice on the synthetic data).

    .venv\\Scripts\\python scripts\\demo_workspace.py            (wipes data/workspace first; add --keep to build into what is there)

Needs the synthetic data (python -m dedupe.synthetic --households 1200 --seed 42 --out data/synthetic). The administrator is `admin` / `admin`: the
password policy is bypassed ON PURPOSE so a demo is easy to start; this account must be changed or deleted before any real use.
Start the portal on it with DEDUPE_AUTH_MODE=local, DEDUPE_LOCAL_AUTH_SECRET (32+ characters), DEDUPE_WORK_DIR=data/workspace and
DEDUPE_DST_DEMO=sqlite:///<project root>/data/workspace/demo_warehouse.db (the destination the script saved).
"""
import json
import os
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
os.environ["DEDUPE_DATA_ROOTS"] = str(root / "data")
ws = Path(os.environ.get("DEMO_WORKSPACE") or root / "data" / "workspace")                 # DEMO_WORKSPACE builds somewhere else (to try the script safely)
if "--keep" not in sys.argv:
    shutil.rmtree(ws, ignore_errors=True)
ws.mkdir(parents=True, exist_ok=True)
os.environ["DEDUPE_DST_DEMO"] = f"sqlite:///{(ws / 'demo_warehouse.db').as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import insert  # noqa: E402

from dedupe.api import ApiSettings, accounts, create_app  # noqa: E402
from dedupe.api.devtoken import issue  # noqa: E402

s = ApiSettings(env="dev", auth_mode="dev", dev_secret="demo-secret-demo-secret-demo-secret-1234", work=ws, database_url=f"sqlite:///{ws / 'api.db'}",
                ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6, export_limit_per_min=10**6, scheduler_enabled=False)
app = create_app(s)
sv = app.state.services
c = TestClient(app)

with sv.accounts.engine.begin() as conn:
    conn.execute(insert(accounts.users).values(username="admin", display_name="Administrator", password_hash=accounts.hash_password("admin"), disabled=0,
                                               must_change=0, failed_count=0, locked_until=0.0, epoch=1, created_by="command-line", created_at=accounts._now()))
    conn.execute(insert(accounts.user_roles).values(username="admin", role="admin"))
H = {"Authorization": "Bearer " + issue(s, "admin", ["admin"], 3000, username="Administrator")}

for pid, name, desc in (("customer-master", "Customer master", "All customer sources: CRM, policies, loans and leads"),
                        ("insurance-file", "Insurance policy file", "One-off clean-up of the policy workbook")):
    assert c.post("/projects", json={"id": pid, "name": name, "description": desc}, headers=H).status_code == 201

sources = json.loads(Path("configs/synthetic_sources.json").read_text(encoding="utf-8"))["sources"]
for src in sources:
    src["connector"]["path"] = str((root / src["connector"]["path"]).resolve())
    assert c.post("/projects/customer-master/connections", json={"connection": src, "reason": "connect the synthetic " + src["source_id"] + " source"}, headers=H).status_code == 200
    assert c.post(f"/projects/customer-master/connections/{src['source_id']}/versions/1/activate", json={}, headers=H).status_code == 200
    c.post(f"/projects/customer-master/connections/{src['source_id']}/test", headers=H)
excel = next(x for x in sources if x["connector"]["type"] == "xlsx")
assert c.post("/projects/insurance-file/connections", json={"connection": excel, "reason": "the policy workbook, on its own"}, headers=H).status_code == 200
assert c.post(f"/projects/insurance-file/connections/{excel['source_id']}/versions/1/activate", json={}, headers=H).status_code == 200

first = c.post("/projects/insurance-file/runs", json={"connections": [excel["source_id"]], "reason": "first look at the policy file"}, headers=H).json()
print("insurance run", sv.jobs.wait("insurance-file", first["id"], 900).status)
ids = [x["source_id"] for x in sources]
master = c.post("/projects/customer-master/runs", json={"connections": ids, "reason": "weekly sweep"}, headers=H).json()
print("master run", sv.jobs.wait("customer-master", master["id"], 900).status)
rid = master["id"]
crm = c.get("/projects/customer-master/connections/bank_crm", headers=H).json()["latest"]
crm["attributes"] = crm["attributes"][:3]
c.post("/projects/customer-master/connections", json={"connection": crm, "reason": "carry fewer CRM columns along"}, headers=H)       # a waiting proposal to show

dest = c.post("/projects/customer-master/destinations", json={"destination": {"destination_id": "demo_warehouse", "url_env": "DEDUPE_DST_DEMO",
                                                                                "description": "The reporting warehouse (demo database)"}, "reason": "the reporting warehouse for the demo"}, headers=H)
assert dest.status_code == 200, dest.text
assert c.post("/projects/customer-master/destinations/demo_warehouse/versions/1/activate", json={}, headers=H).status_code == 200
assert c.post(f"/projects/customer-master/runs/{rid}/export/database", json={"destination": "demo_warehouse", "dataset": "households", "table": "dedupe_households", "mode": "create",
                                                                              "reason": "load the household table for reporting"}, headers=H).status_code == 200
assert c.post("/projects/customer-master/schedules", json={"name": "Weekly sweep", "connections": ids, "frequency": "weekly", "weekday": 0, "at_time": "02:00",
                                                           "timezone": "Asia/Kolkata"}, headers=H).status_code == 201
print("demo workspace ready in", ws, "- sign in as admin / admin")
