# Security: threat model, controls, and what is NOT done

Status: design and automated tests only. **No independent penetration test, no certification, no legal review has happened.** Nothing here may be presented as "secure" or "compliant" to a customer; it describes what the software does and what must still be done around it. Regulation facts come from secondary sources (see PROGRESS.md section 35) and must be checked against the primary texts by the customer's compliance team.

## 1. What we protect and from whom

| Asset | Why it matters |
|---|---|
| Customer records (names, DOB, PAN, phone, email, address, document numbers) in staging and output files | personal data under DPDP; identity-theft value |
| Aadhaar tokens / reference keys | UIDAI restrictions; never the full number (erased after cleaning) |
| Steward decisions, golden records, rule versions | they decide who is merged: tampering = fraud or customer harm |
| The audit logs (judgments, config log, security log) | evidence for regulators |
| Credentials and keys (database URL, token key, identity-provider settings) | access to everything else |

| Attacker | Examples we design against |
|---|---|
| Anonymous outsider | token guessing, forged tokens, scanning, oversized or malformed requests, enumeration |
| Authenticated user with too few rights | a viewer trying steward actions, a steward approving their own decision, anyone reading unmasked data without being logged |
| Malicious or careless configurator | a connector config reading server files, issuing destructive SQL, redirecting a credential to another host |
| Insider with database access | editing a decision or a log row behind the application |
| Supply chain | vulnerable or malicious dependencies, leaked secrets in the repository |

Out of scope for the software (the deployment must cover them): network perimeter, host hardening, disk/database encryption keys, physical access, the identity provider's own security, endpoint security of stewards.

## 2. Controls built (and the tests that pin them)

| Area | Control | Standard it serves | Tests |
|---|---|---|---|
| Authentication | Tokens from the customer's identity provider (OIDC): signature via the provider's published keys, issuer, audience, expiry, not-before, required claims, maximum lifetime (default 1 h), algorithm allow-list (asymmetric only; `none` and shared-secret algorithms refused; blocks key-confusion), roles from a configurable claim. Failure reason is logged, the caller only sees "Invalid or expired token." | API2 broken authentication; ASVS V9/V10 | `test_api_security.py`: expiry, wrong audience/issuer, forged signature, edited payload, unsigned, key confusion, unknown key id, missing claims, oversized token |
| Authorization (function level) | Every route declares `public()`, `authenticated()` or `require(permission)`; roles map to permissions in one table (`api/roles.py`); duties are separated (steward decides but cannot approve; rule admin proposes but cannot activate; platform admin cannot decide pairs or see unmasked data; auditor changes nothing) | API5 broken function-level authorization | matrix test: every route x every role, plus a test that fails if a route lacks a declaration |
| Authorization (object level, property level) | Deployment is single-tenant (one customer per deployment), so there is no cross-customer object to protect. Inside a deployment: person ids are validated (`P` + 9 digits) and unknown ones return the same 404 as missing data; EVERY response is built from an explicit model (a new column in an output file does not reach a client until listed); identifiers are masked unless the caller has `pii.unmask`, gives a reason, and stays within an unmask rate limit, and each unmasked view is logged (who, which person, which fields, why, never the values). **Not done:** business-unit scoping inside a customer (a steward sees every person); names, dates of birth and addresses are visible to anyone who can read people | API1 BOLA, API3 BOPLA, API6 sensitive flows | `test_api_reads.py` |
| Four eyes | Identity-based (the verified `sub`), not role-based, in the judgment log and the config log, so holding two roles never lets anyone approve their own work | SoD | `test_judgments.py`, `test_governance.py` |
| Resource consumption | per-address and per-user rate limits, stricter limit on failed authentications per address, request body cap (declared or streamed), page-size cap, one pipeline run at a time (with the run endpoint) | API4 | `test_api_security.py` |
| Input handling | strict pydantic models; validation errors list field names, never values; unknown routes/methods return the same error shape | API8 misconfiguration; ASVS V5 | validation and generic-error tests |
| Error handling | unexpected errors return a generic message and a request id; the log gets the error type and stack frames, never the message (messages can contain personal data) | ASVS V7 | `test_unexpected_errors_are_generic...` |
| Transport and browser defences | HTTPS required in production (behind a TLS proxy, `X-Forwarded-Proto` trusted only when `DEDUPE_TRUST_PROXY`), HSTS, `nosniff`, no framing, no caching, `no-referrer`, strict CSP for an API, no `Server` banner, host-header allow-list, no CORS unless origins are listed, docs/OpenAPI off in production | API8; ASVS V12/V14 | header, host, https, CORS, docs tests |
| Audit and access logging | `security_log`: append-only (database triggers), hash-chained, `verify()`, structured JSON also emitted for the SIEM, route templates not filled-in paths, opaque object ids, personal-data patterns scrubbed (PAN, Aadhaar, email, phone), allow-listed detail keys | CERT-In logs; DPDP; ASVS V7 | tamper, append-only, scrubbing, pagination tests |
| Connectors (untrusted configuration) | file paths confined to `DEDUPE_DATA_ROOTS` (symbolic links and `..` resolved; required in production); SQL: one read-only statement, forbidden-word screen, plain identifiers, literal database URLs refused in production, PostgreSQL read-only transactions; Google Sheets: id/gid validated, redirects only to Google hosts, token never sent to another host, size cap | API7 SSRF; injection | `test_connector_security.py` |
| Secrets | credentials only from environment variables; production refuses dev tokens, wildcard hosts, http, sqlite; dev token tool refuses production; gitleaks in CI and pre-commit | ASVS V6/V13 | settings tests |
| Supply chain | `bandit`, `pip-audit`, SBOM (CycloneDX), gitleaks, Trivy image scan in CI (`.github/workflows/ci.yml`, not yet executed on a real CI), `scripts/security_scan.py` locally; non-root runtime image, read-only filesystem, all capabilities dropped (compose) | supply chain | scan passes locally |
| Aadhaar | full numbers erased after cleaning; keyed token; **reference keys from the customer's own vault accepted instead** (`aadhaar_reference_key`) | UIDAI vault guidance | `test_aadhaar_reference.py` |

## 3. Known gaps (do not claim otherwise)

1. **Encryption at rest** for staging and output files and the database: not built (gate 1 before real data).
2. **Business-unit scoping** inside a customer is not built; read endpoints expose names, dates of birth and addresses to every `person.read` holder (identifiers are masked and unmasking is controlled and logged). Write endpoints (2.6c, built) use strict bodies, the verified identity as actor, an optional idempotency key, bulk caps, four-eyes by identity, and log every change (without values) to the security log.
3. **No revocation list** for tokens: rely on short lifetimes and the identity provider's session control.
4. **Rate limits are in memory, per process**: behind several workers or nodes they must also be applied at the gateway/WAF. Every denial writes one log row, so a flood can grow the log: the limits sit before logging, but a gateway limit is still required.
5. **Erasure and retention** (DPDP): the append-only logs conflict with erasure; no design yet (idea: crypto-shredding, keep keys not names).
6. **SQL built with f-strings** (bandit B608, 96 sites, audited and skipped): safe today because caller values are bound parameters or pass through the quoting helpers or the rule language, but it is a convention, not a guarantee. File paths containing a single quote would break (not exploit) some SQL: operator-chosen paths only. Re-audit when adding SQL near caller input.
7. **MySQL and other databases**: no read-only enforcement beyond the query screen: the source database user MUST be read-only.
8. **Windows symbolic-link test skipped** on the development machine (needs privileges): covered by logic, verify in CI on Linux.
9. **Docker, CI and Postgres paths are written but not executed** (Docker engine unavailable; no CI host yet).
10. **No independent test**: see section 5.
11. **Multi-factor, session management, password policy**: delegated entirely to the customer's identity provider.

## 4. Configuration reference (environment variables)

| Variable | Meaning |
|---|---|
| `DEDUPE_ENV` | `dev` or `production`; production turns on every strict check below |
| `DEDUPE_AUTH_MODE` | `oidc` (default in production) or `dev` |
| `DEDUPE_OIDC_ISSUER`, `DEDUPE_OIDC_AUDIENCE`, `DEDUPE_OIDC_JWKS_URL` | identity provider (JWKS URL must be https in production) |
| `DEDUPE_OIDC_ALGORITHMS` | default `RS256,ES256`; shared-secret algorithms are rejected |
| `DEDUPE_OIDC_ROLES_CLAIM`, `DEDUPE_OIDC_ROLE_MAP` | where roles are in the token (dotted path) and how provider groups map to ours |
| `DEDUPE_DEV_AUTH_SECRET` | dev only, at least 32 characters |
| `DEDUPE_ALLOWED_HOSTS`, `DEDUPE_REQUIRE_HTTPS`, `DEDUPE_TRUST_PROXY`, `DEDUPE_CORS_ORIGINS` | network exposure |
| `DEDUPE_MAX_BODY_BYTES`, `DEDUPE_MAX_PAGE_SIZE`, `DEDUPE_RATE_LIMIT_PER_MIN`, `DEDUPE_IP_RATE_LIMIT_PER_MIN`, `DEDUPE_AUTH_FAIL_LIMIT_PER_MIN`, `DEDUPE_MAX_TOKEN_LIFETIME_S` | limits |
| `DEDUPE_DATABASE_URL` | the logs' database (server database required in production) |
| `DEDUPE_DATA_ROOTS` | folders file sources may be read from (required in production) |
| `DEDUPE_TOKEN_KEY` | key for Aadhaar tokens (production refuses the development key) |

## 5. Before any real customer data (gates)

1. Encryption at rest (files and database), keys in a KMS/HSM.
2. Object-level scoping, enforced masking and the PII access log in the endpoints.
3. Aadhaar approach decided with compliance (reference keys recommended).
4. Independent penetration test (annual for critical internet-facing systems per the reported RBI directions) and fixes; vulnerability assessment cadence agreed.
5. Legal and compliance review: DPDP, RBI, IRDAI, UIDAI, CERT-In.
6. Retention and erasure design.
7. Incident-response runbook with the clocks below; log retention in India; clock synchronisation (NTP).

## 6. Incident clocks (reported by secondary sources, verify)

CERT-In and RBI: report cyber incidents within **6 hours** of becoming aware. DPDP: tell affected people without delay and report to the Data Protection Board within **72 hours**. Logs: keep at least **180 days** inside India (CERT-In/RBI), and records for at least a year (DPDP). The security log's JSON lines are meant to be shipped to the customer's SIEM, which owns retention.
