"""Workspace administration from the command line.

    python -m dedupe.workspace create --id customer-master --name "Customer master"
    python -m dedupe.workspace import --id customer-master --from data/demo      # register an existing pipeline output as a finished run
    python -m dedupe.workspace list

Uses DEDUPE_WORK_DIR (workspace root), DEDUPE_DATABASE_URL (main database, default sqlite in the root) and DEDUPE_PROJECT_DATABASE_URL.
"""
import argparse
import json
import os
from pathlib import Path

from .projects import Workspace, WorkspaceError
from .runs import RunJobs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--id", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--description", default="")
    i = sub.add_parser("import")
    i.add_argument("--id", required=True)
    i.add_argument("--from", dest="src", type=Path, required=True)
    i.add_argument("--name", default=None, help="create the project with this name if it does not exist")
    sub.add_parser("list")
    a = ap.parse_args()
    root = Path(os.environ.get("DEDUPE_WORK_DIR", "data/staging"))
    ws = Workspace(root, os.environ.get("DEDUPE_DATABASE_URL") or f"sqlite:///{root}/api.db", os.environ.get("DEDUPE_PROJECT_DATABASE_URL") or None)
    actor = os.environ.get("USERNAME") or os.environ.get("USER") or "command-line"
    try:
        if a.cmd == "create":
            print(json.dumps(ws.create(a.id, a.name, a.description, actor).__dict__, indent=2))
        elif a.cmd == "import":
            if ws.get(a.id) is None:
                ws.create(a.id, a.name or a.id, "", actor)
            run = RunJobs(ws, recover=False).import_results(a.id, a.src, actor)
            print(json.dumps({"project": a.id, "run": run.id, "summary": run.summary}, indent=2))
        else:
            for p in ws.list():
                print(f"{p.id:<30} {p.name}")
    except WorkspaceError as e:
        raise SystemExit(str(e)) from None


if __name__ == "__main__":
    main()
