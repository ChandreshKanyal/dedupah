"""Safety checks for connector configurations. A connector config will soon come from a user (web screen / API), so it is untrusted input:
a file path must not reach outside the allowed data folders, a query must not change data, a Google Sheet id must not become another URL,
and credentials must never follow a redirect to a different host."""
import http.client
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


# ---- network hosts (databases, SFTP, REST APIs, object storage endpoints) ----------------------------------------------
_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")
# Cloud instance metadata services hand out the server's own credentials: no source may ever point there.
_METADATA = ("169.254.", "fd00:ec2::254", "metadata.google.internal", "metadata.azure.com", "100.100.100.200")


def allow_insecure_http() -> bool:
    """Plain http and any host, for local tests only (never in production)."""
    return os.environ.get("DEDUPE_SOURCES_ALLOW_HTTP") == "1" and not _production()


def _blocked(ip_text: str) -> bool:
    import ipaddress
    ip = ipaddress.ip_address(ip_text.split("%")[0])
    return ip.is_link_local or str(ip) in _METADATA or (ip.version == 6 and ip.ipv4_mapped is not None and ip.ipv4_mapped.is_link_local)


def resolve_checked(host: str, what: str = "host") -> list[str]:
    """check_host, then the addresses the name resolves to NOW (all of them allowed). Connect to one of these addresses, not to the name, so
    a DNS answer that changes between the check and the connection (DNS rebinding) cannot lead to a metadata address."""
    import socket
    h = check_host(host, what)
    try:
        addrs = list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(h, None)))
    except OSError:
        raise ConnectorConfigError(f"the {what} name could not be resolved") from None
    if any(_blocked(a) for a in addrs):
        raise ConnectorConfigError(f"{what} resolves to a link-local or metadata address, which is never allowed")
    return addrs


def check_peer(sock, what: str = "host") -> None:
    """After connecting: the address actually reached must not be a metadata or link-local one (the last line against DNS rebinding)."""
    if _blocked(sock.getpeername()[0]):
        sock.close()
        raise ConnectorConfigError(f"{what} turned out to be a link-local or metadata address; the connection was closed")


def check_host(host: str, what: str = "host") -> str:
    """A host name or IP address: well formed, not a cloud metadata address (also after DNS resolution), and inside DEDUPE_SOURCE_HOSTS when that
    allow-list is set (comma separated; '*.example.com' allows sub-domains). Production deployments should set the allow-list AND block the
    metadata address (169.254.169.254) at the network: database drivers resolve the name again when they connect, so for drivers that cannot
    be handed a fixed address the check is repeated right before each connection, which narrows but cannot close that gap."""
    import ipaddress
    import socket
    h = (host or "").strip().strip("[]")
    try:
        ipaddress.ip_address(h)
        is_ip = True
    except ValueError:
        is_ip = False
    if not h or (not is_ip and not _HOST.match(h)):
        raise ConnectorConfigError(f"{what} must be a host name or an IP address")
    low = h.lower().rstrip(".")
    if low.startswith(_METADATA) or low in _METADATA:
        raise ConnectorConfigError(f"{what} points at a cloud metadata service, which is never allowed")
    allowed = [a.strip().lower() for a in os.environ.get("DEDUPE_SOURCE_HOSTS", "").split(",") if a.strip()]
    if allowed and not any(low == a or (a.startswith("*.") and low.endswith(a[1:])) for a in allowed):
        raise ConnectorConfigError(f"this {what} is not on the list of hosts sources may connect to (DEDUPE_SOURCE_HOSTS)")
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(h, None)} if not is_ip else {h}
    except OSError:
        return h                                    # unknown name: the driver reports that it cannot connect
    if any(_blocked(a) for a in addrs):
        raise ConnectorConfigError(f"{what} resolves to a link-local or metadata address, which is never allowed")
    return h


def check_url(url: str, what: str = "URL") -> str:
    """An https URL (http only in local tests) whose host passes check_host; no user name or password inside the URL."""
    u = urlparse(url or "")
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not allow_insecure_http()):
        raise ConnectorConfigError(f"{what} must start with https://")
    if u.username or u.password:
        raise ConnectorConfigError(f"{what} must not contain a user name or password: use the credential settings")
    check_host(u.hostname or "", what + " host")
    return url


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the same scheme and host, so a credential header never travels to another server."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        a, b = urlparse(req.full_url), urlparse(newurl)
        if (a.scheme, a.hostname, a.port) != (b.scheme, b.hostname, b.port):
            raise URLError("a redirect to another host was refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _CheckedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        check_peer(self.sock, "the API host")


class _CheckedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()                         # TCP connect + TLS handshake (certificate and name are still verified)
        check_peer(self.sock, "the API host")


class _CheckedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_CheckedHTTPConnection, req)


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        kw = {"context": self._context}
        if hasattr(self, "_check_hostname"):      # Python 3.10/3.11 pass it on; later versions dropped it
            kw["check_hostname"] = self._check_hostname
        return self.do_open(_CheckedHTTPSConnection, req, **kw)


def same_host_opener() -> urllib.request.OpenerDirector:
    """Redirects stay on the same host, and every connection is checked AFTER it is made (the address really reached), so a DNS answer that
    changes after the settings were checked cannot lead to a metadata address."""
    return urllib.request.build_opener(SameHostRedirect, _CheckedHTTPHandler, _CheckedHTTPSHandler)


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
