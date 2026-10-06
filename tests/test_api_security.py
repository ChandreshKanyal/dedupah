"""API security: tokens (forgery, expiry, wrong audience, algorithm confusion), the role matrix on every route, request limits,
safe errors, security headers, and the tamper-evident security log."""
import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from dedupe.api import ApiSettings, SecurityLog, SettingsError, TokenVerifier, create_app
from dedupe.api.auth import AuthError
from dedupe.api.devtoken import issue
from dedupe.api.guards import public
from dedupe.api.roles import PERMISSIONS, ROLES, roles_allowed
from dedupe.api.seclog import scrub

SECRET = "x" * 40
ISS, AUD = "https://idp.example.com", "dedupe-api"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc_settings(**kw):
    base = dict(env="dev", auth_mode="oidc", oidc_issuer=ISS, oidc_audience=AUD, oidc_jwks_url="https://idp.example.com/jwks")
    return ApiSettings(**(base | kw))


def resolver(token):
    if jwt.get_unverified_header(token).get("kid") != "k1":
        raise KeyError("unknown key id")
    return _KEY.public_key()


def claims(**kw):
    now = int(time.time())
    return {"iss": ISS, "aud": AUD, "sub": "u-100", "iat": now, "exp": now + 600, "roles": ["steward"]} | kw


def oidc_token(payload=None, key=_KEY, alg="RS256", kid="k1"):
    return jwt.encode(payload or claims(), key, algorithm=alg, headers={"kid": kid})


def verifier(**kw):
    return TokenVerifier(oidc_settings(**kw), key_resolver=resolver)


def reason(token, **kw):
    with pytest.raises(AuthError) as e:
        verifier(**kw).verify(token)
    return e.value.reason


# ---- settings --------------------------------------------------------------------------------------------------------
def test_production_refuses_insecure_settings():
    with pytest.raises(SettingsError) as e:
        ApiSettings(env="production", auth_mode="dev", dev_secret=SECRET).validate()
    msg = str(e.value)
    for needle in ("dev tokens are not allowed", "ALLOWED_HOSTS", "server database", "https"):
        assert needle in msg
    ok = ApiSettings(env="production", auth_mode="oidc", oidc_issuer=ISS, oidc_audience=AUD, oidc_jwks_url="https://idp.example.com/jwks",
                     allowed_hosts=("dedupe.bank.example",), require_https=True, database_url="postgresql://u:p@db/dedupe")
    ok.validate()
    for bad, needle in ((dict(oidc_jwks_url="http://idp.example.com/jwks"), "https"), (dict(oidc_algorithms=("HS256",)), "asymmetric"),
                        (dict(oidc_algorithms=("none",)), "asymmetric"), (dict(cors_origins=("*",)), "CORS")):
        with pytest.raises(SettingsError, match=needle):
            ApiSettings(**{**ok.__dict__, **bad}).validate()
    with pytest.raises(SettingsError, match="32 characters"):
        ApiSettings(env="dev", auth_mode="dev", dev_secret="short").validate()


def test_settings_come_from_the_environment_and_production_defaults_to_oidc(monkeypatch):
    for k, v in {"DEDUPE_ENV": "production", "DEDUPE_OIDC_ISSUER": ISS, "DEDUPE_OIDC_AUDIENCE": AUD, "DEDUPE_OIDC_JWKS_URL": "https://idp.example.com/jwks",
                 "DEDUPE_ALLOWED_HOSTS": "a.example.com,b.example.com", "DEDUPE_DATABASE_URL": "postgresql://u:p@db/x",
                 "DEDUPE_OIDC_ROLE_MAP": '{"Dedupe-Stewards": "steward"}'}.items():
        monkeypatch.setenv(k, v)
    s = ApiSettings.from_env()
    assert s.auth_mode == "oidc" and s.require_https and not s.enable_docs and s.allowed_hosts == ("a.example.com", "b.example.com")
    assert s.role_map == {"Dedupe-Stewards": "steward"}
    monkeypatch.setenv("DEDUPE_AUTH_MODE", "dev")
    with pytest.raises(SettingsError, match="dev tokens"):
        ApiSettings.from_env()


# ---- tokens ----------------------------------------------------------------------------------------------------------
def test_a_valid_token_gives_identity_and_known_roles_only():
    p = verifier().verify(oidc_token(claims(roles=["steward", "domain-admin", "approver"], preferred_username="Asha K")))
    assert p.sub == "u-100" and p.actor == "u-100" and p.username == "Asha K" and p.roles == {"steward", "approver"}
    assert "judgment.write" in p.permissions and "config.activate" in p.permissions and "runs.start" not in p.permissions


def test_roles_can_be_read_from_a_nested_claim_and_mapped_from_provider_groups():
    v = verifier(roles_claim="realm_access.roles", role_map={"Dedupe-Reviewers": "steward", "Dedupe-QA": "approver"})
    tok = oidc_token(claims(realm_access={"roles": ["Dedupe-Reviewers", "Dedupe-QA", "offline_access"]}, roles=["platform_admin"]))
    assert v.verify(tok).roles == {"steward", "approver"}          # the plain `roles` claim is not consulted
    assert verifier().verify(oidc_token(claims(roles="viewer auditor"))).roles == {"viewer", "auditor"}      # space-separated string form


@pytest.mark.parametrize("payload,why", [
    ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 4000}, "expired"),
    ({"nbf": int(time.time()) + 3600}, "not_yet_valid"),
    ({"aud": "some-other-api"}, "bad_audience"),
    ({"iss": "https://evil.example.com"}, "bad_issuer"),
    ({"exp": int(time.time()) + 90000, "iat": int(time.time())}, "lifetime_too_long"),
])
def test_tokens_that_are_expired_for_someone_else_or_too_long_lived_are_refused(payload, why):
    assert reason(oidc_token(claims(**payload))) == why


def test_missing_required_claims_are_refused():
    for drop in ("exp", "iat", "sub", "iss", "aud"):
        c = claims()
        del c[drop]
        assert reason(oidc_token(c)) in ("missing_claim", "invalid"), drop
    assert reason(oidc_token(claims(sub="   "))) == "bad_subject"


def test_forged_tokens_are_refused():
    good = oidc_token()
    h, p, s = good.split(".")
    assert reason(oidc_token(key=_OTHER)) == "bad_signature"                                      # signed by someone else's key
    forged = base64.urlsafe_b64encode(json.dumps(claims(roles=["platform_admin"])).encode()).rstrip(b"=").decode()
    assert reason(f"{h}.{forged}.{s}") == "bad_signature"                                         # payload edited, signature kept
    assert reason(jwt.encode(claims(), key=None, algorithm="none", headers={"kid": "k1"})) == "bad_algorithm"        # unsigned
    assert reason(f"{h}.{p}.") in ("malformed", "bad_signature", "invalid")                       # signature stripped
    assert reason("not-a-token") == "malformed" and reason("") == "malformed" and reason("a" * 9000) == "malformed"
    # key confusion: sign with HMAC using the PUBLIC key as the secret and claim HS256; must not be accepted
    pub = _KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(claims(roles=["platform_admin"])).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(hmac.new(pub, hdr + b"." + body, hashlib.sha256).digest()).rstrip(b"=")
    assert reason((hdr + b"." + body + b"." + sig).decode()) == "bad_algorithm"
    assert reason(oidc_token(kid="unknown")) == "key_unavailable"                                 # a key id the provider never published


def test_dev_tokens_work_only_in_dev_mode_with_the_right_secret():
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET)
    tok = issue(s, "asha", ["steward"])
    assert TokenVerifier(s).verify(tok).roles == {"steward"}
    other = ApiSettings(env="dev", auth_mode="dev", dev_secret="y" * 40)
    with pytest.raises(AuthError):
        TokenVerifier(other).verify(tok)
    with pytest.raises(AuthError):                                  # a dev token is not accepted by an oidc verifier
        verifier().verify(tok)
    with pytest.raises(SettingsError):
        issue(ApiSettings(env="production", auth_mode="oidc"), "asha", ["steward"])


# ---- the application -------------------------------------------------------------------------------------------------
def dev_app(tmp_path, **kw):
    s = ApiSettings(env="dev", auth_mode="dev", dev_secret=SECRET, work=tmp_path, **kw)
    return create_app(s, seclog=SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}")), s


def bearer(s, role=None, sub=None, roles=None, ttl=600):
    return {"Authorization": "Bearer " + issue(s, sub or f"user-{role}", roles if roles is not None else [role] if role else [], ttl)}


def all_routes(app):
    from fastapi.routing import APIRoute
    return [r for r in app.routes if isinstance(r, APIRoute) and r.path not in ("/openapi.json", "/docs", "/redoc")]


def access_of(route):
    found = [d.call.access for d in route.dependant.dependencies if hasattr(d.call, "access")]
    return found


def test_every_route_declares_its_access(tmp_path):
    app, _ = dev_app(tmp_path)
    routes = all_routes(app)
    assert len(routes) >= 5
    for r in routes:
        assert len(access_of(r)) == 1, f"{r.path} must declare public(), authenticated() or require(...) exactly once"


def test_authorization_matrix_every_route_for_every_role(tmp_path):
    """The generic check: whoever lacks the permission gets 403, an anonymous caller gets 401, a permitted role is never refused.
    Later routes are covered automatically."""
    app, s = dev_app(tmp_path)
    c = TestClient(app)
    for r in all_routes(app):
        (kind, perm), = access_of(r)
        path = r.path.replace("{", "").replace("}", "")                  # path parameters become literal text
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            def call(headers, method=method, path=path):
                return c.request(method, path, headers=headers, json={} if method in ("POST", "PUT", "PATCH") else None)
            if kind == "public":
                assert call({}).status_code not in (401, 403), path
                continue
            assert call({}).status_code == 401, (method, path)
            assert call({"Authorization": "Bearer garbage"}).status_code == 401
            assert call(bearer(s, roles=[])).status_code == (200 if kind == "authenticated" else 403), (method, path, "no roles")
            for role in ROLES:
                got = call(bearer(s, role)).status_code
                if kind == "authenticated" or role in roles_allowed(perm):
                    assert got not in (401, 403), (method, path, role, got)
                else:
                    assert got == 403, (method, path, role, got)


def test_role_design_keeps_duties_apart():
    writes = {"judgment.write", "judgment.approve", "config.propose", "config.approve", "config.activate", "runs.start", "sources.manage"}
    assert not (ROLES["auditor"] & writes), "auditors change nothing"
    assert not (ROLES["viewer"] & (writes | {"pii.unmask", "logs.read"}))
    assert "judgment.approve" not in ROLES["steward"] and "config.approve" not in ROLES["rule_admin"] and "config.activate" not in ROLES["rule_admin"]
    assert not ({"judgment.write", "pii.unmask"} & ROLES["platform_admin"]), "whoever runs the platform neither decides pairs nor sees unmasked data"
    assert {p for r in ROLES.values() for p in r} <= set(PERMISSIONS)


def test_unauthenticated_responses_reveal_nothing(tmp_path):
    app, s = dev_app(tmp_path)
    c = TestClient(app)
    for headers in ({}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic YTpi"},
                    bearer(ApiSettings(env="dev", auth_mode="dev", dev_secret="z" * 40), "viewer")):
        r = c.get("/me", headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert r.json()["error"]["code"] == "unauthorized" and r.json()["error"]["message"] == "Invalid or expired token."
    me = c.get("/me", headers=bearer(s, roles=["steward", "approver"], sub="u-7")).json()
    assert me["sub"] == "u-7" and me["roles"] == ["approver", "steward"] and "judgment.write" in me["permissions"]


def test_security_headers_request_ids_and_no_server_banner(tmp_path):
    app, s = dev_app(tmp_path)
    c = TestClient(app)
    for r in (c.get("/health"), c.get("/me"), c.get("/does-not-exist"), c.post("/health")):
        h = r.headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["cache-control"] == "no-store"
        assert "default-src 'none'" in h["content-security-policy"] and h["referrer-policy"] == "no-referrer"
        assert "server" not in h and "strict-transport-security" not in h               # HSTS only in production
        assert len(h["x-request-id"]) >= 8
    assert c.get("/health", headers={"X-Request-ID": "trace-12345678"}).headers["x-request-id"] == "trace-12345678"
    assert c.get("/health", headers={"X-Request-ID": "bad id\r\nSet-Cookie: x=1"}).headers["x-request-id"] != "bad id"
    assert c.get("/does-not-exist").json()["error"]["code"] == "not_found"


def prod_app(tmp_path, **kw):
    s = ApiSettings(env="production", auth_mode="oidc", oidc_issuer=ISS, oidc_audience=AUD, oidc_jwks_url="https://idp.example.com/jwks",
                    allowed_hosts=("dedupe.bank.example",), require_https=True, trust_proxy=True, database_url="postgresql://u:p@db/dedupe", work=tmp_path, **kw)
    return create_app(s, verifier=TokenVerifier(s, key_resolver=resolver), seclog=SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}")), s


def test_production_app_requires_https_the_right_host_and_hides_its_docs(tmp_path):
    app, _ = prod_app(tmp_path)
    c = TestClient(app, base_url="http://dedupe.bank.example")
    assert c.get("/health").status_code == 400 and c.get("/health").json()["error"]["code"] == "https_required"
    ok = c.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert ok.status_code == 200 and "max-age" in ok.headers["strict-transport-security"]
    assert c.get("/docs", headers={"X-Forwarded-Proto": "https"}).status_code == 404
    assert c.get("/openapi.json", headers={"X-Forwarded-Proto": "https"}).status_code == 404
    wrong = TestClient(app, base_url="http://attacker.example")
    assert wrong.get("/health", headers={"X-Forwarded-Proto": "https"}).status_code == 400            # host header not allowed
    tok = oidc_token(claims(roles=["auditor"]))
    assert c.get("/security/verify", headers={"X-Forwarded-Proto": "https", "Authorization": f"Bearer {tok}"}).status_code == 200


def test_no_cross_origin_access_unless_configured(tmp_path):
    app, _ = dev_app(tmp_path)
    r = TestClient(app).get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    app2, _ = dev_app(tmp_path, cors_origins=("https://ui.example.com",))
    c2 = TestClient(app2)
    assert c2.get("/health", headers={"Origin": "https://ui.example.com"}).headers["access-control-allow-origin"] == "https://ui.example.com"
    assert "access-control-allow-origin" not in c2.get("/health", headers={"Origin": "https://evil.example"}).headers


# ---- limits ----------------------------------------------------------------------------------------------------------
def test_oversized_bodies_are_refused_with_or_without_a_declared_length(tmp_path):
    app, s = dev_app(tmp_path, max_body_bytes=2048)

    @app.post("/echo-test", dependencies=[Depends(public())])
    async def echo(request: Request):
        return {"n": len(await request.body())}

    c = TestClient(app)
    assert c.post("/echo-test", content=b"a" * 1000).json() == {"n": 1000}
    assert c.post("/echo-test", content=b"a" * 5000).status_code == 413
    assert c.post("/echo-test", content=(b"a" * 1000 for _ in range(10))).status_code == 413            # chunked, no length declared
    assert c.get("/health").status_code == 200


def test_rate_limits_per_address_per_user_and_for_failed_logins(tmp_path):
    app, s = dev_app(tmp_path, ip_rate_limit_per_min=1000, rate_limit_per_min=5, auth_fail_limit_per_min=3)
    c = TestClient(app)
    h = bearer(s, "viewer")
    assert [c.get("/me", headers=h).status_code for _ in range(7)] == [200] * 5 + [429] * 2
    assert c.get("/me", headers=bearer(s, "viewer", sub="someone-else")).status_code == 200            # limits are per user
    for _ in range(3):
        assert c.get("/me", headers={"Authorization": "Bearer bad"}).status_code == 401
    locked = c.get("/me", headers=bearer(s, "viewer", sub="third-person"))
    assert locked.status_code == 429 and locked.headers["retry-after"] == "60"                          # guessing is slowed per address
    app2, _ = dev_app(tmp_path / "ip", ip_rate_limit_per_min=3)
    c2 = TestClient(app2)
    assert [c2.get("/health").status_code for _ in range(5)] == [200] * 3 + [429] * 2
    events = SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}").events(limit=100)
    assert {e["event"] for e in events} >= {"rate_limited", "auth_failed"}


def test_sliding_window_forgets_old_requests():
    from dedupe.api.middleware import SlidingWindow
    now = [0.0]
    w = SlidingWindow(60, clock=lambda: now[0])
    assert [w.allow("k", 2) for _ in range(3)] == [True, True, False]
    now[0] = 61
    assert w.allow("k", 2)


# ---- errors never echo the caller's input ----------------------------------------------------------------------------
def test_validation_errors_name_the_field_but_never_echo_the_value(tmp_path):
    app, s = dev_app(tmp_path)
    r = TestClient(app).get("/security/events?limit=ABCDE1234F&after_id=-5", headers=bearer(s, "auditor"))
    assert r.status_code == 422
    body = r.text
    assert "ABCDE1234F" not in body and "-5" not in body and "input" not in r.json()["error"]
    assert {f["field"] for f in r.json()["error"]["fields"]} == {"query.limit", "query.after_id"}


def test_unexpected_errors_are_generic_and_logged_without_their_message(tmp_path):
    app, s = dev_app(tmp_path)

    @app.get("/boom-test", dependencies=[Depends(public())])
    def boom():
        raise ValueError("could not process PAN ABCDE1234F for rina@example.com")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/boom-test")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error" and "ABCDE" not in r.text and "rina" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    ev = [e for e in SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}").events() if e["event"] == "server_error"]
    assert len(ev) == 1 and ev[0]["detail"] == {"error_type": "ValueError"} and ev[0]["request_id"] == r.json()["error"]["request_id"]


# ---- the security log ------------------------------------------------------------------------------------------------
def test_denials_and_failed_logins_are_logged_without_tokens_or_personal_data(tmp_path):
    app, s = dev_app(tmp_path)
    c = TestClient(app)
    c.get("/security/events", headers=bearer(s, "viewer", sub="vera"))
    expired = jwt.encode({"iss": "dedupe-dev", "aud": "dedupe-api", "sub": "old", "iat": int(time.time()) - 5000, "exp": int(time.time()) - 4000}, SECRET, algorithm="HS256")
    c.get("/me", headers={"Authorization": f"Bearer {expired}"})
    events = SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}").events(limit=50)
    denied = next(e for e in events if e["event"] == "permission_denied")
    assert denied["actor"] == "vera" and denied["roles"] == "viewer" and denied["route"] == "/security/events" and denied["status"] == 403
    assert denied["detail"] == {"needs": "logs.read"} and denied["method"] == "GET" and denied["request_id"]
    failed = next(e for e in events if e["event"] == "auth_failed")
    assert failed["detail"] == {"reason": "expired"} and failed["actor"] == ""
    assert expired not in json.dumps(events) and "Bearer" not in json.dumps(events)


def test_auditors_read_the_log_in_pages_and_can_verify_it(tmp_path):
    app, s = dev_app(tmp_path, max_page_size=3)
    c = TestClient(app)
    for i in range(6):
        c.get("/security/events", headers=bearer(s, "viewer", sub=f"v{i}"))
    h = bearer(s, "auditor")
    page = c.get("/security/events?limit=1000", headers=h).json()
    assert len(page) == 3                                                                   # the server caps the page size
    nxt = c.get(f"/security/events?after_id={page[-1]['id']}&limit=2", headers=h).json()
    assert len(nxt) == 2 and nxt[0]["id"] == page[-1]["id"] + 1
    assert [e["event"] for e in c.get("/security/events?event=permission_denied&limit=3", headers=h).json()] == ["permission_denied"] * 3
    assert c.get("/security/events?event=nonsense", headers=h).json() == []
    assert c.get("/security/verify", headers=bearer(s, "platform_admin")).json() == {"ok": True, "rows": c.get("/security/verify", headers=h).json()["rows"], "first_bad_id": None}
    assert c.get("/security/verify", headers=bearer(s, "viewer")).status_code == 403


def test_the_security_log_is_append_only_and_tamper_evident(tmp_path):
    log = SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}")
    for i in range(3):
        log.record("permission_denied", actor=f"u{i}", roles=["viewer"], method="GET", route="/x", status=403, detail={"needs": "logs.read"})
    assert log.verify() == {"ok": True, "rows": 3, "first_bad_id": None}
    with log.engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("UPDATE security_log SET actor = 'mallory' WHERE id = 1"))
    with log.engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("DELETE FROM security_log WHERE id = 3"))
    with log.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER security_log_no_update"))
        conn.execute(text("UPDATE security_log SET actor = 'mallory' WHERE id = 2"))
    assert log.verify() == {"ok": False, "rows": 1, "first_bad_id": 2}
    with pytest.raises(ValueError, match="unknown security event"):
        log.record("made_up")
    create_engine("sqlite://")                                                              # (engine factory sanity: nothing shared between logs)


def test_personal_data_is_scrubbed_from_everything_written_to_the_log(tmp_path):
    assert scrub("PAN ABCDE1234F, mail rina@example.com, mobile 98123 45678? no: 9812345678, +91 9812345678, aadhaar 2345 6789 0123") .count("[redacted]") >= 5
    log = SecurityLog.open(f"sqlite:///{tmp_path / 'sec.db'}")
    log.record("pii_unmasked", actor="steward-1", object_ref="P000000123", detail={"fields": ["pan", "mobile"], "reason": "caller gave ABCDE1234F and 9812345678",
                                                                                  "Bad Key!": "dropped", "n": 3})
    e = log.events()[0]
    assert e["detail"]["reason"] == "caller gave [redacted] and [redacted]" and e["detail"]["fields"] == ["pan", "mobile"] and e["detail"]["n"] == 3
    assert "Bad Key!" not in e["detail"] and e["object_ref"] == "P000000123"
    log.record("pii_unmasked", actor="a@b.com ABCDE1234F", object_ref="rina@example.com")
    last = log.events()[-1]
    assert "@" not in last["actor"] and "ABCDE" not in last["actor"] and last["object_ref"] == "[redacted]"
