"""The web screens: served with a strict policy, free of the usual script-injection patterns, and wired to endpoints and permissions that exist."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dedupe.api import ApiSettings, create_app
from dedupe.api.guards import api_routes
from dedupe.api.roles import PERMISSIONS

UI = Path("src/dedupe/api/ui")


@pytest.fixture
def app(tmp_path):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="u" * 40, work=tmp_path, database_url=f"sqlite:///{tmp_path / 'u.db'}")
    return create_app(s)


def test_the_page_and_its_two_files_are_served_with_a_strict_policy(app):
    c = TestClient(app)
    for path, kind in (("/ui", "text/html"), ("/ui/app.js", "text/javascript"), ("/ui/style.css", "text/css")):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind), path
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp and "frame-ancestors 'none'" in csp
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert c.get("/ui/other.js").status_code == 404 and c.get("/ui/..%2Fapp.py").status_code == 404           # only the three files exist


def test_nothing_in_the_page_or_script_can_inject_markup_or_leak_the_token():
    html = (UI / "index.html").read_text(encoding="utf-8")
    lines = (UI / "app.js").read_text(encoding="utf-8").splitlines()
    js = chr(10).join(x for x in lines if not x.lstrip().startswith("//"))      # code only, not comments
    assert re.findall(r"<script\b(?![^>]*\bsrc=)", html) == [] and not re.search(r"\son[a-z]+\s*=|style\s*=", html)          # no inline code or styles
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage", "sessionStorage", "document.cookie",
                   "location.hash", "location.search"):
        assert banned not in js, banned
    assert "http://" not in js and "https://" not in js                                                 # every request stays on this origin
    assert all(not u.startswith("//") for u in re.findall(r'"(//[^"]*)"', js))


def test_the_script_only_calls_endpoints_and_permissions_that_exist(app):
    js = (UI / "app.js").read_text(encoding="utf-8")
    first_segments = {r.path.split("/")[1] for r in api_routes(app)}
    called = set(re.findall(r'api\("(?:GET|POST)", "/([a-z-]+)', js))
    assert called and called <= first_segments, called - first_segments
    used = set(re.findall(r'(?:can\("|"Review queue", ")([a-z]+\.[a-z]+)', js)) | set(re.findall(r'\["[A-Za-z ]+", "([a-z]+\.[a-z]+)"', js))
    assert used and used <= set(PERMISSIONS), used - set(PERMISSIONS)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_script_is_valid_javascript():
    r = subprocess.run([shutil.which("node"), "--check", str(UI / "app.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_screens_stay_flat_and_plain():
    """The look is a deliberate choice: neutral workspace, one accent colour, no decoration. These rules keep later edits from drifting."""
    css = re.sub(r"/\*.*?\*/", "", (UI / "style.css").read_text(encoding="utf-8"), flags=re.S)          # rules only, not the comments about them
    text = css + (UI / "index.html").read_text(encoding="utf-8") + (UI / "app.js").read_text(encoding="utf-8")
    for decoration in ("gradient", "box-shadow", "text-shadow", "@keyframes", "animation", "blur(", "border-left", "border-inline-start", "::before", "::after", "backdrop-filter"):
        assert decoration not in css, decoration
    assert not [ch for ch in text if ord(ch) > 0x2000 and ch not in "—–‘’“”…"], "no emoji or symbol characters"
    assert "border-radius: 99" not in css and "border-radius: 50%" not in css                     # no pills or dots
