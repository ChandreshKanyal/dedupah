"""Create the first administrator, or reset a password, from the server's command line.

    python -m dedupe.api.accounts_cli create-admin --username admin          (the password is asked for; never put it on the command line)
    python -m dedupe.api.accounts_cli reset-password --username admin

It uses the same environment variables as the server (DEDUPE_WORK_DIR, DEDUPE_DATABASE_URL), so it needs the server's own access to the database:
that is what makes it safe as the way to bootstrap the first account. Both commands are written to the security log as `command-line`.
In automation the password is read from one line of standard input.
"""
import argparse
import getpass
import sys

from .accounts import AccountError, AccountStore


def _read_password(prompt_twice: bool) -> str:
    if not sys.stdin.isatty():                       # automation: one line on standard input
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if prompt_twice and getpass.getpass("Repeat password: ") != first:
        raise SystemExit("The two passwords differ. Nothing was changed.")
    return first


def _store():
    """The accounts store and security log of the server's own database (same environment variables as the server)."""
    import os
    from pathlib import Path

    from dedupe.workspace.projects import engine_for

    from .seclog import SecurityLog

    work = Path(os.environ.get("DEDUPE_WORK_DIR", "data/staging"))
    url = os.environ.get("DEDUPE_DATABASE_URL") or f"sqlite:///{work}/api.db"
    return AccountStore(engine_for(url)), SecurityLog.open(url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin", help="create a user holding the built-in admin role")
    a.add_argument("--username", required=True)
    a.add_argument("--name", default="")
    b = sub.add_parser("reset-password", help="set a new password for an existing user (and unlock the account)")
    b.add_argument("--username", required=True)
    args = ap.parse_args(argv)
    store, seclog = _store()
    try:
        if args.cmd == "create-admin":
            user = store.create_user(args.username, args.name, _read_password(True), ["admin"], "command-line", must_change=False)
            seclog.record("config_change", actor="command-line", detail={"action": "create_admin", "subject": user.username})
            print(f"Created administrator {user.username!r}. Start the server with DEDUPE_AUTH_MODE=local and sign in.")
        else:
            if store.get(args.username) is None:
                raise AccountError("No such user.")
            store.set_password(args.username, _read_password(True), must_change=False)
            seclog.record("config_change", actor="command-line", detail={"action": "reset_password", "subject": args.username})
            print("Password changed. Every earlier session of this user has ended.")
    except AccountError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
