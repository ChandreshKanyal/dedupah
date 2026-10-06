"""Versioned configuration: propose, review, approve, activate, roll back.

    python -m dedupe.governance propose decision --file configs/decision_default.json --actor asha --reason "first version"
    python -m dedupe.governance approve decision 1 --actor ravi
    python -m dedupe.governance activate decision 1 --actor ravi
    python -m dedupe.governance diff decision 1 2
    python -m dedupe.governance impact --work data/staging --name decision --version 2     (dry run on the last run's scored pairs)
    python -m dedupe.governance list | history | verify | rollback decision --to 1 --actor ravi --reason "..."

The store URL comes from --url or DEDUPE_DATABASE_URL (default sqlite:///data/judgments.db). `--solo` lets one person do everything
(development only).
"""
import argparse
import json
import sys
from pathlib import Path

from dedupe.decision.config import DecisionConfig

from .diff import describe_changes
from .impact import dry_run, render
from .store import ConfigError, ConfigStore, canonical


def _content(store: ConfigStore, a) -> dict:
    return json.loads(Path(a.file).read_text(encoding="utf-8")) if getattr(a, "file", None) else store.get(a.name, a.version)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None)
    ap.add_argument("--solo", action="store_true", help="development only: the proposer may activate without a second person")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, *args, actor=False, reason=None):
        p = sub.add_parser(name)
        for a in args:
            p.add_argument(a) if not a.startswith("-") else p.add_argument(a, type=int if a == "--version" else str)
        if actor:
            p.add_argument("--actor", required=True)
        if reason is not None:
            p.add_argument("--reason", default="", required=reason)
        return p
    p = cmd("propose", "name", actor=True, reason=True)
    p.add_argument("--file", required=True)
    for n in ("approve", "activate"):
        p = cmd(n, "name", actor=True, reason=False)
        p.add_argument("version", type=int)
    p = cmd("decline", "name", actor=True, reason=True)
    p.add_argument("version", type=int)
    p = cmd("rollback", "name", actor=True, reason=True)
    p.add_argument("--to", type=int, required=True)
    p = cmd("diff", "name")
    p.add_argument("old", type=int)
    p.add_argument("new", type=int)
    p = cmd("list")
    p.add_argument("name", nargs="?")
    p = cmd("history")
    p.add_argument("name", nargs="?")
    cmd("verify")
    p = cmd("impact")
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--name", default="decision")
    p.add_argument("--version", type=int)
    p.add_argument("--file")
    a = ap.parse_args(argv)
    store = ConfigStore.open(a.url, solo=a.solo)
    try:
        if a.cmd == "propose":
            print(f"proposed {a.name} v{store.propose(a.name, json.loads(Path(a.file).read_text(encoding='utf-8')), a.actor, a.reason)}")
        elif a.cmd == "approve":
            store.approve(a.name, a.version, a.actor, a.reason)
            print(f"{a.actor} approved {a.name} v{a.version}")
        elif a.cmd == "decline":
            store.decline(a.name, a.version, a.actor, a.reason)
            print(f"{a.actor} declined {a.name} v{a.version}")
        elif a.cmd == "activate":
            store.activate(a.name, a.version, a.actor, a.reason)
            print(f"{a.name} v{a.version} is now live")
        elif a.cmd == "rollback":
            print(f"{a.name} restored as v{store.rollback(a.name, a.to, a.actor, a.reason)} (content of v{a.to})")
        elif a.cmd == "diff":
            print("\n".join(describe_changes(a.name, store.get(a.name, a.old), store.get(a.name, a.new))) or "no differences")
        elif a.cmd == "list":
            for n in ([a.name] if a.name else ("decision", "survivorship", "settings")):
                for v in store.versions(n):
                    print(f"{n} v{v.version:<3} {v.status:<10} proposed by {v.proposed_by} {v.proposed_at[:19]}  approved by {list(v.approved_by) or '-'}  {v.reason}")
        elif a.cmd == "history":
            for h in store.history(a.name):
                print(f"#{h['id']:<4} {h['created_at'][:19]} {h['actor']:<12} {h['kind']:<9} {h['name']} v{h['version']}  {h['reason']}")
        elif a.cmd == "verify":
            r = store.verify()
            print(json.dumps(r))
            return 0 if r["ok"] else 1
        elif a.cmd == "impact":
            if a.name != "decision":
                raise ConfigError("a dry run exists for decision configs only (settings and golden-record changes need a full run)")
            content = _content(store, a) if (a.file or a.version) else None
            if content is None:
                raise ConfigError("give --version or --file")
            print(render(dry_run(a.work, DecisionConfig.model_validate(canonical("decision", content)))))
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
