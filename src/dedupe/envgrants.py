"""Which project may use which secret environment variable.

Database URLs, passwords and tokens never enter the portal: a data source or a database destination names an environment variable
(DEDUPE_SRC_... or DEDUPE_DST_...) that the server's operator set. Those variables belong to the server, not to a project, so the operator
also says which projects may use each one, in DEDUPE_ENV_GRANTS:

    DEDUPE_ENV_GRANTS="DEDUPE_SRC_CLIENT_A_DB=client-a; DEDUPE_DST_CLIENT_A=client-a,client-a-archive; DEDUPE_SRC_SHARED_MDM=*"

A name ending in * covers every variable that starts with it (DEDUPE_SRC_CLIENT_A_*=client-a). A variable that is not listed can be used by
no project; `*` as the project lets every project use it (a server that serves one client). The grant lives
next to the secret, where only the operator can change it: nobody can grant themselves access from the portal."""
import os
import re

GRANTS_VAR = "DEDUPE_ENV_GRANTS"
_NAME = re.compile(r"^DEDUPE_(SRC|DST)_([A-Z0-9_]{1,60}|[A-Z0-9_]{0,60}\*)$")          # one variable, or a prefix ending in *
_PROJECT = re.compile(r"^(\*|[a-z0-9][a-z0-9-]{1,39})$")          # the project id pattern (workspace.projects.PROJECT_ID) or *


class EnvNotGranted(ValueError):
    """A project named a server variable it was not given."""


def grants() -> dict[str, set[str]]:
    """Variable name -> the project ids allowed to use it ("*" for every project). Malformed entries are left out (they grant nothing)."""
    out: dict[str, set[str]] = {}
    for entry in re.split(r"[;\n]", os.environ.get(GRANTS_VAR, "")):
        name, _, projects = entry.partition("=")
        name = name.strip()
        allowed = {p.strip() for p in projects.split(",") if _PROJECT.match(p.strip())}
        if _NAME.match(name) and allowed:
            out.setdefault(name, set()).update(allowed)
    return out


def env_names(conf: dict | None) -> list[str]:
    """The variable names a connector or destination setting uses: every setting whose name ends in _env (url_env, password_env, token_env...)."""
    return sorted({v for k, v in (conf or {}).items() if k.endswith("_env") and isinstance(v, str) and v})


def check(project_id: str, conf: dict | None) -> None:
    """Raises EnvNotGranted unless the project may use every variable the settings name. The message names the variable and how to grant it,
    never its value (and never whether it is set)."""
    allowed = grants()
    for name in env_names(conf):
        who = set().union(*(ps for n, ps in allowed.items() if n == name or (n.endswith("*") and name.startswith(n[:-1]))))
        if project_id not in who and "*" not in who:
            raise EnvNotGranted(f"This project may not use the server variable {name}. The server's operator allows it per project in "
                                f"{GRANTS_VAR} (for example {name}={project_id}).")


__all__ = ["GRANTS_VAR", "EnvNotGranted", "check", "env_names", "grants"]
