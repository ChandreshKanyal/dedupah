"""Applications and APIs: any JSON REST API, plus ready-made settings for Salesforce, HubSpot and Microsoft Dynamics 365.

Every request goes over https to a host that passes the host checks; a redirect may not leave that host; a "next page" link must stay on the same
host; responses are size-capped; 429 and 5xx answers are retried a few times, honouring Retry-After. Credentials come only from environment
variables named in the settings and are never written to logs or error messages.

Documented endpoints used:
- Salesforce: OAuth 2.0 client credentials flow at https://<My Domain>/services/oauth2/token, then GET /services/data/vXX.X/query?q=SOQL,
  following nextRecordsUrl (developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/dome_query.htm).
- HubSpot: GET https://api.hubapi.com/crm/v3/objects/{object}?limit=100&properties=..., following paging.next.after, with a private app token
  (developers.hubspot.com/docs/api/crm/understanding-the-crm).
- Dynamics 365 / Dataverse: client credentials at https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token with scope {org}/.default, then
  GET {org}/api/data/v9.2/{entity set}?$select=..., following @odata.nextLink (learn.microsoft.com/power-apps/developer/data-platform/webapi/query-data-web-api).
"""
import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

import pyarrow as pa

from .base import Connector, register, secret
from .guard import ConnectorConfigError, allow_insecure_http, check_url, same_host_opener
from .records import batches_of, columns_of

MAX_PAGE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 100_000
RETRIES = 3
_NAME = re.compile(r"^[A-Za-z_][\w-]{0,60}$")


def dig(doc, path: str | None):
    """A value inside a JSON document by a dotted path. A key that itself contains dots (@odata.nextLink) is found as a whole."""
    if not path:
        return doc
    if isinstance(doc, dict) and path in doc:
        return doc[path]
    head, _, rest = path.partition(".")
    if isinstance(doc, dict) and head in doc:
        return dig(doc[head], rest) if rest else doc[head]
    return None


class _Http(Connector):
    """Shared HTTP machinery: authentication, retries, paging."""

    def _get_json(self, url: str, headers: dict, data: bytes | None = None, method: str = "GET") -> dict | list:
        check_url(url)
        for attempt in range(RETRIES + 1):
            req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", "User-Agent": "dedupe-connector/1", **headers})
            try:
                with same_host_opener().open(req, timeout=60) as resp:      # nosec B310 - scheme and host checked by check_url
                    body = resp.read(MAX_PAGE_BYTES + 1)
                if len(body) > MAX_PAGE_BYTES:
                    raise ConnectorConfigError("one page of the answer is larger than the allowed size: lower the page size")
                return json.loads(body.decode("utf-8-sig") or "null")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                    wait = e.headers.get("Retry-After", "")
                    time.sleep(min(int(wait), 60) if wait.isdigit() else 2 ** attempt)
                    continue
                if e.code in (401, 403):
                    raise ConnectorConfigError(f"the API refused the credentials (HTTP {e.code})") from None
                if e.code == 404:
                    raise ConnectorConfigError("the API address was not found (HTTP 404)") from None
                raise ConnectorConfigError(f"the API answered with an error (HTTP {e.code})") from None
            except json.JSONDecodeError:
                raise ConnectorConfigError("the API did not answer with JSON") from None
        raise ConnectorConfigError("the API kept answering 'busy'")

    def _client_credentials(self, token_url: str, client_id: str, client_secret: str, scope: str | None) -> str:
        form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
        if scope:
            form["scope"] = scope
        doc = self._get_json(token_url, {"Content-Type": "application/x-www-form-urlencoded"}, urllib.parse.urlencode(form).encode(), "POST")
        token = doc.get("access_token") if isinstance(doc, dict) else None
        if not token:
            raise ConnectorConfigError("the sign-in service did not return an access token")
        return token

    def _headers(self) -> dict:
        return {}

    def _pages(self) -> Iterator[list[dict]]:
        raise NotImplementedError

    def _records(self):
        for page in self._pages():
            for r in page:
                yield self._clean(r if isinstance(r, dict) else {"value": r})

    def _clean(self, r: dict) -> dict:
        return r

    def test_connection(self) -> None:
        self.columns()

    def columns(self) -> list[str]:
        return columns_of(self._records())

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        yield from batches_of(self._records(), batch_size, self.columns())


@register
class RestApiConnector(_Http):
    """Any JSON REST API that returns a list of records.

    Settings: url; auth (none, bearer, basic, api_key, oauth2) with its credential settings; records_path (where the list is: "data", "results",
    "value"; empty when the answer is the list itself); pagination (none, next_link, page, offset, cursor) with its settings; max_pages."""
    types = ("rest_api",)

    def __init__(self, **config):
        super().__init__(**config)
        check_url(config.get("url", ""))
        self.auth = config.get("auth") or "none"
        if self.auth not in ("none", "bearer", "basic", "api_key", "oauth2"):
            raise ConnectorConfigError("auth must be one of none, bearer, basic, api_key, oauth2")
        self.paging = config.get("pagination") or "none"
        if self.paging not in ("none", "next_link", "page", "offset", "cursor"):
            raise ConnectorConfigError("pagination must be one of none, next_link, page, offset, cursor")
        for k in ("page_param", "offset_param", "limit_param", "cursor_param", "api_key_header"):
            if config.get(k) and not _NAME.match(config[k]):
                raise ConnectorConfigError(f"{k} must be a plain parameter name")
        self.max_pages = min(int(config.get("max_pages") or 1000), MAX_PAGES)
        self._token: str | None = None

    def _headers(self) -> dict:
        c = self.config
        if self.auth == "bearer":
            return {"Authorization": "Bearer " + secret(None, c.get("token_env"), "API token")}
        if self.auth == "basic":
            pw = secret(None, c.get("password_env"), "API password")
            return {"Authorization": "Basic " + base64.b64encode(f"{c.get('username', '')}:{pw}".encode()).decode()}
        if self.auth == "api_key":
            return {c.get("api_key_header") or "X-API-Key": secret(None, c.get("api_key_env"), "API key")}
        if self.auth == "oauth2":
            if self._token is None:
                self._token = self._client_credentials(check_url(c.get("token_url", ""), "token_url"), c.get("client_id", ""),
                                                       secret(None, c.get("client_secret_env"), "OAuth client secret"), c.get("scope"))
            return {"Authorization": "Bearer " + self._token}
        return {}

    def _with(self, url: str, **params) -> str:
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        q.update({k: str(v) for k, v in params.items()})
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), u.fragment))

    def _pages(self):
        c, base = self.config, self.config["url"]
        size = int(c.get("page_size") or 100)
        page_no, offset, cursor, url = int(c.get("page_start") or 1), 0, None, base
        host = urllib.parse.urlsplit(base).netloc
        for _ in range(self.max_pages):
            if self.paging == "page":
                url = self._with(base, **{c.get("page_param") or "page": page_no}, **({c["limit_param"]: size} if c.get("limit_param") else {}))
            elif self.paging == "offset":
                url = self._with(base, **{c.get("offset_param") or "offset": offset, c.get("limit_param") or "limit": size})
            elif self.paging == "cursor" and cursor is not None:
                url = self._with(base, **{c.get("cursor_param") or "cursor": cursor})
            doc = self._get_json(url, self._headers())
            recs = dig(doc, c.get("records_path"))
            recs = recs if isinstance(recs, list) else ([recs] if isinstance(recs, dict) else [])
            yield recs
            if self.paging == "none" or not recs:
                return
            if self.paging == "next_link":
                nxt = dig(doc, c.get("next_link_path") or "next")
                if not nxt:
                    return
                url = urllib.parse.urljoin(url, str(nxt))
                if urllib.parse.urlsplit(url).netloc != host:
                    raise ConnectorConfigError("the next-page link points at another host; it was not followed")
            elif self.paging == "page":
                page_no += 1
            elif self.paging == "offset":
                offset += len(recs)
            elif self.paging == "cursor":
                cursor = dig(doc, c.get("cursor_path") or "next_cursor")
                if not cursor:
                    return


def _https_host(value: str, suffixes: tuple[str, ...], what: str) -> str:
    """The base URL for a SaaS host given as a host name or URL; it must belong to the vendor's domains (any host in local tests)."""
    v = (value or "").strip().rstrip("/")
    if not v.startswith(("https://", "http://")):
        v = "https://" + v
    host = urllib.parse.urlsplit(v).hostname or ""
    if not allow_insecure_http() and not host.endswith(suffixes):
        raise ConnectorConfigError(f"{what} must be a host under " + " or ".join(suffixes))
    check_url(v, what)
    return v


@register
class SalesforceConnector(_Http):
    """Salesforce (Sales / Service / Financial Services Cloud) with a SOQL query. Settings: instance (your My Domain, e.g. acme.my.salesforce.com),
    client_id + client_secret_env of a connected app with the client credentials flow enabled (or access_token_env), query, api_version."""
    types = ("salesforce",)

    def __init__(self, **config):
        super().__init__(**config)
        self.base = _https_host(config.get("instance", ""), (".salesforce.com", ".force.com"), "instance")
        q = (config.get("query") or "").strip()
        if not re.match(r"(?is)^select\s.+\sfrom\s+\w+", q) or ";" in q:
            raise ConnectorConfigError("query must be one SOQL SELECT ... FROM ... statement")
        if not re.fullmatch(r"v?\d{2}\.\d", config.get("api_version") or "v62.0"):
            raise ConnectorConfigError("api_version looks like v62.0")
        self._token: str | None = None

    def _headers(self) -> dict:
        if self._token is None:
            c = self.config
            if c.get("access_token_env"):
                self._token = secret(None, c["access_token_env"], "Salesforce access token")
            else:
                self._token = self._client_credentials(self.base + "/services/oauth2/token", c.get("client_id", ""),
                                                       secret(None, c.get("client_secret_env"), "Salesforce client secret"), None)
        return {"Authorization": "Bearer " + self._token}

    def _pages(self):
        v = self.config.get("api_version") or "v62.0"
        url = f"{self.base}/services/data/{v if v.startswith('v') else 'v' + v}/query?" + urllib.parse.urlencode({"q": self.config["query"].strip()})
        for _ in range(MAX_PAGES):
            doc = self._get_json(url, self._headers())
            yield doc.get("records") or []
            nxt = doc.get("nextRecordsUrl")
            if doc.get("done", True) or not nxt:
                return
            url = self.base + nxt if nxt.startswith("/") else _https_host(nxt, (".salesforce.com", ".force.com"), "next page")

    def _clean(self, r: dict) -> dict:
        def drop(o):
            if isinstance(o, dict):
                return {k: drop(v) for k, v in o.items() if k != "attributes"}
            return o
        return drop(r)


@register
class HubspotConnector(_Http):
    """HubSpot CRM objects (contacts, companies, deals ...) with a private app access token (token_env). properties lists the properties to read
    (comma separated); HubSpot returns only a few defaults otherwise."""
    types = ("hubspot",)

    def __init__(self, **config):
        super().__init__(**config)
        if not re.fullmatch(r"[a-z0-9_]{2,40}", config.get("object") or "contacts"):
            raise ConnectorConfigError("object is a HubSpot object type such as contacts or companies")
        props = [p.strip() for p in (config.get("properties") or "").split(",") if p.strip()]
        if any(not re.fullmatch(r"[a-z0-9_]{1,100}", p) for p in props):
            raise ConnectorConfigError("properties are HubSpot internal property names, separated by commas")
        self.props = props
        self.base = "https://api.hubapi.com" if not (allow_insecure_http() and config.get("base_url")) else config["base_url"].rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": "Bearer " + secret(None, self.config.get("token_env"), "HubSpot access token")}

    def _pages(self):
        after = None
        for _ in range(MAX_PAGES):
            params = {"limit": 100, "archived": "false"}
            if self.props:
                params["properties"] = ",".join(self.props)
            if after:
                params["after"] = after
            doc = self._get_json(f"{self.base}/crm/v3/objects/{self.config.get('object') or 'contacts'}?" + urllib.parse.urlencode(params), self._headers())
            yield doc.get("results") or []
            after = dig(doc, "paging.next.after")
            if not after:
                return

    def _clean(self, r: dict) -> dict:
        out = {k: v for k, v in r.items() if k != "properties"}
        out.update(r.get("properties") or {})                   # properties.firstname -> firstname
        return out


@register
class Dynamics365Connector(_Http):
    """Microsoft Dynamics 365 / Dataverse tables through the Web API. Settings: org_url (https://yourorg.crm8.dynamics.com), tenant_id,
    client_id + client_secret_env of an app registration that is an application user in the environment, entity (entity set name such as
    contacts or accounts), select (columns, comma separated)."""
    types = ("dynamics365",)

    def __init__(self, **config):
        super().__init__(**config)
        self.org = _https_host(config.get("org_url", ""), (".dynamics.com",), "org_url")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{1,60}", config.get("entity") or ""):
            raise ConnectorConfigError("entity is the table's entity set name, e.g. contacts or accounts")
        sel = [s.strip() for s in (config.get("select") or "").split(",") if s.strip()]
        if any(not re.fullmatch(r"[a-z_][a-z0-9_]{0,100}", s) for s in sel):
            raise ConnectorConfigError("select lists column logical names, separated by commas")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}|[a-z0-9.-]{3,100}", config.get("tenant_id") or ""):
            raise ConnectorConfigError("tenant_id is the Microsoft Entra tenant id (a GUID) or domain")
        self.select = sel
        self._token: str | None = None

    def _headers(self) -> dict:
        if self._token is None:
            c = self.config
            token_url = c.get("token_url") if allow_insecure_http() and c.get("token_url") else \
                f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token"
            self._token = self._client_credentials(token_url, c.get("client_id", ""), secret(None, c.get("client_secret_env"), "client secret"),
                                                   self.org + "/.default")
        return {"Authorization": "Bearer " + self._token, "OData-MaxVersion": "4.0", "OData-Version": "4.0", "Prefer": "odata.maxpagesize=5000"}

    def _pages(self):
        url = f"{self.org}/api/data/v9.2/{self.config['entity']}" + ("?" + urllib.parse.urlencode({"$select": ",".join(self.select)}) if self.select else "")
        host = urllib.parse.urlsplit(self.org).netloc
        for _ in range(MAX_PAGES):
            doc = self._get_json(url, self._headers())
            yield doc.get("value") or []
            url = doc.get("@odata.nextLink")
            if not url:
                return
            if urllib.parse.urlsplit(url).netloc != host:
                raise ConnectorConfigError("the next-page link points at another host; it was not followed")

    def _clean(self, r: dict) -> dict:
        return {k: v for k, v in r.items() if not k.startswith("@odata.")}
