"""The web screens: three static files served from this package (no build step, no third-party scripts). Public by design: the files contain no
data; every call the page makes to the API is authenticated like any other. The page gets its own, still strict, content-security policy:
only scripts and styles from this origin, no inline code, no framing, requests to this origin only."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .guards import public

router = APIRouter()
UI = Path(__file__).parent / "ui"
FILES = {"": ("index.html", "text/html; charset=utf-8"), "app.js": ("app.js", "text/javascript; charset=utf-8"),
         "style.css": ("style.css", "text/css; charset=utf-8"),
         **{n: (n, "image/png") for n in ("logo-light.png", "logo-dark.png", "wordmark-light.png", "wordmark-dark.png", "favicon.png")}}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
_cache: dict[str, bytes] = {}


def _serve(name: str) -> Response:
    path, kind = FILES[name]
    if path not in _cache:
        _cache[path] = (UI / path).read_bytes()
    return Response(_cache[path], media_type=kind, headers={"Content-Security-Policy": CSP})


@router.get("/ui", dependencies=[Depends(public())], include_in_schema=False)
def ui_index():
    return _serve("")


@router.get("/ui/app.js", dependencies=[Depends(public())], include_in_schema=False)
def ui_script():
    return _serve("app.js")


@router.get("/ui/{name}.png", dependencies=[Depends(public())], include_in_schema=False)
def ui_image(name: str):
    if name + ".png" not in FILES:
        raise HTTPException(404, "Not found")
    return _serve(name + ".png")


@router.get("/ui/style.css", dependencies=[Depends(public())], include_in_schema=False)
def ui_style():
    return _serve("style.css")
