"""Every third-party package the code imports is declared in pyproject.toml.

The runtime image installs only what is declared, while the dev tools pull in extra packages indirectly: a missing declaration
passes every test here and still breaks the server (psutil did exactly that: every run failed in the runtime image).
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# import name -> distribution name, where they differ; starlette arrives with fastapi
DIST = {"jwt": "pyjwt", "pydantic_settings": "pydantic-settings", "starlette": "fastapi"}


def _imported() -> set[str]:
    names: set[str] = set()
    for f in (ROOT / "src").rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                names.add(n.module.split(".")[0])
    return {x for x in names if x not in sys.stdlib_module_names and x != "dedupe"}


def _declared() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return {m.lower() for m in re.findall(r'"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*(?:[<>=!~]|;|")', text)}


def test_every_imported_package_is_declared():
    declared = _declared()
    missing = sorted(x for x in _imported() if DIST.get(x, x).lower() not in declared)
    assert not missing, f"imported but not declared in pyproject.toml: {missing}"
