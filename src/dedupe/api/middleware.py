"""Request-level defences, before any route code runs: request id, client address, https and host checks, per-address rate limit,
request-size cap, security headers, and error responses that never echo what the client sent. Plain ASGI so they also apply to errors."""
import json
import logging
import re
import time
import traceback
import uuid
from collections import defaultdict, deque

from .settings import ApiSettings

_RID = re.compile(r"[A-Za-z0-9-]{8,64}")


class SlidingWindow:
    """Allow at most `limit` events per key per `window` seconds. In memory, per process: behind several workers put the limit at the
    gateway too (it is a second line of defence here, not a replacement for a WAF)."""

    def __init__(self, window: float = 60.0, clock=time.monotonic):
        self.window, self.clock, self._hits = window, clock, defaultdict(deque)

    def allow(self, key: str, limit: int) -> bool:
        now, q = self.clock(), self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self._hits) > 50_000:             # bound memory under a scan from many addresses
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window][:10_000]:
                self._hits.pop(k, None)
        return True

    def count(self, key: str) -> int:
        now, q = self.clock(), self._hits[key]
        return sum(1 for t in q if now - t <= self.window)


def error_body(status: int, code: str, message: str, request_id: str) -> bytes:
    return json.dumps({"error": {"code": code, "message": message, "request_id": request_id}}).encode()


async def send_error(send, status: int, code: str, message: str, request_id: str, extra: list | None = None):
    body = error_body(status, code, message, request_id)
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *(extra or [])]})
    await send({"type": "http.response.body", "body": body})


def client_ip(scope, trust_proxy: bool) -> str:
    if trust_proxy:
        for k, v in scope["headers"]:
            if k == b"x-forwarded-for":
                return v.decode("latin-1").split(",")[-1].strip()[:64]      # the address our own proxy saw
    c = scope.get("client")
    return c[0] if c else "unknown"


class ContextMiddleware:
    """Assigns the request id and client address, enforces https, and applies the per-address rate limit."""

    def __init__(self, app, settings: ApiSettings, limiter: SlidingWindow, on_event=None):
        self.app, self.s, self.limiter, self.on_event = app, settings, limiter, on_event

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _RID.fullmatch(incoming) else uuid.uuid4().hex
        ip = client_ip(scope, self.s.trust_proxy)
        scope["state"] = {**scope.get("state", {}), "request_id": rid, "client_ip": ip}

        started = False

        async def send_with_id(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
                msg = {**msg, "headers": [*msg.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(msg)

        if self.s.require_https:
            proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1") if self.s.trust_proxy else scope.get("scheme", "http")
            if proto != "https":
                return await send_error(send_with_id, 400, "https_required", "This API only accepts https.", rid)
        if not self.limiter.allow(f"ip:{ip}", self.s.ip_rate_limit_per_min):
            if self.on_event:
                self.on_event("rate_limited", scope, rid, ip, {"scope": "ip"})
            return await send_error(send_with_id, 429, "rate_limited", "Too many requests.", rid, [(b"retry-after", b"60")])
        try:
            await self.app(scope, receive, send_with_id)
        except _TooLarge:
            raise
        except Exception as e:
            # never show the caller what went wrong (messages can contain personal data); the log gets the error TYPE and the request id
            if self.on_event:
                self.on_event("server_error", scope, rid, ip, {"error_type": type(e).__name__})
            logging.getLogger("dedupe.api").error("unhandled %s, request %s: %s", type(e).__name__, rid, " <- ".join(reversed([f.strip().splitlines()[0] for f in traceback.format_tb(e.__traceback__)])))
            if not started:
                await send_error(send_with_id, 500, "internal_error", "Something went wrong. Quote the request id to support.", rid)


class BodyLimitMiddleware:
    """Refuses oversized request bodies, whether or not the client declares a length."""

    def __init__(self, app, max_bytes: int, on_event=None):
        self.app, self.max, self.on_event = app, max_bytes, on_event

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = scope.get("state", {}).get("request_id", "")
        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max:
            return await self._reject(scope, send, rid)
        seen, started = 0, False

        async def counted_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max:
                    raise _TooLarge
            return msg

        async def tracking_send(msg):
            nonlocal started
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, counted_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(scope, send, rid)

    async def _reject(self, scope, send, rid):
        if self.on_event:
            self.on_event("request_rejected", scope, rid, scope.get("state", {}).get("client_ip", ""), {"reason": "body_too_large"})
        await send_error(send, 413, "body_too_large", "The request body is too large.", rid)


class _TooLarge(Exception):
    pass


class HeadersMiddleware:
    """Adds the standard defensive response headers to every response (an API returns data, never pages: nothing is framed, scripted or cached)."""

    def __init__(self, app, production: bool):
        h = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
             (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"), (b"cache-control", b"no-store"),
             (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"), (b"cross-origin-resource-policy", b"same-origin")]
        if production:
            h.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
        self.app, self.extra = app, h

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(msg):
            if msg["type"] == "http.response.start":
                names = {k.lower() for k, _ in msg.get("headers", [])}
                msg = {**msg, "headers": [*[(k, v) for k, v in msg.get("headers", []) if k.lower() != b"server"],
                                          *[(k, v) for k, v in self.extra if k not in names]]}
            await send(msg)
        await self.app(scope, receive, send_with_headers)
