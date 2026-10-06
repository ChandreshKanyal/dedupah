"""Safety checks for connector configurations. A connector config will soon come from a user (web screen / API), so it is untrusted input:
a file path must not reach outside the allowed data folders, a query must not change data, a Google Sheet id must not become another URL,
and credentials must never follow a redirect to a different host."""
import os
import re
import urllib.request
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse


class ConnectorConfigError(ValueError):
    pass


def _production() -> bool:
    return os.environ.get("DEDUPE_ENV", "dev").strip().lower() == "production"


# ---- files -----------------------------------------------------------------------------------------------------------
def data_roots() -> list[Path]:
    """Folders the file connectors may read, from DEDUPE_DATA_ROOTS (separated like PATH). Required in production."""
    raw = os.environ.get("DEDUPE_DATA_ROOTS", "").strip()
    return [Path(r).resolve() for r in raw.split(os.pathsep) if r.strip()]


def check_path(path: str | Path) -> Path:
    """The resolved path, or an error when it is outside the allowed folders (symbolic links and `..` are resolved first)."""
    if "\x00" in str(path):
        raise ConnectorConfigError("invalid path")
    roots = data_roots()
    if not roots:
        if _production():
            raise ConnectorConfigError("set DEDUPE_DATA_ROOTS: file sources are only allowed inside listed folders in production")
        return Path(path)
    real = Path(path).resolve()
    if not any(real == r or r in real.parents for r in roots):
        raise ConnectorConfigError("this file is outside the folders sources may be read from")      # the message names no path
    return real


# ---- SQL -------------------------------------------------------------------------------------------------------------
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,62}")
_FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke", "merge", "call", "exec", "execute", "copy",
              "pragma", "attach", "detach", "vacuum", "into", "load", "outfile", "dumpfile", "lo_import", "lo_export", "pg_read_file",
              "pg_read_binary_file", "xp_cmdshell", "dblink", "openrowset", "opendatasource")
_QUOTED = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]")
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def check_identifier(name: str, what: str) -> str:
    if not isinstance(name, str) or not _IDENT.fullmatch(name):
        raise ConnectorConfigError(f"{what} must be a plain name (letters, digits, underscore)")
    return name


def check_select_only(query: str) -> str:
    """One read-only statement. This is a second line of defence: the database user for a source must ALSO be read-only."""
    q = _COMMENTS.sub(" ", query)
    bare = _QUOTED.sub(" ", q)
    if ";" in bare.strip().rstrip(";"):
        raise ConnectorConfigError("only one statement is allowed")
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", bare)}
    if not bare.lstrip().lower().startswith(("select", "with")):
        raise ConnectorConfigError("only SELECT queries are allowed")
    bad = sorted(words & set(_FORBIDDEN))
    if bad:
        raise ConnectorConfigError(f"this query uses words that can change data or reach outside the database: {bad}")
    return query


# ---- Google Sheets ---------------------------------------------------------------------------------------------------
_SHEET_ID = re.compile(r"[A-Za-z0-9_-]{10,100}")
_GID = re.compile(r"\d{1,20}")
_GOOGLE_HOSTS = (".google.com", ".googleusercontent.com")
MAX_SHEET_BYTES = 500 * 1024 * 1024


def check_sheet_ids(spreadsheet_id: str, gid: str) -> tuple[str, str]:
    if not isinstance(spreadsheet_id, str) or not _SHEET_ID.fullmatch(spreadsheet_id) or not _GID.fullmatch(str(gid)):
        raise ConnectorConfigError("invalid spreadsheet id or gid")
    return spreadsheet_id, str(gid)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to https Google hosts, and send the access token only to docs.google.com itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlparse(newurl)
        if u.scheme != "https" or not (u.hostname or "").endswith(_GOOGLE_HOSTS):
            raise URLError("redirect to a host that is not Google was refused")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and u.hostname != "docs.google.com":
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def safe_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SafeRedirect)
