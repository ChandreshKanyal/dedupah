"""The public landing page at "/": served under a strict policy, self-contained (no third-party requests, the font ships with it), free of
inline code, and every file it references exists."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dedupe.api import ApiSettings, create_app

LANDING = Path("src/dedupe/api/ui/landing")


@pytest.fixture
def client(tmp_path):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret="l" * 40, work=tmp_path, database_url=f"sqlite:///{tmp_path / 'l.db'}")
    return TestClient(create_app(s))


def test_the_landing_page_and_its_files_are_public_and_strictly_served(client):
    for path, kind in (("/", "text/html"), ("/landing/landing.css", "text/css"), ("/landing/landing.js", "text/javascript"),
                       ("/landing/fonts/inter-latin.woff2", "font/woff2")):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind), path
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "font-src 'self'" in csp and "'unsafe-inline'" not in csp and "connect-src" not in csp
    for bad in ("/landing/index.html", "/landing/fonts/OFL.txt", "/landing/../routes_ui.py", "/landing/%2e%2e/app.js", "/landing/other.css"):
        assert client.get(bad).status_code == 404, bad


def test_the_page_has_no_inline_code_and_loads_nothing_from_other_sites():
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    css = (LANDING / "landing.css").read_text(encoding="utf-8")
    js = (LANDING / "landing.js").read_text(encoding="utf-8")
    assert re.findall(r"<script\b(?![^>]*\bsrc=)", html) == [] and not re.search(r"\son[a-z]+\s*=|\sstyle\s*=", html)
    assert not re.search(r"(src|href)=\"(https?:)?//", html) and "url(http" not in css and "//fonts." not in html + css
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "fetch(", "XMLHttpRequest", "localStorage"):
        assert banned not in js, banned
    assert "prefers-reduced-motion" in css and "prefers-reduced-motion" in js           # motion can be switched off


def test_every_file_the_page_references_is_served(client):
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    css = (LANDING / "landing.css").read_text(encoding="utf-8")
    refs = set(re.findall(r'(?:src|href)="(/[^"#]*)"', html)) | set(re.findall(r'url\("(/[^"]+)"\)', css))
    assert "/ui" in refs and "/landing/landing.js" in refs
    for ref in refs:
        assert client.get(ref).status_code == 200, ref


def test_the_font_licence_ships_with_the_font():
    assert (LANDING / "fonts" / "inter-latin.woff2").read_bytes()[:4] == b"wOF2"
    assert "SIL Open Font License" in (LANDING / "fonts" / "OFL.txt").read_text(encoding="utf-8")
