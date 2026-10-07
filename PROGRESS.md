# PROGRESS.md — Customer Data Deduplication Platform

> **Living document. Read this first at the start of every session, update it at the end of every step.**
> Last updated: 2026-10-07 (end of session 1)

## >>> RESUME HERE (latest state: end of session 1, after the portal audit) <<<

**One-line status:** a working, branded (Aurionpro) portal over the dedupe engine: private projects that own their connections, uploads, runs, decisions and rules; run queue and schedules; results export to files and databases; a dedicated Review section for the human in the loop; deleting runs and projects; local username/password sign-in with an admin and admin-defined roles (OIDC single sign-on still supported). **377 tests + 3 skipped, ruff and bandit clean, live click-throughs in Edge with 0 problems (section 53 adds golden-record rules, incremental runs, messy-data handling).** EVERYTHING is validated on SYNTHETIC data only; nothing has run on real data; no independent security test or legal review has happened.

### DONE (newest first; details in the numbered sections)
| Commit | What | Section |
|---|---|---|
| b40b181..c13811e | **Golden records and matching across any sources ("fix all and thoroughly").** Golden values by record date, automatic/chosen source trust, per-field trust and quality, from a form; customer columns carried; no-ID recall measured correctly (0.89); per-source data quality; incremental runs; awkward data shapes handled | 53 |
| bdd3186 | **Portal audit and fixes.** Three audits (flows, accessibility/design, human-in-the-loop) found ~70 problems, 11 blockers; all blockers fixed. Screen addresses with Back/Forward/reload; Review section (Review inbox, decide-and-next compare page with keys S/D/U/N/P and Undo, Decisions list with undo, split a person, correct a golden value, bulk decide, score filter, paging); Approvals with evidence; delete runs / projects / unused uploads (new permission `data.delete`); rename project; edit schedules; rules editor starts from defaults; newest-first audit log; accessibility (keyboard rows, labels, dialogs, focus, contrast; accent is now the brand teal); browser-tab icon | 52 |
| f89eb7f | Results tab and "Get the results" button on the run page | - |
| cd11c18 | **Scoring when a file has no identifiers** (the user's Trial file scored an obvious duplicate 48.6%): learns weights from the data itself (EM) when IDs cannot teach them, says which mode scored a run, odd inputs never break a run | 51 |
| 9ab777f | Aurionpro logo replaces "Dedupe" (sign-in page and top bar, both themes) | - |
| 7a1976f and earlier | Project workspace in six steps: private projects owning connections, run queue, run log, connection health, results export, schedules, local accounts and roles | 44-50 |

### NEXT (suggested order; confirm with the user before starting any of them)
1. **User reviews the portal again** (Ctrl+F5 first). Expect small usability fixes; collect them before building anything new. Quickest tour: Review inbox -> Start reviewing.
2. **Next accuracy work (optional):** pairs that share only a name (one record has the phone, the other the birth date) are the remaining misses without IDs; they need more data, not more rules (section 53 step 4). Golden-record stage is now the slowest part of an incremental run.
3. **Remaining review-section gaps** (section 52 "Not done"): assign/claim pairs to reviewers, ageing/SLA and reviewer statistics, comments on pairs; retire a connection or destination; search for connections and people; the Approvals page makes several requests per project (slow with many projects); light-theme users see a dark flash on load.
4. **Erasure and retention:** deleting runs, projects and uploads now exists. Still waiting for the user's three answers: are we the processor; may backups expire by retention; build only the machinery and leave the legal rules to customer configuration.
5. **Notifications** (email/chat) when a run fails or a schedule pauses.
6. **Never executed: Docker and PostgreSQL** (`tests/test_postgres_live.py` skips without a server; Docker Desktop was not running). Portability is a hard requirement.
7. **Gates before any real data:** compliance opinion on Aadhaar handling, independent penetration test, legal review (DPDP, RBI, IRDAI, UIDAI, CERT-In), incident procedures; replace the admin / admin demo account; consider switching back to single sign-on.
8. **Governance choices to confirm with a real customer:** second approval OFF by default for portal-created projects; `admin` and `analyst` export unmasked results; who holds `data.delete`; bulk "same person" merges without opening each pair (a reason is required).

**Where things are described:** engine and accuracy (sections 1-35), API security baseline (36, `docs/SECURITY.md`), encryption at rest (41), sources and mapping (42), workspace (44), members-only projects (45), local accounts and roles (46), project-owned work (47), run queue and health (48), results export (49), schedules (50), scoring without identifiers (51), portal audit and Review section (52).

**Run the portal (Windows, project root):** `$env:DEDUPE_AUTH_MODE="local"; $env:DEDUPE_LOCAL_AUTH_SECRET="<32+ characters>"; $env:DEDUPE_WORK_DIR="data/workspace"; $env:DEDUPE_DATA_ROOTS="$PWD\data"` then `.venv\Scripts\python -m dedupe.api --port 8765`; open http://127.0.0.1:8765/ui. New install: create an administrator with `.venv\Scripts\python -m dedupe.api.accounts_cli create-admin --username admin`. The demo workspace in `data/workspace` (ignored by git; rebuild with `scripts/demo_workspace.py`) has the DEMO account admin / admin (password policy bypassed on purpose). It now also holds the user's own "Trial" project (their uploaded Excel file, three runs): do not wipe `data/workspace` without asking.

**Check the live portal after UI changes:** start the portal, then `.venv\Scripts\python scripts\ui_walkthrough.py` and (a minute later, sign-in rate limit) `scripts\ui_walkthrough_more.py`. Both need Playwright in the venv (dev tool only) and fail on any console error, server error or red message.

**Decisions of the user (do not re-ask):** projects private to their creator for now (sharing later); each project has its own connections (copying between projects is enough); several people use one deployment, each in their own space; results leave as Excel, CSV, Parquet or a database table (new, add to existing, replace); scheduled runs wanted; "data can come in any form, handles must exist" (section 51); a dedicated human-in-the-loop section, deleting runs, back-and-forth navigation and accessibility for every feature (section 52); company branding is Aurionpro; may switch back to single sign-on later.

**Practical pitfalls added this session:** the portal's UI tests forbid reading the address bar except in `readAddress`/`writeAddress` and forbid `::after`, emoji and extra UI files (only index.html, app.js, style.css and the whitelisted PNGs are served); judgment times (Z, microseconds) and run times (+00:00, seconds) must be compared as datetimes, never as text; long inline Python in the Bash tool still breaks: write scripts with the Write tool.

### Older notes from 2026-10-05 (partly superseded by the block above)

**Older next steps (items 0-2 and 4 are still open; item 3, the product layer, is done)**
0. Accuracy status (section 31): most remaining error is information-poor records (same name, nothing else), which no blocking rule can fix safely; age evidence was added and, after the code-review fixes (section 32), measured at 35k records as +0.6 F1 before review, +0.8 after full review, 72 -> 44 wrong merges, for about 30% more review items. Further gains need NEW SIGNALS or real data: e.g. transliteration-aware name matching, household context as evidence for individuals, document images (Phase 3), or a learned blocker/matcher trained on steward decisions.
1. Try Docker again (`docker info`; Docker Desktop must show "Engine running"; if stuck, `wsl --shutdown` then reopen, or reboot). If it works, `docker compose run --rm --build app` must pass the tests inside the container; fix the Dockerfile as needed. Portability is a hard requirement, so Phase 0 is not truly done until this passes.
2. Memory for millions: peak is now 4.6 GB at 694k records (section 23). The remaining big holder is clustering in pure Python (dicts/lists per record, ~2 GB left after it). Linear extrapolation gives ~30 GB at 5M records, too much for one machine: move clustering to array-based union-find (NumPy) or DuckDB before a multi-million test.
3. Phase 2 (product layer): DONE 2.1-2.5, 2.3, 2.6a-2.6d (API complete), 2.7 thin screens (no sources/mapping screen yet, not tried in a real browser). Encryption at rest DONE for pipeline files (section 41; database and inputs still need volume/database encryption). NEXT 2.7 thin web UI; then encryption at rest (gate before real data) and S3 connector. Postgres persistence of outputs moved to Phase 4. Confirm with the user before starting.
4. Phase 3 documents (OCR / MRZ / face) and Phase 4 hardening (see roadmap).

**Review queue (sections 19-20):** FIXED. At 694k records the queue went from 246,948 pairs of people (13% real) to 14,543 (87% real) via a Senzing-style minimum-evidence rule (`not_enough_evidence`) and dropping the name+city blocking rule; F1 before review rose 0.906 -> 0.922. Cost: ideal-steward ceiling F1 0.956 -> 0.941 (matches only findable by checking ~232k mostly undecidable name+city pairs).

**Where the numbers stand (synthetic; sections 10, 14-18):** naive baseline F1 0.585; full pipeline person-cluster pairwise F1 about 0.91 (precision 0.99, recall 0.84), auto-merge precision 99.7%, households (family-only) precision 0.98 / recall 0.54 at threshold 5; ideal-steward ceiling F1 0.97. On held-out seeds and noise levels F1 is 0.86-0.93; at 692,537 records F1 is 0.917 (section 18).

**Known gaps and honest limits to carry forward**
- All results are on synthetic data generated by me; the generator and the cleaners share my assumptions about Indian names and addresses (optimistic). There is no real data or design partner. L2 (0.2), thresholds and household evidence weights were tuned on synthetic seeds 42/44/1; the benchmark uses held-out seeds 101-103.
- Term-frequency adjustment: lowers F1 before review on small data but prevents ~650 wrong auto-merges at 694k records (section 22); kept on.
- The gender veto is safe only because synthetic data has no gender errors. Date logic (twin guard, age -> birth year, future-date checks) is relative to today, or to `DEDUPE_REFERENCE_DATE` when pinned (section 32).
- Age evidence assumes a stated age is right 93% of the time (m = 0.93) and the synthetic age noise is mild (+-1 year): unverified on real leads.
- Recall ceilings: blocking reaches 94.5% transitive completeness; ID-less leads and KYC-document-only records are hard to link; 46% of true family pairs have no shared evidence at all.
- At scale the review queue is large (57k pairs at 137k records, 42% real): the steward UI needs prioritisation and bulk actions.
- Weak household evidence (named relative, father name, email, surname) loses reliability as the population grows; only strong evidence (policy, same home, shared phone) stays at about 99.8%.
- Staging files hold PII unencrypted (names, PAN, phone, email, address). Raw Aadhaar is erased after standardisation, but the source systems (and the fake `data/synthetic`) contain it. Encryption, access control and audit logging are Phase 4. Legal review (DPDP Act 2023, RBI KYC, IRDAI, UIDAI) has not been done.
- Not verified against real systems: Postgres/MySQL (only SQLite via SQLAlchemy), Google Sheets (mocked), cloud warehouses.
- Batch only; no incremental runs; single machine; documents (images / OCR / face) not started.
- The scoring stage got slower after chunking (3 s to about 6 s at 8k records) because random-pair counting runs 9 queries per chunk: optimisable.

**How to run everything** (Windows; project root `C:\Users\chandresh.kanyal\Desktop\Dedupe`; use the project venv, prefix each with `.venv/Scripts/python`):
`-m pytest -q` | `-m dedupe.synthetic --households 1200 --seed 42 --out data/synthetic` | `-m dedupe.pipeline --sources configs/synthetic_sources.json --truth data/synthetic` | `-m dedupe.benchmark --out docs/BENCHMARK.md` | `-m ruff check src tests`. Individual stages: `dedupe.ingest`, `dedupe.standardise` (+`.quality`), `dedupe.blocking`, `dedupe.scoring`, `dedupe.decision`, `dedupe.clustering`, `dedupe.households`, `dedupe.evaluation baseline|score`.

**Working agreements with the user (follow these)**
- Go step by step. Before each phase give a SHORT plan, then do the work, then a SHORT report. Explain what / why / how in plain language; the user is new to the dedup domain. Keep chat output short: tokens should go into doing the work, not into extra text. Ask only when genuinely blocked.
- Be honest: report failures, weaknesses and caveats (several of my own bugs and flaws were found and disclosed this way). Never present synthetic results as real-world accuracy.
- Update this file and commit after every step (git commit messages end with the Co-Authored-By line given by the harness). Everything must stay portable, modular and containerisable.

**Practical pitfalls learned (save time)**
- The Bash tool mangles backslashes and long heredocs: write scripts with the Write tool (or use forward slashes in paths) and run them with `.venv/Scripts/python <file>`; never edit `src/` while the full test suite is running (subprocess tests import it fresh), new files are safe.
- A class that defines a method called `list` (or another builtin name) breaks `list[str]` annotations written after it in the same class: name such methods differently (`list_users`, `for_project`).
- The full suite takes about 6 minutes: run it in the background and wait for the notification; run the affected test files first. `tests/test_postgres_live.py` skips without a server.
- The demo workspace is rebuilt with `scripts/demo_workspace.py` (wipes `data/workspace`, about 2 minutes); its administrator is admin / admin on purpose.
- In the Bash tool, long or multiple heredocs can fail with "unexpected EOF": use the Write tool for files and apply edits with a small script file or the Edit tool. A single short `python - <<'PYEOF'` patch usually works.
- Windows Python cannot see Git-Bash `/tmp`; use the scratchpad directory or project-relative paths. A `cd` inside a command persists: start commands with `cd /c/Users/chandresh.kanyal/Desktop/Dedupe`.
- `from dedupe.scoring import train` is a FUNCTION (the package re-exports it and shadows the submodule); get the module with `importlib.import_module("dedupe.scoring.train")`. Same pattern for `dedupe.standardise.run`.
- When stage code changes, re-run standardise -> blocking -> scoring -> decision -> clustering -> households (the pipeline command does it all).
- Record IDs sort as text: zero-pad IDs in tests so `rid` equals the loop index.
- `data/` is gitignored (synthetic data, staging, runs, models); regenerate with the commands above.

**Section 18 template (fill in after the big run and the benchmark regeneration):** pipeline runner (`dedupe/pipeline.py`: one command, per-stage time + peak memory, Aadhaar redaction, `DEDUPE_ENV=production` refuses the dev token key, `cluster_guard` switch); tests (`tests/test_pipeline.py`: artifacts, no raw Aadhaar in any parquet/json output, reproducible cluster assignments, key enforcement); chunked pair-level scoring (peak memory 1.4 GB -> 0.37 GB at 8k records); benchmark harness (`dedupe/benchmark.py`); ablation findings; big-run results; lint config (ruff E,F,I,B; E501/E741 ignored); README.

## 1. Project goal

A B2B platform that sweeps multiple customer data sources (warehouses, silos), finds records that refer to the same customer, groups them, and produces a single **golden (master) record** per customer. Documents/images (government IDs) are additional evidence for matching.

- **Target domains:** insurance and banking
- **Region:** India primarily (PAN, Aadhaar, passport, DL, voter ID; Indian names/addresses)
- **Mode:** batch sweeps (real-time later)
- **Scale:** PoC now, must be robust to millions of records. Single machine today; multi-machine later.
- **Hard constraint:** CPU only (no GPU). Fast, accurate, efficient.
- **Approach:** hybrid. Deterministic rules + probabilistic scoring (Fellegi-Sunter / Splink) + small CPU ML reranker later.
- **Key product feature:** business users define their own matching rules after ingestion (auto-merge, send-to-review, never-merge/veto), with dry-run impact preview, versioning and audit.

## 2. Non-negotiable engineering principles

1. **Portable:** the whole project runs on any machine/instance via Docker. No host-specific paths or global installs. Config via env vars / config files.
2. **Modular:** stages are independent packages with clear interfaces; each can be swapped, tested and later scaled separately.
3. **Stateless stages over files:** pipeline stages read/write Parquet, so work can be sharded across machines later.
4. **Explainable:** every merge must be traceable to rule/score + evidence. Merges must be reversible (unmerge).
5. **Conservative matching:** false merges are worse than missed duplicates.
6. **Sensitive data:** never store raw Aadhaar; tokenise/hash. Treat PII carefully (DPDP Act 2023, RBI KYC, IRDAI, UIDAI rules; legal points to be verified with counsel).
7. **Measure everything:** an evaluation harness with ground truth gates every change.

## 3. Architecture (as built in Phase 1)

```
sources -> ingest -> standardise -> block -> score -> decide -> cluster -> households
(CSV, XLSX,  connectors  names, phones,  union of    per-field   auto/review/  one group   families as
 Parquet,    + mapping   PAN, Aadhaar    SQL rules   evidence    reject +      per person, relationships
 SQL, Sheets) -> canonical token, dates, + freq cap   -> prob,     never-merge   contradiction between people
                          addresses                  explainable vetoes, JSON  guard
                                                                  business rules
```
Every stage reads/writes Parquet; `python -m dedupe.pipeline` runs all of them; each stage also runs alone (`python -m dedupe.<stage>`).

Code map (`src/dedupe/`): `connectors/` (plugin interface + csv/xlsx/parquet/sql/gsheet), `mapping/` (canonical schema, mapping JSON -> SQL, auto-suggest), `ingest.py`, `standardise/` (names, identifiers, dates, addresses, geo, verhoeff, quality report), `blocking/` (rules, generate, report), `scoring/` (comparisons = level ladders in SQL, levels, train, predict, evaluate, model), `decision/` (conditions DSL, config, apply, report), `clustering/` (graph, run), `households/` (evidence, group, run), `evaluation/` (metrics, loaders, naive baseline), `synthetic/` (generator + ground truth), `pipeline.py`, `benchmark.py`, `config/settings.py`. Configs in `configs/` (`synthetic_sources.json`, `decision_default.json`). Tests in `tests/` (81). Docs: `README.md`, `docs/BENCHMARK.md`, this file.

Stack actually used: Python 3.10 (3.12 in the Dockerfile), DuckDB (compute/SQL), PyArrow/Parquet, NumPy (logistic regression, graph logic in pure Python), Pydantic, SQLAlchemy, openpyxl, psutil, ruff/pytest. NOT used (despite earlier plans): Splink (replaced by own SQL level ladders + logistic model, see section 14), rapidfuzz/libpostal/phonenumbers (own cleaners; DuckDB jaro_winkler), polars (installed, unused).

Still planned (not built): ONNX Runtime + PaddleOCR (documents), FAISS/hnswlib (optional face vectors), Postgres persistence + FastAPI + web UI (Phase 2), `survivorship/`, `documents/`, `store/`, `api/` package stubs exist but are empty.

## 4. Environment (this machine)

- Windows 11 Pro; shells: PowerShell and Git Bash. 8 CPUs, 31.7 GB RAM, ~31 GB free disk at the time of writing.
- Python 3.10.11 in the project-local `.venv` (all development and test runs happen there). Git repository on branch `main`, many commits (one per step).
- Docker 29.4.3 is installed but the Docker Desktop engine was NOT running/healthy all session (containers stayed in "Created", `wsl` hung), so the Docker setup is **unverified**.
- Large generated data lives in `data/` (gitignored).

## 5. Roadmap

### Phase 0 — Foundation (portability + modularity)
- [x] 0.1 PROGRESS.md + CLAUDE.md pointer
- [x] 0.2 Git init, .gitignore, .dockerignore
- [x] 0.3 Project skeleton (`src/dedupe` package, pyproject.toml, tests dir)
- [x] 0.4 Dockerfile + docker-compose (app + Postgres), `.env.example`
- [~] 0.5 Smoke tests pass in local `.venv` (3 passed, 2026-10-05). Container run still unverified — **BLOCKED**: Docker Desktop engine not running / containers hang in "Created" (WSL likely stuck). Needs Docker Desktop restart / PC reboot, then re-run `docker compose run --rm --build app`. Not yet verified.
- [x] 0.6 README written (`README.md`); the Docker part of it is unverified (see 0.5)

### Phase 1 — Core engine
- [x] 1.1 Synthetic Indian customer generator with ground-truth duplicates — see section 9. Run in `.venv`: `python -m dedupe.synthetic --households 1200 --seed 42 --out data/synthetic`
- [x] 1.2 Evaluation harness (pairwise precision/recall/F1, false merges split same-family vs cross-family, per-hard-case breakdown) — `src/dedupe/evaluation/`; run `python -m dedupe.evaluation baseline|score --data data/synthetic [--pred file.csv]`. Prediction file format: CSV `source,source_record_id,cluster_id`.
- [x] 1.3 Connector plugin interface + schema-mapping layer (see 7a and section 11). Connectors: CSV/TSV, XLSX, Parquet, generic SQL (SQLAlchemy), Google Sheets. Run: `python -m dedupe.ingest --config configs/synthetic_sources.json`
- [x] 1.4 Standardisation (names, phones, PAN/Aadhaar validators, addresses/PIN) — see section 12. Run: `python -m dedupe.standardise`; report: `python -m dedupe.standardise.quality`
- [x] 1.5 (done, see section 13) Candidate generation (blocking): UNION of several rules (strong-ID equality; phonetic surname + DOB; first-name + DOB; DOB + PIN prefix + initial...). **Frequency cap**: values shared by too many records (family phones, common names) are not used as keys. Measure pairs completeness (true pairs kept), reduction ratio and pairs generated, per rule.
- [x] 1.6 (done, see section 14) Pair scoring: ordered level ladders per field in SQL; fixed weights for identifiers; L2 logistic regression fitted on identifier-seeded pairs for everything else (deviation from plain Splink Fellegi-Sunter, reasons in section 14); term-frequency adjustment; per-field contributions kept for explainability
- [x] 1.7 (done, see section 15) Decisions + hard constraints: score -> auto-merge / review / reject zones; **never-merge vetoes** (two different valid PANs or Aadhaar tokens, conflicting gender, large DOB gap, twin guard). This is the seed of the Phase 2 rules engine.
- [x] 1.8 (done, see section 16) Clustering: only auto-merge edges form clusters; connected components + **conflict check that splits clusters** + size alerts (no black-hole entities); review pairs stay unmerged. Add cluster-level metrics (purity, B-cubed).
- [x] 1.9 (done, see section 17) Household/relationship layer: families are NOT merges. A separate graph of 'related' links between resolved people (shared address, phone, nominee/co-applicant, father name, surname) -> household groups. Evaluate against household truth.
- [x] 1.10 (done, section 18) Final Phase 1 benchmark + hardening. DONE: single-command pipeline (`python -m dedupe.pipeline`), Aadhaar redaction + production key enforcement, reproducibility/privacy tests, benchmark harness with held-out seeds / noise / size / ablation (`docs/BENCHMARK.md`), chunked scoring (memory 1.4 GB -> 0.37 GB), lint clean, README. Big run (692k records) and regenerated benchmark recorded in section 18.

### Phase 2 — Rules, golden record, review, API (product layer)
- [~] 2.1 Rule schema (JSON): auto-merge / review / never-merge — DONE in Phase 1.7 (`dedupe.decision`: safe condition language, `DecisionConfig`)
- [x] 2.2 (done, section 33) Rule → SQL compiler; truth-free dry run of a decision config on the last run's scored pairs (`dedupe.governance impact`): exact zone changes, per-rule changes, example pairs
- [x] 2.3 (done, section 33) Versioned, audited config: decision rules + thresholds, golden-record rules, matching settings (blocking rules on/off, caps, household threshold, cluster-size limit, queue limits); draft -> approve (four eyes) -> activate, rollback as a new version, hash-chained append-only log, every run records the versions it used. NOT yet versioned: household evidence weights (still code constants), source mappings, blocking rule definitions (only on/off)
- [x] 2.4 (done, section 24) Survivorship rules + golden record + lineage (per-field rules; keep which source record supplied each value; canonical.parquet `attributes` JSON carries source-specific fields)
- [~] 2.5 Review workflow, revised after research (section 25). DONE: grouped, ranked queue per pair of people (`dedupe.review`).
  - [x] 2.5a (done, section 26) Stable person IDs across runs (new clusters inherit the previous run's ID by record overlap; today `cl00000012` changes every run)
  - [x] 2.5b (done, section 27) Append-only judgment store (SQLAlchemy: SQLite for dev/tests, Postgres in compose): same_person / different_person / unsure / split / field_override, keyed by (source_id, source_record_id), with who, when, why, rule/model version and an evidence snapshot
  - [x] 2.5c (done, section 28) Judgments applied inside clustering: must-link edges, cannot-links (steward beats automation; an ID conflict (two different PANs) needs a stronger confirmation), contradiction report
  - [x] 2.5d (done, section 29) Review service: queue, explain (why / why-not from the stored per-field contributions), approve / reject / unsure / split / undo, bulk actions, golden-field overrides; optional four-eyes approval
  - [x] 2.5e (done, section 30) Simulated-reviewer test harness: review-budget curves (F1 vs reviews) for ranking strategies, property tests (idempotent, adding a cannot-link never merges, IDs stable, unmerge never re-merges)
- [~] 2.6 API (FastAPI) over the above, REVISED after research (sections 34-35). 2.6a DONE (section 36), 2.6b DONE (section 37): security baseline + authentication + roles (OIDC/JWT, dev tokens, authorization matrix tests, security log, request limits, CI security scanning, threat model `docs/SECURITY.md`, connector hardening, Aadhaar reference keys). Remaining: 2.6c write endpoints (judgments, config governance) acting as the authenticated user, 2.6d run jobs (202 + status + idempotency key). Postgres persistence of pipeline outputs MOVED to Phase 4 (with incremental matching)
- [ ] 2.7 Thin web UI: source connection + mapping (use `dedupe.mapping.suggest`), rule editor with dry-run, review queue, golden record viewer

### Phase 3 — Documents
- [ ] 3.1 Doc classification, OCR (PaddleOCR), MRZ parsing, validators
- [ ] 3.2 Extracted fields feed the matching pipeline
- [ ] 3.3 Optional: face embeddings + FAISS (flagged as biometric/sensitive)

### Phase 4 — Hardening
- [ ] 4.1 Scale test at 5–10M synthetic records
- [ ] 4.2 Incremental runs
- [ ] 4.3 Encryption / tokenisation of sensitive identifiers, access control
- [ ] 4.4 Multi-machine sharding plan

## 6. Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-10-05 | Python + DuckDB + Splink core | Proven CPU performance (~1M records/min on a laptop per Splink docs); scales to Spark later |
| 2026-10-05 | Individuals only in PoC | Business entities (GSTIN) need different logic; add later |
| 2026-10-05 | Aadhaar matched via salted hash/token only | Regulatory restriction on storing raw Aadhaar |
| 2026-10-05 | Face matching is an optional module | Biometric sensitivity; core engine must work without it |
| 2026-10-05 | Rules stored as data (JSON), compiled to SQL | Business-user configurability at engine speed |
| 2026-10-05 | Containerised + modular from day one | User requirement: easy to move to another device/instance |
| 2026-10-05 | Connectors are a plugin interface + canonical schema mapping | Requirement: accept any source; the list is unbounded |
| 2026-10-05 | Develop in project-local `.venv` while Docker is broken on the dev host; Docker files kept for deployment | Docker engine failing on this machine; must not block progress |
| 2026-10-05 | Revised 1.5-1.10 after researching industry tools (Splink, dedupe/Zingg, Senzing, Reltio/Informatica, ER literature) | Adopt: union blocking + frequency caps, TF-adjusted Fellegi-Sunter, auto/review/reject zones, veto rules, conflict-aware clustering, families as relationships not merges |
| 2026-10-05 | Synthetic data only for now | No design partner / real data available |
| 2026-10-05 | Scoring: own SQL level ladders + identifier-seeded logistic regression instead of Splink EM | EM drifted on family pairs (contaminated m), random-pair u made shared address/phone look decisive (section 14) |
| 2026-10-05 | Logistic L2 = 0.2 | 2.0 over-shrank evidence on small data (section 16); tuned on synthetic seeds 42/44/1 |
| 2026-10-05 | Clustering is rule/graph based, no ML | Deterministic and traceable for audits; guard splits groups with conflicting PAN/Aadhaar or vetoed pairs |
| 2026-10-05 | Households are links between distinct people, default threshold 5 | Weak evidence stops being reliable at scale (section 17) |
| 2026-10-05 | Raw Aadhaar erased from canonical.parquet right after standardisation; `DEDUPE_ENV=production` refuses the dev token key | Data minimisation; tokens/last4 are enough for matching |
| 2026-10-05 | Pair-level scoring is chunked (400k pairs) | Peak memory 1.4 GB -> 0.37 GB at 8k records; needed for millions |
| 2026-10-05 | Benchmark on held-out seeds 101-103, noise 0.5/1/2, sizes 300/1.2k/5k households, ablations | Avoid reporting only on tuned data |
| 2026-10-05 | Ruff lint config: E,F,I,B; E501/E741 ignored; line length 160 | Long SQL/f-strings are deliberate |
| 2026-10-05 | Minimum-evidence rule type `not_enough_evidence` (soft reject, not a cannot-link), default rule in `configs/decision_default.json` | Senzing-style principles: name + location alone is not enough to merge or review; cut the review queue 94% at 694k records (section 20) |
| 2026-10-05 | `name_city` blocking rule moved to `OPTIONAL_RULES` (off by default) | 47% of candidates, superlinear growth, almost no surviving decisions; F1 rose slightly without it |
| 2026-10-05 | Force-review rule `review_possible_namesake` (fuzzy name + DOB only, score >= 0.9, no ID/contact/home corroboration) | Most efficient of the measured options against namesake false merges: 15 checks per prevented error vs 40+ for a higher auto threshold (section 21) |
| 2026-10-05 | Keep term-frequency adjustment ON | At 694k records it prevents ~650 wrong auto-merges for ~5k extra review items (~8 checks per error); the 8k-record benchmark, where it looked harmful, was too small (section 22) |
| 2026-10-05 | Pipeline DuckDB connection gets a memory cap (`PipelineConfig.memory_limit`, default 4GB) + spill directory, and each stage drops its working tables | Peak memory 17 GB -> 4.6 GB at 694k records (section 23) |
| 2026-10-05 | Golden record: per-field survivorship strategies (most trusted source / most recent / most complete / most frequent, ties fall back to trust then recency), address fields move as one block, every value keeps its source record (lineage) and source conflicts are flagged; settings in `configs/survivorship_default.json` | Business-editable, auditable; validated on synthetic truth (section 24) |
| 2026-10-05 | Golden names come from the standardised clean name chosen by agreement, not the longest raw string; placeholder / year-only birth dates are never golden values | Longest-wins picked noisy forms ("Sri Mahes Krushnan", "Parekh, Kiran Bhhai"): first-name accuracy 60% -> 87% |
| 2026-10-05 | Steward decisions are stored as an append-only judgment log keyed by source record keys, and replayed as must-link / cannot-link constraints on every run (not as edits to cluster tables) | Industry pattern: nomenklatura judgment graph, Informatica excludes unmerged records from re-merging, patent on re-clustering with only the steward link removed (section 25) |
| 2026-10-05 | Persistent person IDs across runs become a prerequisite for the review workflow | Cluster IDs change every run; Senzing / Informatica keep stable entity IDs |
| 2026-10-05 | Person IDs (`P` + 9 digits, column still called `cluster_id`) persist across runs through a registry folder `<work>/identity/` (records, persons, events parquet files): greedy largest-overlap inheritance, older ID wins a merge, larger part keeps the ID on a split, retired IDs never reused | Review decisions and downstream systems need IDs that keep meaning the same person (section 26) |
| 2026-10-05 | Judgment log (`dedupe.store.JudgmentStore`): append-only (DB triggers refuse UPDATE/DELETE), SHA-256 hash chain for tamper evidence, undo / supersede / approve / decline as new rows, optional four-eyes per decision; SQLite for dev and tests, PostgreSQL via `DEDUPE_DATABASE_URL` | Audit-grade steward decisions that survive re-runs (section 27) |
| 2026-10-05 | Steward decisions enter clustering as heavy edges (same person: weight 2.0 + id*1e-9, newest wins on conflict), hard separations (different person, split) and glue edges inside split parts (weight 1.5); a same-person link across different valid PANs/Aadhaar is refused and reported unless confirmed (`confirm_id_conflict`, needs a reason) | Steward beats automation except hard identifier conflicts (section 28) |
| 2026-10-05 | A retired person ID is reactivated when its last records come back together (Jaccard >= 0.5), so undoing a merge restores the original IDs; it never goes to a different group of records | Informatica-style unmerge behaviour; found by the revoke test (section 28) |
| 2026-10-05 | Review service (`dedupe.review.ReviewService`) is a plain Python layer over the run outputs and the judgment store; the API (2.6) and UI (2.7) will only wrap it. Reject / unsure record EVERY review pair between two people (max 500), approve records the best pair; every action has an `action_id` so it can be undone as a unit | Person-level decisions must survive the next run and be undoable (section 29) |
| 2026-10-05 | Golden field overrides from stewards are applied inside the golden record (strategy `steward_override`, source `steward`, newest wins, null clears); identifier fields (pan, aadhaar, dob) need a second approver by default | Reltio-style steward survivorship choice, with a safety net for identifiers |
| 2026-10-05 | `possible_matches.parquet` now carries record keys and per-field evidence of each review pair, ordered by person pair | The review screen must not scan millions of scored pairs per click (bulk action 35 s -> 10 s per 300 items) |
| 2026-10-05 | Review queue priority = probability + 0.05 (rule-forced) + 0.02 ln(1+record pairs) + 0.2 ln(size_l x size_r) (`SIZE_WEIGHT`) | Simulated-reviewer study: near-best on pairwise F1 (like expected gain) AND on customers recovered exactly (like the old order); random / probability-only / most-uncertain-first were worse at moderate budgets (section 30) |
| 2026-10-05 | Age (band) is evidence: canonical fields `age` and `record_date`; standardised `birth_year_min/max` (exact birth year, else age counted back from the record's year, one year of slack); DOB ladder gets `age-based: compatible` / `incompatible` (two years of tolerance), with FIXED weights log(m/u): m assumed 0.93 / 0.07, u measured among age-only random pairs | Leads carry only an age; it was ignored (section 31) |
| 2026-10-05 | New blocking rule `name_city_age` (name sounds + city + overlapping 5-year birth-year bucket); `name_city` stays optional/off | Recovers real matches that name_city used to find, at a fraction of the candidate pairs |
| 2026-10-05 | Rules: `not_enough_evidence` also accepts name + same city + compatible age BUT ONLY at score >= 0.8; new force-review rule `review_age_only_evidence` (score >= 0.9, age-only evidence, no contact/address/ID agreement) with `priority_offset` -0.3 so it ranks after more reliable items | At 139k records age-only pairs below 0.8 were 2-8% real; at 0.9+ 67% real (section 31) |
| 2026-10-05 | Decision rules can carry `priority_offset` (force_review only), written to `decisions.parquet` as `rank_offset` and used by the queue ranking | Rule-forced items differ in reliability |
| 2026-10-05 | Queue `SIZE_WEIGHT` 0.2 -> 0.05 | 0.2 was best at 35k records but worse than 0 at 139k; 0.05 is neutral-to-better at both (lesson: check ranking choices at 100k+ records) |
| 2026-10-06 | Work is organised in PROJECTS (own decisions, person IDs, rules); CONNECTIONS (source + mapping) are shared, versioned and approved by a second person; a RUN executes a project over chosen live connections into its own folder | User decision: separate ad-hoc runs (one Excel file) from the customer master, review any past run, start new runs (section 44) |
| 2026-10-06 | Files can be uploaded from the browser; they are checked (content type, zip bombs, header, size, rows), converted once to a Parquet table, stored encrypted, and the original bytes deleted | User decision; avoids keeping raw spreadsheets on disk |
| 2026-10-06 | Dark theme by default with a light switch; the theme is the only thing kept in browser storage | User decision |
| 2026-10-07 | Local username/password sign-in exists as an OPT-IN mode (`DEDUPE_AUTH_MODE=local`); production refuses it unless `DEDUPE_ALLOW_LOCAL_AUTH=yes`; OIDC stays the recommended route and the user may switch back to it | User asked for a simple login for testing and demos; banks use single sign-on (section 46) |
| 2026-10-07 | A built-in `admin` role holds every permission; custom roles are admin-defined; nobody can grant more than they hold, change their own roles, or remove the last user manager | User: an admin who creates users and decides who gets what (section 46) |
| 2026-10-08 | Projects are private to their creator (owner) from creation; sharing is by adding members; administration (`projects.all`) still sees every project | User: a person sees all details of THEIR projects, sharing later (section 47) |
| 2026-10-08 | Connections, uploads and database destinations belong to a project (not shared); copying a connection between projects is enough for now | User and industry research: Tamr, AWS Entity Resolution, Airbyte keep sources inside the project/workspace (section 47) |
| 2026-10-08 | `second_approval` is a per-project setting, OFF for projects created in the portal, ON for engine-created and pre-existing ones | A one-person project cannot approve its own changes; regulated customers will want it on: CONFIRM with a real customer (section 47) |
| 2026-10-08 | Runs queue (default 1 at a time) instead of being refused; scheduled runs are ordinary runs started by an in-process scheduler with atomic claiming | Several people use one deployment; user wants schedules now (sections 48, 50) |
| 2026-10-08 | Results leave unmasked behind `results.export`, a reason, a rate limit and an audit row with counts only; database writes only to the project's approved destinations (an environment variable name), all-or-nothing | User wants Excel/CSV/Parquet and database table options; personal data leaving needs controls (section 49) |
| 2026-10-06 | API identity comes from the customer's identity provider (OIDC); verified `sub` is the `actor` in every audit log; six fixed roles with separated duties; dev shared-secret tokens exist only for tests/demos and are refused in production | Banks use SSO; no user database or passwords to protect; four-eyes becomes real (section 34-36) |
| 2026-10-06 | Aadhaar: customers may supply their own vault reference key (`aadhaar_reference_key`, stored as `ref:<key>`); the full number remains supported but is not the recommended route | UIDAI vault guidance rejects hashing as a reference-key method (section 35); real Aadhaar numbers still need a compliance opinion |
| 2026-10-06 | Connector configs are untrusted: file paths confined to `DEDUPE_DATA_ROOTS`, one read-only SQL statement, validated sheet ids, redirects only to Google, no token to other hosts | Configs will come from a web screen/API |
| 2026-10-06 | bandit B608 (f-string SQL) and B105 skipped by configuration after an audit; every other bandit rule runs | caller values are bound parameters / quoting helpers / the rule language; B105 only matched a column name |
| 2026-10-06 | Configs are versioned per name (decision / survivorship / settings) in an append-only, hash-chained `config_log` table in the same database as the judgments; a version goes live only after approval by someone other than its proposer (`solo` switch for development); rollback publishes the old content as a NEW version | Industry practice (immutable versions, draft vs published, who/what/when/why) and the audit need of regulated customers (section 33) |
| 2026-10-06 | A pipeline run uses the ACTIVE store version when one exists, else the file, and always records name, version and content hash in `pipeline_summary.json` (`configs`) and copies of the configs in `<work>/config_used/`; reviewer evidence snapshots carry the same hashes | A decision must be explainable and reproducible later ("which rules produced this?") |
| 2026-10-05 | 'Today' comes from the clock (or `DEDUPE_REFERENCE_DATE` for reproducible re-runs), not a hard-coded 2026-10-01; twin guard uses a relative condition (`both_born_within_last` 18) | Fixed dates silently go stale as years pass (code review) |
| 2026-10-05 | An age without a usable record date gets 3 extra years of slack on the early side | The age may have been stated years ago; a stale age must not be scored as a contradiction |
| 2026-10-05 | `name_city_age` blocking only pairs records where at least one side has no birth year (new `Rule.one_side`) | Two dated documents are covered by the DOB rules; without this the rule adds namesakes that grow with the population |
| 2026-10-05 | `review_age_only_evidence` also requires a strong name and the same city/PIN | Otherwise force_review (higher precedence) overrode the minimum-evidence reject |
| 2026-10-05 | Removed the unused `Comparison.blocked_by` field | Dead and out of date; the trainer is identifier-seeded, not EM |
| 2026-10-05 | Synthetic generator: email addresses unique per person | Bug: unrelated namesakes got identical mailboxes, which cannot happen; deliberate family sharing kept as a trap. All synthetic numbers shifted slightly |

## 7. Open questions

- Docker: does the container build and pass the tests once Docker Desktop is healthy? (blocks calling Phase 0 done)
- Golden-record survivorship defaults per field (most recent / most trusted source / most complete / most frequent): needs a business answer; propose defaults in Phase 2.
- Steward workflow: who approves, how are decisions audited, how do they survive re-runs (proposal: must-link / cannot-link constraints).
- Real-data pilot: any design partner or anonymised sample (even a few hundred labelled pairs) would validate or break the synthetic-data conclusions.
- Legal review of DPDP Act 2023 / RBI KYC / IRDAI / UIDAI handling before any real data is processed.
- Web UI timing and stack (after the Phase 2 API; assumed thin web app).

### Answered
- **Real data sample?** No design partner and no real data. All development and benchmarking uses synthetic data with known ground truth (Phase 1.1). Real-world accuracy is therefore unproven until a pilot; the synthetic generator must be deliberately nasty (realistic Indian name/address noise, family-shared attributes) and we must say so when reporting results.
- **Which sources?** The platform must accept data from any source: Excel, spreadsheets, Google Sheets, RDS, Postgres, MySQL, and any other data platform. See connector design below.

## 7a. Connector design (from the "any source" requirement)

"Any and all" is unbounded, so connectors are a **plugin interface**, not a fixed list.

- **Interface:** every connector implements `test_connection()`, `discover_schema()`, `read(batch)` → Arrow/Parquet. Connectors are registered by name, so new ones drop in without touching the core.
- **Generic fallbacks cover most of the long tail:**
  - SQL databases (Postgres, MySQL, SQL Server, Oracle, AWS RDS/Aurora, Redshift, Snowflake, BigQuery…) via SQLAlchemy dialects, with DuckDB scanners / ConnectorX for fast bulk reads where available.
  - Files (CSV, TSV, XLSX/XLS, JSON, Parquet) via DuckDB/Polars; object storage (S3, GCS, Azure Blob) via fsspec.
  - Google Sheets via the Sheets API (OAuth/service account).
  - Later: REST/API sources, ODBC/JDBC bridge, CDC streams.
- **Schema mapping layer:** each source's columns are mapped to a **canonical customer schema** (name, DOB, phone, email, address, PAN, Aadhaar-token, ids…). Mappings are saved config, with auto-suggest from column names and sample values. Everything downstream only sees the canonical schema.
- **Provenance:** every row keeps `source_id` + `source_record_id` so lineage and unmerge work.
- **Credentials:** never stored in config files or the repo; supplied via env vars / a secrets backend; read-only DB users recommended.
- **Incremental reads:** watermark/updated-at where available; full re-scan otherwise.
- **Build order:** files (CSV/XLSX/Parquet) → Postgres/MySQL via SQLAlchemy → Google Sheets → cloud warehouses → the rest.

## 8. Session log

- **2026-10-08 (session 1, step 4 of 6)** — Scheduled runs: hourly, daily or weekly per project in the schedule's own time zone, exactly-once claiming, one catch-up run after downtime, no pile-up, self-pausing after three failures or when the creator can no longer sign in. Section 50.
- **2026-10-08 (session 1, step 3 of 6)** — Results export: download golden records, person-to-record map and households as Excel, CSV or Parquet; write them into a database as a new table, added to an existing one, or replacing its rows; project database destinations approved like connections. Section 49.
- **2026-10-08 (session 1, step 2 of 6)** — Run queue (runs wait their turn and survive a restart), run log, rows per connection, connection health (test now, history from runs, column drift). Section 48.
- **2026-10-08 (session 1, project-owned work, step 1 of 6)** — Projects private to their creator, connections and uploads inside the project, copy between projects, one-person projects approve their own changes, per-project activity feed, additive schema upgrades, project workspace screens (Overview checklist, Connections, Runs, Rules, Activity, Access, Settings). Section 47.
- **2026-10-07 (session 1, accounts)** — Username/password sign-in (local mode), an admin holding every permission, users and admin-defined roles with delegation limits, Users and Roles screens, sessions ended at once on disable/reset. Section 46.
- **2026-10-07 (session 1, access and cancel)** — Members-only projects (business-unit scoping), run cancel, Postgres test written (not executed: no Docker/Postgres here). Section 45.
- **2026-10-06 (session 1, workspace)** — Product reorganised around projects, connections, uploads and runs (user's design request with Databricks screenshots): per-project decisions/person IDs/rules, approved connections shared by all projects, browser uploads stored encrypted, a run per project over chosen connections with its own results folder, dark workspace UI with icons, + New, search, home, run pages, add-data tiles, wizards, approvals and audit log. Section 44.
- **2026-10-06 (session 1, UI redesign)** — Screens redesigned as a flat, neutral workspace (top bar, grouped left navigation, plain tables, tabs, dialogs instead of browser prompts); screenshots checked in a real browser engine for the first time; a test pins the no-decoration rules. Section 43.
- **2026-10-06 (session 1, sources + mapping)** — Sources and field-mapping screen and endpoints: inspect a source (masked sample, suggested mapping), preview, propose with a deep check; `sources` is a versioned, audited, four-eyes configuration and runs use the ACTIVE version; 14 new tests (273 + 1 skipped). Section 42.
- **2026-10-06 (session 1, encryption at rest)** — `dedupe.crypto`: Parquet modular encryption (AES-GCM-256) for every file the pipeline writes, encrypted DuckDB spill files, key from the environment, wrong/missing/mixed key refused, convert/rekey tools, production refuses to start without a key; 8 new tests. Section 41.
- **2026-10-06 (session 1, 2.7)** — Thin web screens served by the API (`/ui`): review queue and comparison with decisions, people, approvals, rules with dry run, runs; found and fixed a hole in the authorization-matrix test (it never saw routes added with include_router); 4 UI tests. Section 40.
- **2026-10-06 (session 1, 2.6d)** — Run jobs: `POST /runs` (202), list/status, one run at a time in a separate process, reads 503 during a run, restart marks orphans interrupted, real end-to-end run test; 7 new tests (247 + 1 skipped). Section 39.
- **2026-10-06 (session 1, 2.6c)** — Write endpoints (decisions, bulk, undo, split, override, four-eyes approvals, config propose/dry-run/approve/decline/activate/rollback) as the verified caller; 11 new tests (240 + 1 skipped). Section 38.
- **2026-10-06 (session 1, 2.6b)** — Read endpoints (runs, queue, people, comparisons, configuration history) with allow-listed responses, role-enforced masking, logged and rate-limited unmasking; 10 new tests (229 + 1 skipped). Section 37.
- **2026-10-06 (session 1, 2.6a)** — API security baseline (`dedupe.api`): OIDC/dev token verification, role matrix, security log, request defences, safe errors; connector hardening; Aadhaar reference keys; security scanning (bandit, pip-audit, SBOM, gitleaks/trivy in CI files); non-root runtime image; `docs/SECURITY.md`; 55 new tests (219 + 1 skipped). Section 36.
- **2026-10-06 (session 1, security research)** — Researched API security standards, Indian regulator requirements (RBI, CERT-In, IRDAI, DPDP, UIDAI) and secure-development practice; section 35 (findings, gaps in our design, gates before real data). No code yet.
- **2026-10-06 (session 1, 2.6 research)** — Researched API authentication, roles, audit and long-running jobs; revised plan recorded in section 34 (no code yet).
- **2026-10-06 (session 1, 2.3)** — Versioned, audited configuration (`dedupe.governance`): config log, four-eyes approval, rollback, plain-language diffs, truth-free dry run, pipeline uses active versions and records them; blocking rules selectable by name; 11 new tests (164). Section 33.
- **2026-10-05 (session 1, code-review fixes)** — Fixed 10 review findings on the age work (generator bug, timestamps, stale reference date, rule precedence, old canonical files, label-keyed age weights, blocking namesakes, offset validation, age formats, dead field) plus the twin-guard cut-off; re-measured at 35k and 139k; regenerated benchmark and review simulation; 153 tests. Section 32.
- **2026-10-05 (session 1, accuracy: blocking and age)** — Diagnosed never-candidate pairs (information-poor records), added age evidence end to end, tuned rules at 35k and 139k, fixed a config-signature break in the pipeline CLI and benchmark (+2 entry-point tests), regenerated benchmark and review simulation; 146 tests. Section 31.
- **2026-10-05 (session 1, 2.5e)** — Simulated reviewer (`dedupe.review.simulate`, `docs/REVIEW_SIMULATION.md`), queue priority tuned, 7 property tests (138 tests), found that blocking misses are the main remaining error. Section 30.
- **2026-10-05 (session 1, 2.5d)** — Review service (queue, explain, approve / reject / unsure / split / override / undo, bulk, four-eyes policy, masking), golden overrides applied, enriched possible_matches; 131 tests. Section 29.
- **2026-10-05 (session 1, 2.5c)** — Judgments applied in clustering (`clustering/judged.py`, pipeline `judgments_url`), report of which decisions held, review queue skips decided pairs, ID reactivation after undo; 122 tests. Oracle-reviewer scale check. Section 28.
- **2026-10-05 (session 1, 2.5b)** — Judgment store (`dedupe.store`, 11 tests, 109 total): same/different/unsure/split/field-override + revoke/approve/decline, hash chain, append-only triggers. Section 27.
- **2026-10-05 (session 1, 2.5a)** — Stable person IDs (`dedupe.identity`, 8 tests + pipeline rerun test, 98 tests total); verified at 138k records. Section 26.
- **2026-10-05 (session 1, research before 2.5)** — Researched how Reltio, Informatica, Senzing, nomenklatura/OpenSanctions, Tamr/Quantexa-style tooling, AWS Entity Resolution and the research literature handle steward review, unmerge and persistence; revised plan 2.5 into 2.5a-e. Section 25.
- **2026-10-05 (session 1, Phase 2 start)** — Golden record + survivorship (`dedupe.survivorship`, pipeline stage `golden_record`, 6 tests), checked against synthetic truth and fixed twice (name source, placeholder dates). Section 24.
- **2026-10-05 (session 1, step 3 of fixes)** — Memory: per-stage memory metering, DuckDB memory cap + spill, stage tables dropped; 17 GB -> 4.6 GB at 694k. Section 23.
- **2026-10-05 (session 1, step 2 of fixes)** — Term-frequency re-check at 138k and 694k: kept on. Section 22.
- **2026-10-05 (session 1, step 1 of fixes)** — Namesake auto-merges: diagnosed at 694k, compared 6 options, added `review_possible_namesake` rule. Section 21.
- **2026-10-05 (session 1, later)** — Review queue fix: researched industry handling (Splink thresholds/prior, Senzing principles + generic values, Informatica filtered match rules, active learning); diagnosed queue composition; added `not_enough_evidence` rule type, made name_city blocking optional, fixed synthetic email uniqueness; grouped/ranked review queue (`dedupe.review`). Section 20.
- **2026-10-05 (session 1, final)** — Big run found a household-evidence blow-up (>16 GB, name joins and per-record surname check); fixed (popularity cap + per-person surnames), 692k records now run in 7.4 min; benchmark regenerated; section 18 written.
- **2026-10-05 (end of session 1)** — Phase 1.10 mostly done: pipeline runner, privacy/reproducibility tests, benchmark harness (+ held-out results and ablation in `docs/BENCHMARK.md`), chunked scoring, lint clean, README; 81 tests passing in `.venv`. Big (~690k records) run in flight; benchmark must be regenerated; Docker still unverified; Phase 2 not started. See the RESUME HERE block at the top.
- **2026-10-05** — Research completed (entity resolution tools, Splink/Zingg/dedupe, KYC/doc pipelines). Requirements gathered. Created PROGRESS.md. Next: Phase 0.2–0.6.
- **2026-10-05** — Phase 1.9 done: household layer (evidence + guarded grouping); 75 tests passing in `.venv`.
- **2026-10-05** — Phase 1.8 done: guarded clustering; found and fixed over-regularisation (L2 2.0 -> 0.2); end-to-end cluster F1 0.907; 68 tests passing in `.venv`.
- **2026-10-05** — Phase 1.7 done: decision zones + rule language; 59 tests passing in `.venv`.
- **2026-10-05** — Phase 1.6 done: scoring (identifier-seeded logistic + TF), best pair F1 0.90 on 8k records; 53 tests passing in `.venv`.
- **2026-10-05** — Phase 1.5 done: blocking with frequency cap; 45 tests passing in `.venv`.
- **2026-10-05** — Phase 1.4 done: standardisation + quality report; 41 tests passing in `.venv`.
- **2026-10-05** — Phase 1.3 done: connectors + mapping + ingest; 24 tests passing in `.venv`.
- **2026-10-05** — Phase 1.2 done: evaluation harness + baseline (F1 0.585). 14 tests passing in `.venv`.
- **2026-10-05** — Phase 1.1 done: synthetic generator, 10 tests passing in `.venv`.
- **2026-10-05** — Phase 0.2–0.4 done (repo, skeleton, Dockerfile, compose). 0.5 blocked by Docker engine failure on the host (engine pipe missing, `wsl` command hangs). Waiting on user to restart Docker Desktop / reboot. No commits yet. Fallback: dev proceeds in `.venv` (`.venv/Scripts/python -m pytest`).
- **2026-10-05** — User answered open questions: no real data (synthetic only); platform must ingest from any source. Added connector design (7a), updated roadmap 1.3 and decisions log.

## 9. Synthetic dataset (Phase 1.1)

Code: `src/dedupe/synthetic/` (reference.py names/cities/catalogues, identifiers.py PAN/Aadhaar(Verhoeff)/mobile/etc., corrupt.py noise, households.py families + traps, sources.py the 5 systems, generate.py CLI). Tests: `tests/test_synthetic.py`. Output in `data/synthetic/` (gitignored, regenerate with the command above; deterministic per seed).

Default run (1200 households, seed 42): 3,802 people, ~8,200 records across 5 sources.

| Source | Format | Notes |
|---|---|---|
| bank_crm | CSV | accounts, segment, KYC status, nominee; ~7% customers onboarded twice (in-source duplicates) |
| insurance_policies | XLSX | one row per party per policy (Proposer / Insured / Nominee); nominee rows very sparse; motor, health floater (family), term, ULIP etc. |
| loans | CSV | applicant + co-applicant, father name, current vs permanent address, raw Aadhaar on ~38% rows |
| marketing_leads | CSV | sparse, first-name-only, age bands, repeat form submissions |
| kyc_documents | CSV | OCR-style extractions of PAN/Aadhaar/passport/DL/voter ID with OCR errors and confidence |

Ground truth in `data/synthetic/ground_truth/`: `persons_truth.csv` (true person, household/family, relation, hard-case `traps`) and `record_truth.csv` (every source record -> true person, role). Never feed these to the matcher; only to the evaluation harness.

Hard cases built in: shared mobile/email within families, minors under a parent's phone, twins, junior/senior same name, namesakes (same name + city, different people), maiden vs married names, old mobile/address, initials, transliteration, typos, swapped/placeholder DOBs, dropped PINs, city aliases, OCR digit confusion, and in-source duplicates. Row order and IDs are shuffled so they leak nothing.

Known limitations: names come from limited lists; addresses are template-based; no non-Latin scripts yet; individuals only. Results on this data do not prove real-world accuracy.

## 10. Benchmark log (all on SYNTHETIC data, seed 42, 1200 households, 8,178 scored records, 2,842 persons)

Note: 9 people appear only as loan co-applicants and have no record of their own, so scored persons (2,842) < persons with any mention (2,851).

| Date | Matcher | Precision | Recall | F1 | False-merge pairs | Notes |
|---|---|---|---|---|---|---|
| 2026-10-05 | Baseline: exact PAN or exact mobile (union-find) | 0.790 | 0.465 | 0.585 | 1,459 (all within the same family) | Shared family mobiles cause false merges; misses records without PAN/mobile, typo'd identifiers and OCR'd docs |
| 2026-10-05 | **Full pipeline (rules + scoring + decisions + guarded clustering), cluster-level pairwise, 8.2k records** | 0.992 | 0.836 | **0.907** | 77 | auto-merge groups only; 3,514 clusters for 2,842 true persons (single-record leftovers + review queue pending) |
| 2026-10-05 | Same, 137k records (20k households) | 0.996 | 0.841 | **0.912** | - | 8.5 s clustering; review queue 57k pairs (42% real) |
| 2026-10-05 | Dev-only ceiling: stewards approve every real match in review queue | 0.993 | 0.942 | 0.967 | - | upper bound for the review workflow, not a real result |

Key lesson: a naive shared-mobile rule merges family members. The real engine must treat shared contact details as weak evidence.

## 11. Connectors and mapping (Phase 1.3)

- `src/dedupe/connectors/`: `base.py` (interface `test_connection / columns / read_batches` + registry + env-var secrets), `files.py` (csv/tsv, xlsx, parquet), `sql.py` (any SQLAlchemy URL; table or SELECT-only query; URL from env var), `sheets.py` (Google Sheets via CSV export). All emit Arrow tables of strings.
- `src/dedupe/mapping/`: `canonical.py` (the canonical schema: 24 fields + `source_id`, `source_record_id`, `attributes` JSON), `config.py` (mapping JSON -> DuckDB SQL; expressions: column, coalesce, concat, conditional column, const), `suggest.py` (auto-suggest mapping from column names + sample values).
- `src/dedupe/ingest.py`: validates all sources/mappings first, then streams batches -> one Parquet (`data/staging/canonical.parquet`). Config for the synthetic sources: `configs/synthetic_sources.json`.
- Verified: 24 tests pass; ingest of the 5 synthetic sources yields 8,178 records whose keys equal the evaluation truth keys. Throughput check: 137k records ingested in ~12s on this machine (XLSX reading is likely the slowest part, unprofiled).
- **Not verified against real systems:** Postgres/MySQL (tested only via SQLite through the same SQLAlchemy path; drivers are optional extras `.[postgres]`, `.[mysql]`), Google Sheets (tested with a mocked fetch; no live credentials), cloud warehouses.
- Not yet done: loan co-applicants are carried only as `related_name`/attributes, not as separate records; address parsing and cleaning belong to 1.4.

## 12. Standardisation (Phase 1.4)

Code `src/dedupe/standardise/`: `names.py` (honorifics, `Last, First` order, OCR digit repair, initials, phonetic skeleton: Mohd/Muhammad/Md -> md, Sharma/Sarma -> srm), `identifiers.py` (phone, email + matching key, PAN with positional OCR repair, Aadhaar Verhoeff check + keyed-HMAC token, passport, DL, voter ID), `dates.py` (day-first, month-first detection, year-only, placeholder 01-Jan flag), `addresses.py` + `geo.py` (PIN, city aliases/renames, state, house number, normalised text), `verhoeff.py`, `run.py` (canonical.parquet -> standardised.parquet, ~10k records/s single core), `quality.py` (raw vs cleaned agreement using ground truth).

Principles enforced: invalid identifiers become NULL (never matched on); raw Aadhaar never leaves this stage (only HMAC token + last4; set `DEDUPE_TOKEN_KEY` in production, a dev key with a warning is used otherwise); output holds match features only, raw values stay in canonical.parquet for survivorship/lineage.

Measured on synthetic data (same-person record pairs agreeing on a field, raw -> cleaned): mobile 25% -> 98%, surname 62% -> 94%, whole name 25% -> 64%, DOB 42% -> 89%, city 57% -> 89%, PAN 94% -> 100%, Aadhaar 80% -> 100% (PAN/Aadhaar measured only on values that pass validation). Cross-person collisions rise for mobile (330 -> 1,248) because formats now line up and families share numbers: this is the family-sharing hazard, handled in scoring/rules, not here.

Caveats: (1) the pincode "agreement" fell (95% -> 92%) only because the cleaned column also includes PINs extracted from free-text addresses, a larger and noisier population. (2) The synthetic generator and the cleaners share my assumptions about Indian naming/addresses, so these numbers are optimistic for real data. (3) Names remain Latin-script only; no transliteration from Devanagari etc. yet. (4) City aliases not in the real-world list (e.g. 'Mohali' vs 'Chandigarh', 'Pink City') stay unresolved on purpose; PIN prefix should cover them in scoring.

## 13. Blocking (Phase 1.5)

Code `src/dedupe/blocking/`: `rules.py` (each rule = SQL returning (rid, key); 12 default rules: PAN, Aadhaar token, passport, DL, voter ID, phone (either field), email key, surname+DOB, first name+DOB, all-name-sounds+birth year, surname+initial+birth year, first+last+PIN prefix, name+city, DOB+PIN), `generate.py` (union of rules, frequency cap, writes `candidates.parquet` (l_rid, r_rid, rules[]) + `candidates.index.parquet`), `report.py` (ground-truth evaluation). Run: `python -m dedupe.blocking --data data/synthetic [--cap 50]`.

Results (synthetic, seed 42, 8,178 records): 33.4M possible pairs -> 13,210 candidates (99.96% reduction); 85.2% of true duplicate pairs found directly; **94.5% reachable through other records** (what clustering could achieve with perfect scoring). At 137k records: 9.4B possible pairs -> 539k candidates, 93.5% transitive completeness, 3.3 s. Pair count grows ~2.4x faster than records (real name/birth-year coincidences), so the cap and rule selectivity matter at millions.

Candidates are not matches: ~3,200 of the 13,210 are false (family-shared phones, namesakes, twins, same-PIN same-DOB): scoring/vetoes must reject them.

Misses (5.5% unreachable): mostly KYC-document-only and lead-only records that carry no phone/email/ID in common with anything else (leads have no DOB/ID; KYC docs have no phone/email). Documented limitation; fixing needs richer evidence (e.g. face/biometric, Phase 3) or review-queue suggestions.

Caveat: the frequency cap (default 50) is unit-tested but this synthetic data contains no truly generic values (shared agent phones, '9999999999'), so its real-world setting is unproven. Tune it on pilot data.

## 14. Pair scoring (Phase 1.6)

Code `src/dedupe/scoring/`: `comparisons.py` (9 comparisons as ordered level ladders in SQL: name 8 levels, DOB 6, phone 3, email 2, PAN 2, Aadhaar 4, other gov IDs 2, address 6, gender 2; missing values are 'not comparable' = zero evidence), `levels.py` (DuckDB -> level/TF matrices), `train.py`, `predict.py`, `evaluate.py`, `model.py`. Run: `python -m dedupe.scoring --data data/synthetic` -> `data/staging/scored_pairs.parquet` (probability, match_weight, per-field levels `lv_*` and contributions in bits `w_*`) + `data/models/fs_model.json` (weights per level in bits, readable).

**Design deviation (and why).** I first built classic Splink-style Fellegi-Sunter (u from random pairs, m by EM). Findings on this data: (a) EM drifted: family pairs with shared address/phone got high posteriors, so m was contaminated (e.g. believed 24% of true matches had a different phone); (b) with u from random pairs, shared address/phone counted as +10 bits each, giving ~500 false merges (503 within families) at p>=0.9; (c) when m/u are taken from identifier-seeded pairs instead, naive-Bayes per-field ratios still double-count correlated fields. Final design: identifiers (PAN, Aadhaar token, passport/licence/voter) get fixed, documented weights (+13.2 bits same, -6.6/-4 different); all other comparisons get **L2-regularised logistic-regression coefficients fitted to identifier-seeded candidate pairs** (same ID = same person, conflicting IDs = different people), using only non-identifier levels so there is no leakage. Term-frequency adjustment (rare agreeing value counts more, common less; x16 clip) is applied on top. No hand labels are used. Hybrid rules + ML, CPU only, explainable per field.

Results (SYNTHETIC; 8,178 records, 13,260 candidates, 11,787 true pairs of which 10,060 are candidates):
| threshold | precision | recall (of all true pairs) | F1 | false merges |
|---|---|---|---|---|
| 0.5 | 0.979 | 0.805 | 0.883 | 201 |
| 0.9 | 0.9988 | 0.568 | 0.724 | 8 |
best F1 0.899 (precision 0.962, recall 0.844) at p>=0.23. Naive baseline was F1 0.585 / precision 0.79. Calibration is monotone: p 0.1-0.5 -> 49.5% real matches; 0.5-0.9 -> 93.5%; >0.9 -> 99.9%. => natural zones: auto-merge >= ~0.9, review ~0.1-0.9, reject below (to be formalised in 1.7). Recall ceiling from blocking is 85.4%.
At 137,471 records (20k households): precision 0.997 at p>=0.9 (387 false merges / 136k), best F1 0.860, scoring 543k pairs in 16 s; mid-band probabilities slightly over-confident (0.5-0.9 -> 71% real).

Remaining errors: false merges at 0.9 are twins, junior/senior and shared-mobile family members (strongest misleading field: DOB/name placeholders). Missed true pairs (in candidates): weakest fields are name (maiden/married, initials, OCR) and address (previous addresses, one-sided city). Many involve ID-less leads where only name+phone+city exist: genuinely ambiguous, belong in review.

Caveats: weights learned from identifier-bearing pairs; behaviour on ID-less pairs is extrapolated. Training data and test data are the same synthetic population (no hand labels, but no held-out set either). The strong-ID bits are principled constants, not fitted. Real-data behaviour unproven.

## 15. Decisions and business rules (Phase 1.7)

Code `src/dedupe/decision/`: `conditions.py` (safe condition language compiled to SQL; no raw SQL accepted; unknown comparison/level/field raises), `config.py` (pydantic `DecisionConfig`/`Rule`; rules validated at load time; precedence **never_merge > force_review > auto_merge > score zones**), `apply.py` (writes `decisions.parquet`: l_rid, r_rid, probability, decision, decided_by, reason, fired[]), `report.py` (dry-run impact vs ground truth, dev only). Default rules in `configs/decision_default.json`. Run: `python -m dedupe.decision --config configs/decision_default.json`.

Default zones: auto-merge at probability >= 0.90, review 0.10-0.90, reject below. Default rules (9): vetoes for PAN conflict, Aadhaar conflict, gender conflict, birth years >10 apart (each unless an identifier agrees); auto-merge for same PAN / same Aadhaar token with a compatible name; force-review when an identifier agrees but names differ, when PAN and Aadhaar contradict each other, and a **twin guard** (minors, same exact DOB and PIN, no identifier agreeing). Every decision records which rule (or 'score') decided and why, so it is auditable.

Results (SYNTHETIC, 13,260 candidate pairs, 11,787 true pairs):
| zone | pairs | real matches | not matches |
|---|---|---|---|
| auto_merge | 6,643 | 6,638 (99.9%) | 5 |
| review | 4,077 | 3,393 (83.2%) | 684 |
| reject | 2,540 | 29 (1.1%) | 2,511 |
Auto-merge precision 0.9992 (score-only zones: 0.9988, 8 false merges), recall 0.563. Rules moved 71 pairs review->reject (0 real) i.e. 71 fewer for a human, 55 pairs auto->review (52 real: the price of the twin/identifier guards), 1 auto->reject. The twin guard fired on 14 pairs (10 real, 4 not).

Honest reading: on this data the vetoes mostly save reviewer effort (they decided ~1,700 pairs that the score had already kept out of auto-merge) and the guards trade a little auto-merge recall for safety; the score alone was already precise. Vetoes matter more when scores are less reliable (real data). The review zone is 83% real matches, so it is a worthwhile queue, not noise. Remaining 5 auto-merge false merges are initial-only twin/sibling records and family members sharing a phone.

Notes: gender veto is safe here because the synthetic data has no gender noise; real data will, so tune/disable via the config (this is exactly what Phase 2 exposes to business users). 'Possible twins' uses a dob_year >= 2008 cut-off tied to the data's reference date (2026): make it relative to run date when Phase 2 adds parameters.

## 16. Clustering (Phase 1.8) and a model correction

Code `src/dedupe/clustering/`: `graph.py` (connected components over auto-merge edges; contradiction check = two different PANs, two different Aadhaar tokens, or a vetoed 'cannot-link' pair inside one group; split by repeatedly deleting the weakest edge on the max-bottleneck path between the conflicting records; bounded by `max_removals`, unresolved groups are released as singletons and flagged; oversize groups flagged), `run.py` (writes `data/staging/clusters/`: `clusters.parquet`, `cluster_stats.parquet`, `possible_matches.parquet` (review pairs between different clusters, for the steward queue), `removed_edges.json`, `report.json`; evaluates with the Phase 1.2 harness). Run: `python -m dedupe.clustering`. No ML here by design: deterministic and fully traceable.

Results (SYNTHETIC): 8,178 records -> 3,514 clusters (1,555 singletons), cluster-level pairwise precision 0.992, recall 0.836, F1 0.907 (naive baseline 0.585). The guard fired on 8 groups (16 edges removed); without it precision is 0.985 and impure clusters 17 vs 15. At 137,471 records: precision 0.996, recall 0.841, F1 0.912; guard fired on 124 groups (229 edges), impure clusters 170 vs 248 without it, largest cluster 12 vs 16. Dev-only ceiling if stewards approved all real matches in the review queue: F1 0.967.

**Model correction found while testing.** Quality depended on dataset size (F1 0.56 / 0.71 / 0.79 at 150 / 400 / 1,200 households): the logistic regularisation (L2 = 2.0) was too strong, shrinking evidence and pushing pairs into review. Experiment over L2 in {2.0, 0.5, 0.1}; set L2 = 0.2 (F1 at 150 / 400 / 1,200 households about .75-.9, precision >= 0.99). This supersedes the scoring/decision numbers in sections 14 and 15 (they used L2 = 2.0): with L2 = 0.2 scoring best F1 is 0.902 (p>=0.32); at p>=0.9 precision 0.9966 and recall 0.703; zones are auto-merge 8,252 pairs (99.7% real, 24 false merges), review 2,306 (77.7% real), reject 2,702 (1.5% real).

Caveats: (1) L2 was tuned on the same synthetic family of data it is evaluated on; treat as a sensible default and re-tune on pilot data. (2) At scale the review queue grows large (57k pairs at 137k records, 42% real): real deployments need prioritisation (sort by probability / cluster impact) and bulk-approve tools in the steward UI. (3) 'Persons split across clusters' is high (588 of 2,842) because records that only a human can link remain separate: that is the review queue's job. (4) The contradiction guard only knows PAN/Aadhaar and vetoed pairs; other contradictions go undetected.

## 17. Households (Phase 1.9)

Code `src/dedupe/households/`: `evidence.py` (SQL relationship evidence between resolved people), `group.py` (components with size guard), `run.py` (outputs + dev evaluation). Run: `python -m dedupe.households [--threshold 5]` -> `data/staging/households/` (`household_members.parquet`, `relationships.parquet` with score + evidence list, `report.json`). Standardiser gained `father_name_phon` / `related_name_phon`.

Principle: a household is a set of DISTINCT people linked by evidence; nothing is merged. Evidence per pair of people (each type counted once, strongest instance): policy_member 5 (insured on the same policy), policy_nominee 3, same_home 4 (same PIN + flat number + similar text), same_area_text 1, shared_phone 4 and shared_email 3 (ignored if used by more than 8 people: agents/call centres), named_relative 3 (a nominee/co-applicant name matches the other person; must not contradict known cities; diluted by namesake count), father_named 2 (age gap >= 15 years, male), same_surname 1 (only counts when other evidence exists). Households = connected components of links with score >= threshold (default 5); components over 10 people are re-split at stricter thresholds (chaining guard), flagged if unsplittable.

Results (SYNTHETIC, dev evaluation vs true households; people = predicted person-clusters):
| size | threshold | pairwise P / R / F1 | family-only P / R | note |
|---|---|---|---|---|
| 8.2k records | 5 (default) | 0.983 / 0.518 / 0.678 | 0.980 / 0.538 | 350 of 927 multi-person households reproduced exactly |
| 8.2k records | 4 | 0.910 / 0.619 / 0.737 | 0.898 / 0.637 | best F1, recall-leaning |
| 137k records | 5 | 0.996 / 0.403 / 0.574 | 0.996 / 0.434 | 4,218 of 15,469 exactly |
| 137k records | 4 | 0.751 / 0.524 / 0.617 | 0.728 / 0.545 | precision collapses; 422 size-guard escalations |
'Family-only' ignores pairs that are really two fragments of one person (a clustering miss, 783 pairs in the 8.2k run).

Evidence reliability (share of links inside one true household), 8.2k -> 137k records: policy 99% -> 99.9%, same_home 99.7% -> 99.9%, shared_phone 99.9% -> 99.8%; but named_relative 78% -> 17%, father_named 50% -> 6%, shared_email 82% -> 26%, same_surname 88% -> 28%. Name/email-based evidence stops being reliable as the population grows (coincidences), so **threshold 5 is the default** (needs a strong signal plus support) and weak evidence types must never act alone.

Limits: (1) 46% of true family pairs have no evidence at all in the data (e.g. a family member who appears only as a lead/document with their own phone and no address): inherently unreachable, recall ceiling ~55%. (2) The synthetic data has no apartment-complex neighbours with identical society names, so same_area_text (99.6% precise here) will be far less reliable on real data: it is kept at weight 1. (3) Weights are hand-set defaults informed by the measured reliabilities on synthetic data, not learned; to be exposed as business-user settings in Phase 2. (4) The evidence queries took 30 s at 137k records; PIN joins are capped (300 records per PIN) but real data with large PINs needs a locality-aware key.

## 18. Final Phase 1 run: pipeline, benchmark, 692k-record run (all SYNTHETIC)

**One command:** `python -m dedupe.pipeline` (ingest -> standardise -> block -> score -> decide -> cluster -> households); about 7-9 s for 8k records.

**Benchmark (`docs/BENCHMARK.md`, regenerated after the final code; held-out seeds 101-103).** Person-cluster pairwise F1 0.86-0.93 across noise 0.5/1/2 (means 0.90 / 0.91 / 0.90); precision 0.98-1.00, recall 0.76-0.88. Size scaling (noise 1, seed 101): 300 households F1 0.783 (recall 0.645, the model has little data to learn from), 1,200 households 0.928, 5,000 households 0.917. Ablation: naive baseline F1 0.601; clustering guard cuts false-merge pairs 466 -> 166; decision rules 250 -> 166 (F1 equal); TF adjustment lowers F1 (0.940 without vs 0.928 with) but halves false merges (367 -> 166).

**692,537 records (100,000 households, seed 7, noise 1.0), 8 CPUs:** 8.87M candidate pairs; ingest 46 s, standardise 58 s, blocking 9 s, scoring 106 s, decisions 55 s, clustering 30 s, households 114 s, evaluation 25 s; total 445 s. Person clusters: precision 0.993, recall 0.851, F1 0.917; auto-merge precision 99.4% (3,956 false merges among auto merges); review queue 428,852 pairs; households family-only precision 0.990, recall 0.420. Log: `docs/big_run_690k.log`.

**Problems found and fixed during the big run:** the first attempt used >16 GB in the households stage and had to be killed (I first misdiagnosed it as scoring because I looked in the wrong folder). Causes: the named-relative join matched every record against every record sharing a phonetic name (common names repeat thousands of times), and the same-surname check joined records, not people. Fix in `households/evidence.py`: ignore relative names shared by >50 people (their weight would be under 1/50 anyway) and compare per-person surname sets. Households stage: >16 GB and unfinished -> 1 GB / 11 s standalone.

**Honest caveats:** peak memory of the whole pipeline is still 17 GB at 692k records (shared in-memory DuckDB connection plus Python lists of pairs and edges); scoring alone peaks near 3 GB. Millions of records need a memory pass first. Recall at small data sizes is weak. Everything here is synthetic.

## 19. Review queue at scale (SYNTHETIC, 692,537 records)

**Problem found:** the pipeline reported 428,852 pairs for human review, far beyond what stewards can clear. Measured composition (record pairs; truth known because synthetic): 49,822 were already inside one cluster (nothing to decide); 386,034 crossed clusters and collapse to 253,120 distinct pairs of people, of which 24,723 (10%) are the same person, 8,486 (3%) are family, 219,911 (87%) are unrelated. Most junk sits at probability below 0.2 (243k unrelated vs 10k same-person record pairs). At scale more unrelated people share names/phones, so the 0.1 lower threshold that suited 8k records is too wide.

**Built:** `src/dedupe/review/queue.py`, pipeline stage `review_queue` writing `review_queue.parquet`: one row per pair of people (record_pairs, top/mean probability, forced-by-rule flag, cluster sizes, priority), pairs already in one cluster dropped, sorted best first. Workload controls `review_min_probability` and `review_cap` in `PipelineConfig` (defaults keep everything). 0.6 s on 692k records. 83 tests.

**Trade-off (per pair of people, real same-person matches kept):** review_at 0.1: 253k items, 10% real, 100% kept; 0.2: 88k, 22%, 78%; 0.4: 37k, 43%, 63%; 0.6: 20k, 59%, 48%. Note: matches dropped from the queue are not necessarily lost for the final result if other pairs of the same two people were auto-merged; not measured. Choosing the cut-off is a business decision. Still too large for manual review at default; options: higher cut-off, cap, better model features, steward bulk actions.

## 20. Fixing the review-queue size (SYNTHETIC)

**How industry tools handle it (research):** Splink/Fellegi-Sunter: the review band is set from target error rates and the prior match probability falls as data grows; studies review only a few % of pairs on a budget. Senzing: "principles" (a minimum combination of features such as name+DOB or name+address) must be met before anything is even a possible match, and generic values (e.g. a call-centre phone) are ignored. Informatica: narrow manual-merge band, "filtered" match rules (exact keys + fuzzy key) for large batches. dedupe.io/Zingg: reviewers label the most uncertain pairs (active learning).

**Diagnosis (138k records):** overall calibration was acceptable (predicted 0.35 vs actual 0.31 etc.), but one pattern dominated the queue: same name + same city (PIN missing on one side) with DOB, phone and email all missing: ~25k of ~40k queue pairs, 10-15% real, scored ~0.5 (over-confident for this pattern). Same phone + different name was 97% family (a relationship, not a duplicate). The `name_city` blocking rule produced 47% of all candidates and 26k of the junk pairs; candidates per record grew from 4 (138k) to 13 (692k). At 692k a second pattern appeared: same name + same email but different people, traced to a generator bug (identical mailboxes for unrelated namesakes), fixed.

**Fix:** (1) new rule type `not_enough_evidence` (precedence after never_merge, force_review, auto_merge; soft reject that does not forbid the records joining through other evidence); default rule requires one of: an agreeing identifier; a compatible name plus full DOB / phone / email / same home address / (birth year + same PIN); or an exact DOB plus phone / email / similar address (covers name changes). Business-editable in `configs/decision_default.json`. (2) `name_city` moved to `OPTIONAL_RULES`. (3) Grouped, ranked queue (section 19).

**Result (694,330 records, same data, old vs new):** queue 246,948 -> 14,543 pairs of people; real share 13% -> 87% (items checked per real match 7.6 -> 1.15); F1 before review 0.906 -> 0.922 (recall 0.832 -> 0.858, precision 0.995 both); auto-merge precision 99.56% -> 99.53%; run time 8.6 -> 7.2 min; candidates 6.3M. At 138k: queue 27,754 -> 4,352.

**Cost / caveats:** ideal-steward ceiling F1 0.956 -> 0.941: about 20k real matches are no longer offered for review; they were hidden among ~214k unrelated pairs with only a name and a city in common, which a reviewer could not tell apart either. Their decisions are kept (`decided_by = not_enough_evidence`) so they can be revisited when new data arrives. Father's name would rescue only ~190 such pairs here (only loans/KYC carry it). Remaining open: auto-merge false positives at scale are mostly fuzzy name + exact DOB with nothing else (namesakes sharing a birthday) — candidate for a stricter auto-merge principle; active learning from steward decisions is Phase 2. Synthetic data only.

**Benchmark after the fix (`docs/BENCHMARK.md`, regenerated; held-out seeds 101-103, ~8k records):** F1 0.855-0.940, mean 0.910 / 0.911 / 0.879 at noise 0.5 / 1 / 2 (noise 2 is slightly lower than before: 0.897 -> 0.879); precision 0.995-1.000; review pairs now 97-99% real (was 66-87%) and queues are about half the size. Small data improved most: 300 households F1 0.783 -> 0.905. Ablation: decision rules now trade a little recall for far fewer false merges (38 -> 13). New signal: without term-frequency adjustment F1 is higher (0.929 vs 0.907) with a much smaller queue (329 vs 1,004) and only slightly more false merges (20 vs 13); TF adjustment should be re-examined next.

## 21. Namesake auto-merges (SYNTHETIC, 694,330 records)

**Diagnosis:** 3,396 of 718,628 auto-merged record pairs were wrong (0.47%), all decided by score (identifier rules: 0 wrong). Two families of errors: (1) namesakes: a name plus an exact/swapped DOB and nothing else (~1,350 wrong; worst sub-pattern 'fuzzy full-name match (JW>=0.90)' + exact DOB: 7-10% wrong, because a JW >= 0.90 on full names accepts different surnames such as Sharma/Varma); (2) same name + shared phone with no DOB: family members (~550 wrong). Auto-merges backed by 2+ independent evidence types are almost never wrong (187 of 243k).

**Options measured (record pairs moved to review / wrong merges prevented / checks per prevented error):** A fuzzy name + DOB only: 12,223 / 792 / 15. B any non-identical name + DOB only: 53,297 / 1,204 / 44. D any name + DOB only: 204,004 / 1,651 / 124. E name + phone, no DOB: 114,262 / 1,224 / 93. Raising auto_merge_at 0.9 -> 0.97: 48,849 / 1,235 / 40; -> 0.99: 152,035 / 2,514 / 60. Chose A as a force_review rule (business-editable in `configs/decision_default.json`).

**Result (same data):** review queue 14,543 -> 17,769 pairs of people (87.6% real); auto-merge precision 99.53% -> 99.63%; person-cluster precision 0.9950 -> 0.9962, recall before review 0.858 -> 0.850 (those matches now wait for a reviewer), F1 before review 0.9215 -> 0.9172; ideal-steward ceiling F1 0.9407 -> 0.9413. At 138k: queue 2,883 -> 3,525.

**Not fixed (documented):** same-name family members sharing a phone with no DOB (fix needs evidence a reviewer also lacks; candidate: household-aware check in Phase 2), and namesakes with identical names + DOB (0.3% wrong, not worth 48k reviews). The rule's 0.9 score condition duplicates `auto_merge_at`; keep them in sync.

## 22. Term-frequency adjustment re-check (SYNTHETIC)

TF ("common values count less") applies to name, dob, phone, email. The 8k-record benchmark suggested switching it off. Measured with the current rules:

| run | TF | queue | P | R before review | F1 before review | auto-merge P | ceiling F1 |
|---|---|---|---|---|---|---|---|
| 138k | on | 3,525 | 0.9960 | 0.848 | 0.916 | 99.68% | 0.9409 |
| 138k | off (all) | 2,654 | 0.9959 | 0.862 | 0.924 | 99.62% | 0.9410 |
| 138k | off (name only) | 2,889 | 0.9962 | 0.856 | 0.921 | 99.66% | 0.9411 |
| 694k | on | 17,769 | 0.9962 | 0.850 | 0.917 | 99.63% | 0.9413 |
| 694k | off (all) | 12,709 | 0.9955 | 0.864 | 0.925 | 99.54% | 0.9413 |
| 694k | off (name only) | 14,905 | 0.9961 | 0.857 | 0.922 | 99.61% | 0.9415 |

Phone-only or email-only TF off changes nothing material (138k). **Decision: keep TF on.** Its value grows with data size: at 694k it prevents ~650 wrong auto-merges at a cost of ~5k extra review items (~8 checks per prevented error), and the reachable F1 after review is identical. Lesson: conclusions from 8k-record runs do not transfer to scale; check decisions at 100k+ records.

## 23. Pipeline memory (SYNTHETIC, 694,330 records, 8 CPUs)

**Cause of the 17 GB peak:** one in-memory DuckDB connection was shared by all stages and every stage left its working tables in it (blocking rule pairs, pair levels, scored/wide/flagged pair tables, ...), and DuckDB by default may use up to 80% of RAM.

**Fix (`src/dedupe/pipeline.py`):** the meter now records peak memory per stage and memory still held after each stage (`peak_memory_mb`, `memory_after_mb`); the connection gets `memory_limit` (default 4GB, `PipelineConfig.memory_limit`) and a spill directory `<work>/.duckdb_tmp` (deleted at the end); each stage drops tables later stages do not need; clustering inputs are released.

**Result:** overall peak 17.1 GB -> 4.6 GB; run time without evaluation 6.0 min. Per stage (peak / after): ingest 0.36 / 0.27 GB, standardise 0.63 / 0.48, blocking 2.9 / 0.86, scoring 3.9 / 0.79, decisions 4.6 / 1.5, clustering 4.3 / 3.5, households 3.9 / 3.6. At 138k: 1.4 -> 1.1 GB. Results unchanged (logic untouched; reproducibility test passes).

**Remaining:** clustering is pure Python (dicts/lists per record) and leaves ~2 GB held; linear extrapolation is ~30 GB at 5M records. Next memory step before a multi-million run: array-based clustering (NumPy union-find) or clustering inside DuckDB.

## 24. Golden record and survivorship (Phase 2.4; SYNTHETIC)

**What it does:** after clustering, each person (cluster) gets one golden record built from its source records. Per field, a strategy picks the value: `most_trusted_source` (source ranking), `most_recent` (record date taken from a per-source attribute, e.g. bank_crm `account_open_date`), `most_complete` (longest / most filled), `most_frequent` (agreement between records; ties go to the more complete, then more trusted, then newer). Fields in a **group** (default: address, city, state, pincode) always come from ONE source record so a golden address is never a mix; the first group member is the key field (records without it are skipped unless no record of the person has it). Cleaned values from standardisation are used (title-cased names, validated identifiers, valid birth dates only); raw values only for address, marital status, occupation, income.

**Outputs** (`<work>/golden/`): `golden_values.parquet` (person x field: value, source_id + source_record_id it came from, strategy, n_candidates, n_distinct = lineage and conflict evidence) and `golden_records.parquet` (one row per person: all fields, record_count, sources, conflict_fields). Settings: `configs/survivorship_default.json` (source_trust, source_dates, default_strategy, per-field strategies, groups), validated by Pydantic (`SurvivorshipConfig`); `python -m dedupe.survivorship --work data/staging` runs it alone.

**Cost at 694k records:** +75 s, +0.4 GB peak; 299,563 golden records, 2.8M values, 564k values where sources disagreed.

**Accuracy vs synthetic truth** (clusters made of one true person, 288,011 of 299,563): PAN 100%, gender 100%, father name 99%, mobile 97%, DOB 96.5%, pincode 95%, email 93.6%, first name 87%, city 91.5%. Two bugs found by this check and fixed: (1) longest-raw-name wins chose noisy forms (honorifics, surname-first commas, typos): first-name accuracy 60% -> 87% after using the cleaned name chosen by agreement; (2) 1-Jan placeholder birth dates were accepted as values: DOB 94% -> 96.5%. Remaining errors are mostly typos in clusters with a single record (nothing to vote against) and name variants; unavoidable without outside data.

**Caveats:** defaults (source trust order, per-field strategies) are my guesses for a bank/insurer mix and need a business decision; `most_recent` only works where a source has a usable date attribute, else it falls back to trust; the golden record is a view of the current clusters (no steward overrides yet: Phase 2.5); no per-field survivorship audit trail across runs yet.

## 25. Research: steward review, merge/unmerge and persistence (before building 2.5)

**Sources consulted:** Reltio docs (potential matches, Unmerger agent, immutable activity log), Informatica MDM docs (manual-merge band, merge history, Supplier 360 unmerge), nomenklatura / OpenSanctions (judgment graph, 290k published pair judgments), Senzing (explainability "why / why not", re-resolution, persistent entity IDs), a Quantexa/Tamr-style patent family on combining user feedback with automated ER, US patent 12417236 (stewardship actions and re-clustering), AWS Entity Resolution (incremental ML matching), arXiv 1805.12502 (risk-based selection of pairs for human verification). Limits: vendor pages were thin on internals (Reltio and Senzing did not detail how "not a match" is stored); marketing numbers were not relied on.

**What the tools do, and what we do about it**

| Practice found | Where | Our status / decision |
|---|---|---|
| Decisions are an append-only graph of judgments (same / not same / unsure) between record IDs; a cluster is computed from the graph | nomenklatura | ADOPT: judgment log (2.5b), including `unsure` |
| Unmerge = remove the steward-created link and re-run automatic matching with that link excluded, keeping later data/rule changes; unmerged records are blocked from auto re-merging | patent 12417236; Informatica | ADOPT: split = cannot-link between the parts + removal of the must-link; replayed every run (2.5c) |
| Stable entity IDs survive re-processing | Senzing, Informatica | GAP: our cluster IDs change per run -> 2.5a before anything else |
| Immutable activity log: who, what, when; every merge and unmerge traceable | Reltio | ADOPT in 2.5b (+ rule/model version and evidence snapshot) |
| Steward can choose a survivorship preference when merging | Reltio | ADOPT: golden field overrides (2.5d), stored as judgments |
| Auto-merge above ~0.95, steward task 0.70-0.95 (a narrow band), side-by-side compare with evidence and one action to merge or separate | Reltio, Informatica | PARTLY: grouped + ranked queue done; add explain (per-field contributions are already stored in `scored_pairs.parquet`) |
| "Why / why not" explanations | Senzing | ADOPT via stored contributions (2.5d) |
| Risk-based selection of pairs for human review beats plain active learning at the same budget | arXiv 1805.12502 | MEASURE in 2.5e: ranking strategies by reviews-vs-F1 curve; keep current ranking unless a better one is shown |
| Four-eyes approval for sensitive merges | MDM governance practice | OPTIONAL setting (2.5d) |
| Re-resolution: earlier decisions are revisited when new evidence arrives | Senzing | HAVE (every batch run re-scores everything); steward judgments override |
| Incremental matching against persisted state (1M new records in under an hour vs reprocessing everything) | AWS Entity Resolution | NOT BUILT: full re-run today (7 min at 694k, grows linearly or worse). Needs 2.5a + 2.5b first; Phase 4 item, and important for competitiveness |
| Judgments become training labels (OpenSanctions trains its matcher on 290k) | OpenSanctions | LATER: store judgments in training-ready form now; use them as labels after the pilot |

**Policy decisions made**
- Judgments key on `(source_id, source_record_id)`, never on cluster IDs or row ids (those change per run).
- Steward decision beats automation, except hard identifier conflicts (different valid PANs/Aadhaar): those need an explicit second confirmation and are reported.
- Conflicting judgments (A=B, B=C, A!=C) are not silently resolved: reported in a contradiction list for a steward.
- Everything is tested by a simulated reviewer using synthetic ground truth (optionally with a mistake rate), reporting F1 against number of reviews, plus property tests (2.5e).

**Honest limits of this research:** no access to vendor internals or real benchmarks; the 0.95 / 0.70 numbers are from one vendor workflow example, not a standard; effects on a real bank's reviewers (fatigue, error rates, turnaround) cannot be tested without them.

## 26. Stable person IDs (Phase 2.5a)

**Problem:** clustering labels (`cl00000012`) were renumbered every run, so no decision, report or downstream reference could point at the same person next week.

**Design (`src/dedupe/identity/ids.py`):** after clustering, every group inherits the ID of the previous group it overlaps most by source records. Greedy, largest overlap first, ties broken by the older ID then the smallest record key (deterministic, independent of the arbitrary labels and input order). Unchanged, grown or shrunk group: same ID. Several old people merge into one group: the oldest ID survives, the others are RETIRED with `successor` pointing at the survivor. One person splits: the largest part keeps the ID, the other parts get new IDs (event `split_from`). New records only: new ID. All records of an ID vanished: retired. IDs (`P` + 9 digits) are strictly increasing and never reused. Registry = three parquet files in `<work>/identity/` (`records`, `persons` with status/created_run/last_run/retired_run/successor, `events` with run, event type, related ID, record count), written atomically. **Keep that folder between runs**; deleting it makes the next run assign fresh IDs. The column in `clusters.parquet`, golden records, review queue and households is still called `cluster_id` but now holds the stable person ID. `write_outputs(..., registry_dir)` does the remap; without a registry (stand-alone use) the old per-run labels remain.

**Tests:** first run deterministic and independent of labels/order; identical rerun changes nothing and logs no events; new records join existing IDs, new groups get new IDs; merge keeps the older ID and retires the other with a pointer; split keeps the ID on the larger part; retired IDs never reused, vanished records retire; equal-overlap ties deterministic; full pipeline rerun in the same work folder gives identical IDs, and appending new records keeps >97% of existing IDs.

**At scale (138,984 records, 60,285 people):** identical rerun: all IDs kept. Rerun with different decision rules (so groups merge): 99.34% of records keep their person ID; 887 people retired with pointers (887 `merged_from` events), 6 `moved_in`. Bookkeeping adds little time (clustering stage unchanged to within noise).

**Caveats:** greedy overlap can pick a surprising survivor when a merge involves two similarly sized groups (the older ID wins ties; documented, deterministic); if records are removed from a source and later return they join whichever person they match, not necessarily the retired ID; the registry is files, not yet the Postgres store (2.5b); steward judgments will also need to reference these IDs only through record keys (decided in section 25).

## 27. Judgment store (Phase 2.5b)

**What it is:** a persistent, append-only log of steward decisions (`src/dedupe/store/judgments.py`, class `JudgmentStore`). Kinds: `same_person` (must-link), `different_person` (cannot-link), `unsure` (snooze, no clustering effect), `split` (parts of a wrongly merged person that must stay apart), `field_override` (steward-fixed golden value, anchored on records of the person), and the meta rows `revoke` (undo), `approve`, `decline`.

**Rules built in:** (1) append-only: no API to edit or delete, and database triggers (SQLite and PostgreSQL) refuse UPDATE/DELETE; (2) keyed by `(source_id, source_record_id)`, never by person ID or group label, so decisions survive re-runs, new data and rule changes; pair order does not matter and repeating a decision is idempotent; (3) a new same/different decision on a pair automatically revokes the opposite earlier one (a changed mind) while the old row stays in the history; `unsure` can never overwrite a decision; (4) four eyes: `requires_approval` decisions have no effect until a DIFFERENT person approves (the proposer cannot), declines need a reason and are permanent; (5) every row stores actor, UTC time, reason and an evidence snapshot (what the steward saw: probability, per-field contributions, versions) and a hash chained to the previous row; `verify()` recomputes the chain and reports the first tampered row; (6) validation: no identical-record pairs, split parts must be disjoint, overrides only for known golden fields (`null` clears a field) and need a reason and an anchor record.

**API:** `same_person / different_person / unsure / split / override_field / revoke / approve / decline`, `active(kinds)`, `pending_approval()`, `pair_state(a, b)`, `history()` (audit view with current status of each row), `verify()`. `JudgmentStore.open(url)` takes a SQLAlchemy URL, default `DEDUPE_DATABASE_URL` or `sqlite:///data/judgments.db`.

**Tests (11):** order independence and idempotence, supersede keeps history, revoke and unsure, four-eyes (self-approval refused, approval activates, no double approval, decline), split/override canonical form and validation, input validation, evidence kept verbatim, DB refuses UPDATE/DELETE, hash-chain tamper detection (after an administrator drops the trigger and edits a row), persistence across reopen.

**Speed (SQLite, this machine):** 2.4 ms per decision written; replaying 5,000 decisions 0.1 s; verifying the chain 0.09 s.

**Not verified / open:** the PostgreSQL path (triggers, `LOCK TABLE` serialisation of writers) is written but untested because Docker/Postgres is not available here; SQLite is single-writer so concurrency was not stress-tested; the log is not yet read by the pipeline (that is 2.5c); a person-level `merge` button in a UI will be expressed as a same-person judgment on a representative record pair, and an UNMERGE as a `split`; no users/roles system yet (the `actor` is trusted text until the API adds authentication in Phase 2.6).

## 28. Judgments inside clustering (Phase 2.5c)

**How decisions are applied** (`src/dedupe/clustering/judged.py`, used by the pipeline when `PipelineConfig.judgments_url` is set): `same_person` becomes an edge heavier than any automatic one (weight 2.0 + a tiny bonus per judgment id, so if two judgments truly contradict, the older one yields and the newer wins); `different_person` becomes a forbidden pair; `split` becomes a separation between its parts, with glue edges (weight 1.5) inside each part so the cut falls BETWEEN the parts instead of inside one; a rule veto on exactly the pair a steward declared the same person is dropped (steward beats automation); `unsure` changes nothing but keeps the pair out of the queue; records no longer in the data are ignored and listed as `missing`. Identifier conflicts: a same-person decision that would join two different valid PANs or Aadhaar numbers is NOT applied and is reported (`identifier_conflict_not_confirmed`); `store.same_person(..., confirm_id_conflict=True, reason=...)` confirms it explicitly (the pair's identifiers are then ignored by the contradiction guard).

**Audit:** after clustering every judgment is checked; `clusters/judgment_report.json` lists decisions that did not hold and why (`identifier_conflict_not_confirmed`, `contradicts_a_newer_judgment_or_a_split`, `kept_together_by_automation`, `parts_still_together`), and the pipeline summary carries held / not_held. **Review queue:** pairs a steward decided or marked unsure are excluded (cluster pairs left empty disappear).

**Identity fix found on the way:** after a merge and an undo the person split back out received a brand-new ID (retired IDs are never reused). The registry now keeps each retired ID's last records (`retired_members.parquet`) and reactivates the ID when those records come back together (Jaccard >= 0.5), so undoing a merge restores the original IDs; a retired ID never goes to a different group.

**Tests (122 total, +13):** clustering core (steward links, forbidden pairs cut at the weakest link, splits keep parts whole, newer steward decision wins, identifier conflict refused unless confirmed, unchanged behaviour without judgments); store -> constraints -> audit; veto override; pipeline: join + split applied and everyone else keeps their person ID, revoking both returns EXACTLY to the baseline (same groups and same person IDs, ids reactivated), a decided pair leaves the queue, refuse-then-confirm across different PANs; identity: undone merge gets its IDs back, a different person never gets a retired ID.

**Scale check with a simulated reviewer (138,984 records, SYNTHETIC; the oracle knows the truth and is never wrong):** it decided the 3,000 highest-priority review items (2,824 same person, 176 different). Before: P 0.9960, R 0.8480, F1 0.9160, queue 3,525. After: P 0.9961, R 0.8862, F1 0.9379, queue 577; all 3,000 judgments held; 57,497 of 57,498 people kept their person ID (2,788 retired by merges, 1 new from a split); second run 77 s. The ideal-reviewer ceiling for this data is F1 0.941.

**Caveats:** a real reviewer makes mistakes and takes time (not modelled here; 2.5e adds a noisy reviewer and budget curves); steward decisions are applied at the record level, so a judgment about a record is lost (reported as `missing`) if the record disappears from the source; field overrides are stored but not yet applied to the golden record (2.5d); no UI or API yet; performance with hundreds of thousands of judgments is untested (replay cost is linear: 0.1 s per 5,000).

## 29. Review service (Phase 2.5d)

**What it is** (`src/dedupe/review/service.py`): `ReviewService(work_folder, judgment_store, policy)`, plain Python calls that read the outputs of a pipeline run and write judgments; the next run applies them. Addressing uses the stable person IDs.
- `queue(limit, offset, status, min_probability)`: ranked person pairs (priority order from `review_queue.parquet`). `status` = `open` / `unsure` / `all`. Anything a steward already decided disappears immediately, before the next run.
- `explain(person_l, person_r)`: both people side by side (golden values, member records, conflict fields), the strongest record pair's evidence split into `why_match` / `why_not` (per-field contributions in bits with level labels), probability, which rule or score zone sent it to review, hard identifier conflicts, existing judgments. Sensitive fields (PAN, phones, email, documents) are masked unless the policy allows revealing AND the caller asks.
- Actions: `approve_match`, `reject_match`, `mark_unsure`, `split_person`, `override_field`, `undo_action`, `bulk_approve`, `bulk_reject`. Every judgment stores an evidence snapshot (probability, top evidence, rule that decided, pipeline commit, persons, `action_id`). Merging people with different valid PANs/Aadhaar is refused unless `confirm_id_conflict=True` with a reason, and then needs a second approver by default.
- Four eyes: `ReviewPolicy` (merge size threshold, splits, identifier-field overrides default on, ID conflicts default on); `pending()`, `approve_pending()`, `decline_pending()`; the proposer cannot approve their own decision.
- `summary()` for a dashboard (judgments by kind, pending approvals, chain verified).

**Golden overrides:** `build_golden(..., overrides)` applies active `field_override` judgments (anchored on records of the person; newest wins; null clears); lineage shows `steward_override` / source `steward`. The pipeline reads them from the store.

**Speed at 138,984 records (SQLite, this machine):** queue page 0.03 s; explain 0.3 s; 300 bulk rejects 10 s (about 34 ms per item, 8 judgments each on average, dominated by store writes); queue after 300 decisions 0.05 s. Found and fixed on the way: the first version re-scanned the big scored-pairs and clusters files for every item (35 s per 300).

**Tests (131 total, +9):** ranked/paged/filtered queue; explain structure and masking (policy gates reveal); approve + reject + split + override all take effect on the next run, decided items leave the queue at once and a rejected pair does not return; undo restores the item (immediately and after a run); unsure snoozes and is listed separately; four-eyes (pending has no effect, self-approval refused, approval takes effect after a run, identifier overrides wait); identifier conflict refused / confirmed / needs a second approver; bulk actions report failures and are individually undoable; summary and input validation.

**Caveats:** masking is a convenience, not access control (users, roles and authentication come with the API, Phase 2.6); `actor` is trusted text; explain latency at 694k records is untested (expected about 1 s, it scans standardised/clusters for two people); `summary().open_now` is computed only for small queues; a reject writes up to 500 judgments (one per review pair) so bulk rejects of very large queues should be batched; approve merges via the best record pair only, so if that pair is later invalidated the merge is not held by other pairs; no UI yet.

## 30. Simulated reviewer: how much does review help, and in what order? (Phase 2.5e; SYNTHETIC)

**Tool:** `python -m dedupe.review.simulate --work <run folder> --truth <synthetic folder> --out docs/REVIEW_SIMULATION.md` (`src/dedupe/review/simulate.py`). It loads a finished run once, lets a simulated reviewer (always right, or wrong 5% / 10% of the time) decide the first N items of a review ORDER, applies the decisions exactly as the pipeline does (same-person: heavy edge on the best pair; different-person: forbidden pairs for every review pair), re-runs only the clustering step and scores against the truth. Orders: `priority` (the queue), `probability`, `uncertainty` (closest to 0.5 first, classic active learning), `expected_gain` (probability x record pairs connected), `random`, `best_possible` (real matches first: a ceiling for selection). Two yardsticks: pairwise F1 and "customers recovered exactly" (people with 2+ records whose records all sit in one cluster that holds nobody else).

**Result on 34,816 records (5,000 households, noise 1.0), queue 951 person pairs of which 92% are real (the earlier queue fix), perfect reviewer:** pairwise F1 0.9093 before review, 0.9388 if everything is reviewed. After 200 reviews: priority (new) 0.9188, expected_gain 0.9206, probability 0.9152, random 0.9157, uncertainty 0.9150. 80% of the possible gain needs 600 reviews with expected_gain, 700 with the new priority, 800 with probability / uncertainty / random. On customers recovered exactly the orders are within 0.3 points of each other and `priority` / `probability` are slightly ahead of expected_gain. A reviewer wrong 10% of the time costs only about 0.3 F1 points after all 951 reviews (0.9356 vs 0.9388): most decisions confirm what the model already believed.

**Queue priority tuned:** a damped size term was added (`SIZE_WEIGHT` 0.2 x ln(size_l x size_r)): bigger people matter more for the pairwise metric, while keeping the customer-level result. Blends 0.02 / 0.05 / 0.1 / 0.2 were measured; 0.2 came closest to expected_gain on F1 (0.9265 vs 0.9282 at 400 reviews, old order 0.9225) with no loss on customers recovered (0.7430 vs 0.7425).

**Honest findings:** (1) the order matters little once the queue is 92% real: the earlier queue fix (minimum-evidence rule) did most of the work; (2) review is NOT the main accuracy lever: even a perfect reviewer lifts customers recovered exactly only from 71.0% to 78.4%; (3) where the rest goes (8,081 same-person record pairs split across clusters): 70.7% were never candidate pairs (blocking missed them: mostly kyc+leads, insurance+leads, insurance+kyc, bank_crm+leads), 18.1% are in the review queue, 10.8% were rejected as not enough evidence, 0.2% low score, 0.2% auto-merge wrongly split by a veto/guard. So candidate generation for thin records (leads, KYC documents) is the biggest remaining accuracy lever, not review or scoring; (4) `best_possible` is not the best order for pairwise F1 because it ignores merge size (expected_gain beats it).

**Property tests (`tests/test_review_properties.py`, 7 new, 138 total):** decisions are order independent and deterministic; a different-person decision can only split (the new partition refines the old one) and never merges; a same-person decision only joins when no veto / identifier conflict is involved (the old partition refines the new one); a perfect reviewer never lowers F1 and more reviews never help less; a decision that was wrong and then corrected gives the same clustering as the right decision made once, while the audit trail keeps both; every review order is a valid permutation; simulation numbers are internally consistent (no reviews = same start, noise never helps, exact-person share is 1.0 for the truth).

**Caveats:** one data set (seed 7, 5,000 households, noise 1.0); the simulated reviewer knows the truth and has no fatigue, disagreement or time cost; reviews are applied in one batch, not iteratively (iterative re-ranking after each batch was not simulated); queue-level truth uses 'any record pair is the same person'; synthetic traps (twins, namesakes) are present but real-world reviewer error is unlikely to be random.

## 31. Blocking misses, age evidence and what is left (SYNTHETIC)

**Question:** section 30 found that 70.7% of same-person record pairs that end up in different clusters were never candidate pairs. Is blocking the problem?

**Diagnosis (34,816 records):** of 5,710 such pairs, 85% have the same phonetic first and last name, but 62% have no birth year on at least one side and 70% have no city on at least one side: typically a KYC document or lead with a name and nothing else. Candidate rules measured against the missed pairs (new pairs created / share of them real / missed pairs recovered): name + city + 5-year bucket 1,496 / 58% / 7%; name + bucket (no city) 15,428 / 17% / 20%; any full name with block size <= 50: 130,643 / 4.7% / 60%. Widening blocking enough to reach most of them nearly triples the candidate pairs and 95% of the extra pairs are different people, and the records carry no evidence that could justify a merge. **Conclusion: the bulk of the 70% is an information limit, not a blocking bug.** Do not widen blocking further.

**What IS recoverable: age.** Leads have an age (exact or a band such as `50-59`) that was ignored. Built end to end: canonical fields `age` and `record_date` (mapped for leads, bank_crm, insurance, loans in `configs/synthetic_sources.json`), standardised `birth_year_min/max`, DOB comparison levels `age-based: compatible` / `age-based: incompatible`, fixed weights (the levels never occur in identifier-seeded pairs, so they cannot be learned: weight = log(m/u), m = 0.93 / 0.07 is an ASSUMPTION about how reliable a stated age is, u measured on random pairs where age is the only birth information; a first version measured u over all pairs and over-weighted age at 4.3 bits instead of about 2.6), blocking rule `name_city_age`, and rules (see decisions log).

**Result at 34,816 records (5,000 households), before -> after:** precision 0.9976 -> 0.9982; wrong automatic merges 85 -> 56; recall 0.8354 -> 0.8446; F1 before review 0.9093 -> 0.9150; F1 if everything is reviewed 0.9388 -> 0.9465 (+0.8); customers recovered exactly after full review 78.4% -> 80.6%; review queue 951 -> 1,150 person pairs (92% -> 84% real). **At 138,984 records:** F1 before review 0.916 -> 0.9212, precision 0.996 -> 0.997, F1 after full review 0.9409 -> 0.9494, queue 3,525 -> 5,474 items (about 66% real). Held-out benchmark (8k records, regenerated): mean F1 0.921 / 0.915 / 0.880 at noise 0.5 / 1 / 2 (was 0.910 / 0.911 / 0.879).

**Lessons recorded:** (1) a first version that treated age-only pairs like any evidence doubled the queue at 139k with only 51% real, because many different people share name + city + a compatible age: age-only evidence is gated by score (>= 0.8 to enter review, never auto-merge); (2) a ranking choice tuned at 35k (size weight 0.2) lost to plain probability at 139k; (3) inserting a setting into `PipelineConfig` silently broke the pipeline command line and the benchmark (positional arguments); both now use keywords and have tests (`tests/test_entrypoints.py`).

**Cost of the extra recall:** about 20-50% more review items depending on size. Reviewers can cap the queue (`review_min_probability`, `review_cap`) or disable the age rules in `configs/decision_default.json`.

**Caveats:** the age reliability (m = 0.93) is an assumption; the synthetic age noise (+-1 year, decade bands) is mild and real lead ages are often stale or wrong; `docs/REVIEW_SIMULATION.md` was regenerated with the final code (section 30 numbers describe the version before the age work); not re-run at 694k records; thin-record accuracy is still bounded by the information in the records.

## 32. Code-review fixes to the age work, and corrected numbers (SYNTHETIC)

A code review of commit 7e3314b found ten problems; all fixed, each with a test (153 tests):
1. **Synthetic generator bug (affected all section 31 numbers):** a lead's age was the person's age on the data's reference date, but the lead's `created_at` (up to 900 days earlier) was used to turn it back into a birth year, pushing birth years 0-3 years too early and scoring some true matches as "age-based: incompatible". The age is now measured on the lead's own date; a test checks every synthetic lead's age against its true birth year.
2. **Record dates with a time** (`2023-04-01 00:00:00`, `2023-04-01T10:22:00Z`, `01/04/2023 10:22`, Excel datetimes) were not understood and silently replaced by the current year. New `parse_record_date`; the data-quality report now counts unreadable ages and record dates per source.
3. **Hard-coded "today" (2026-10-01):** replaced by the clock, or `DEDUPE_REFERENCE_DATE` for reproducible re-runs. An age without any record date now gets 3 extra years of slack (it may be stale). The twin guard's fixed 2008 cut-off is now `both_born_within_last: 18` (was a known gap).
4. **Rule precedence:** `review_age_only_evidence` (force_review) could override the minimum-evidence reject for pairs with no shared place; it now requires a strong name and the same city/PIN, like the evidence rule it refines.
5. **Older canonical files** without the new `age` / `record_date` columns crashed standardisation; missing columns are now treated as empty.
6. **Age weights keyed by level number** would silently land on the wrong level if the DOB ladder were renumbered; now keyed by label, with an error if a label is missing.
7. **`name_city_age` blocking** also paired two documents with full birth dates (namesakes born a few years apart); it now only pairs records where at least one side has no birth year (new generic `Rule.one_side`).
8. **`priority_offset`** on a non-force_review rule was silently ignored; now rejected when the config is loaded.
9. **Age formats:** `90+` was dropped (now 90-110), `35 yrs`, `35.0`, `50 to 59` are understood.
10. **`Comparison.blocked_by`** was unused and out of date; removed.

**Corrected measurements (fixed generator, seed 7):**

| 34,744 records (5,000 households) | without age | with age |
|---|---|---|
| precision / recall | 0.9982 / 0.8445 | 0.9992 / 0.8533 |
| F1 before review | 0.9149 | **0.9205** |
| wrong automatic merges | 72 | **44** |
| review queue (person pairs, % real) | 806 (93%) | 1,055 (81%) |
| F1 after full perfect review | 0.9403 | **0.9482** |
| customers recovered exactly after full review | 78.2% | 80.4% |

At 139k records (20,000 households), with age: precision 0.9973, recall 0.8556, F1 0.9211, 337 wrong automatic merges, queue 5,641 person pairs (66% real), F1 after full perfect review 0.9496 (81.0% of customers recovered exactly); peak memory 1.1 GB, 117 s. Held-out benchmark mean F1 0.933 / 0.916 / 0.882 at noise 0.5 / 1 / 2. `docs/REVIEW_SIMULATION.md` regenerated at 35k records (priority order is close to the best possible order; with a 5% reviewer error rate the full queue reaches F1 0.946).

**Honest notes:** the generator change alters the data, so these numbers are not directly comparable with section 31 (the without/with-age comparison above is on identical data). The age gain is real but modest and costs about 30% more review items, with a lower real share (81% vs 93%); at 139k the queue is only 66% real. The 0.93 age reliability is still an assumption.

## 33. Versioned, audited configuration (roadmap 2.2 and 2.3)

**Why:** business users will change rules, thresholds and settings; a bank or insurer must be able to answer "who changed what, when, why, who approved it, and what was live when this customer was merged". Research (Reltio audit trail / change requests, rules-engine versioning practice: immutable versions, draft vs published, test before publishing, every decision records the version that made it) matched what the judgment log already does for steward decisions, so the same design was reused.

**What was built (`src/dedupe/governance/`):**
- `ConfigStore` (`store.py`): table `config_log` (append-only by database triggers, hash-chained, `verify()`), kinds propose / approve / decline / activate / rollback. Versions are numbered per config name and never edited. A proposal is a draft with no effect. Activation needs an approval from someone other than the proposer (`ConfigStore.open(..., solo=True)` or CLI `--solo` turns this off, development only). Rollback to a version that was live before publishes its content as a new version without fresh approval (it was approved once; the rollback is logged with a reason). Content is validated with the same pydantic models the pipeline uses and stored in full canonical form (defaults filled in), so identical content has an identical hash; proposing unchanged content is refused.
- Config names: `decision` (rules, thresholds), `survivorship` (golden-record rules), `settings` (`MatchingSettings`: which blocking rules run, blocking cap, household threshold, cluster-size limit, review queue limits). Blocking rules can now be chosen by name (`blocking.rules_by_name`).
- `describe_changes` (`diff.py`): plain-language list of what changed between two versions (rules added / removed / disabled / condition changed / reordered, thresholds, generic setting changes).
- `dry_run` (`impact.py`): puts the last run's scored pairs through a candidate decision config and compares with the decisions that run made: zone counts before and after, moves between zones, which rules' influence changed, example pairs. Needs no ground truth. Tested to be EXACT: the predicted zone counts equal those of a real pipeline run under the new config. Limits: decision config only (a settings change that alters blocking or scoring, or a golden-record change, needs a full run); clusters and the review queue are not re-run.
- `resolve` + pipeline: `PipelineConfig.config_store_url` / `--config-store-url`. Active store versions override the config files (and settings override the pipeline fields); with no active version the file is used. Each run writes `configs` (source, version, hash, effective settings) to `pipeline_summary.json` and copies to `<work>/config_used/`. Review-service evidence snapshots include the same versions and hashes.
- CLI: `python -m dedupe.governance propose | approve | decline | activate | rollback | diff | list | history | verify | impact`.

**Tests (11, `tests/test_governance.py`):** draft/approve/activate with four eyes, solo mode, refusal of invalid / empty / unchanged proposals, database refuses edits and deletes, tamper detection, decline and rollback and full history, independent versioning per name, plain-language diff, a run records exactly which configuration it used (and fewer blocking rules means fewer candidates), dry run equals the real run, dry run without a previous run, command-line workflow.

**Honest limits:** the `actor` is trusted text until the API has authentication (so "four eyes" is a workflow control, not security, until then); Postgres path (LOCK TABLE, triggers) not yet run against a real Postgres; household evidence weights, source mappings and the SQL of blocking rules themselves are still code or files, only the on/off choice of blocking rules is versioned; no approval roles (anyone other than the proposer may approve); no scheduled or staged rollout.

## 34. Research before the API (roadmap 2.6) and the adjusted plan

**Sources consulted (web search, summaries only, no vendor internals):** FastAPI security practice write-ups (OAuth2 bearer tokens, OIDC with JWKS validation, role/scope checks as route dependencies), MDM stewardship and governance material (least privilege, segregation of duties between creators and approvers, audit log for every change, masking by policy, APIs that enforce identity, scope, rate limits and data minimisation), and REST patterns for long-running jobs (202 Accepted + status resource, idempotency key, separate worker rather than in-process background tasks for heavy work).

**What this means for us, compared with the original plan:**
1. **Identity comes from the customer's identity provider, we do not build a user database or password login.** Banks and insurers use SSO (OIDC/SAML through their IdP or a broker). The API validates the bearer token (signature via the provider's key set, issuer, audience, expiry, allowed algorithms) and reads the user's name and roles from claims. A local development token mode (secret from an environment variable, refused when `DEDUPE_ENV=production`, same pattern as the Aadhaar token key) keeps tests and demos possible. This also turns the trusted-text `actor` of the judgment log and config log into a verified identity.
2. **Roles are explicit and checked on every route:** viewer (read masked), steward (decide pairs, split, override fields), approver (second pair of eyes on pending decisions and config changes), rule_admin (propose rule/setting changes, run dry runs), auditor (read logs and unmasked evidence, cannot change anything), platform_admin (run pipelines, manage sources). Segregation of duties: nobody approves their own change (already enforced in both logs; the API makes the identity real).
3. **Masking becomes access control:** today masking is only a convenience. Unmasked PAN / phone / email will need a role and every unmasked read is written to an access log (who looked at whom), which regulators ask for.
4. **Runs are jobs:** `POST /runs` returns 202 with a run id, `GET /runs/{id}` reports status and stage progress, an idempotency key prevents double submission, one run at a time per workspace. The worker is a separate process (subprocess now, a queue later) because a pipeline would block the web server.
5. **Postgres persistence of pipeline outputs is moved to Phase 4.** The API can serve from the parquet outputs already (queue 0.03 s, explain 0.3 s at 139k records); the judgment and config logs are already in the database. Persisting outputs matters for incremental matching and many concurrent users, not for a first API. (Honest: this reorders the original roadmap.)
6. **Small but needed:** request validation (pydantic), pagination, rate limiting, security headers, no PII in logs or error messages, consistent error format, OpenAPI docs.

**Not covered by this research:** actual customers' IdP setups (SAML-only shops need a broker), penetration testing, legal review, performance under many users.

**Proposed order:** 2.6a auth + roles + access log (with tests using dev tokens and a fake JWKS), 2.6b read endpoints, 2.6c write endpoints, 2.6d run jobs.

## 35. Security research: standards, Indian requirements, what we must build, what we can never claim

**Method and caveat:** web searches (OWASP API Security Top 10 2023, OWASP ASVS 5.0, RBI / CERT-In / IRDAI / DPDP / UIDAI summaries, secure-SDLC checklists). Most Indian-regulation facts below come from SECONDARY sources (law-firm and vendor write-ups, some dated 2026) and MUST be checked against the primary texts (RBI directions, CERT-In directions, IRDAI guidelines, DPDP Rules, UIDAI circulars) by the customer's compliance team or counsel before anyone relies on them. Nothing here is legal advice.

**Industry standards to design and test against**
- OWASP API Security Top 10 (2023): broken object-level authorization (BOLA), broken authentication, broken object-property-level authorization (field exposure / mass assignment), unrestricted resource consumption, broken function-level authorization, server-side request forgery (SSRF), security misconfiguration, unrestricted access to sensitive business flows (automation), improper asset management, unsafe consumption of other APIs.
- OWASP ASVS 5.0 (May 2025): target Level 2 for the whole application (the standard level for business apps with sensitive data) and Level 3 for the parts that handle identifiers (vault-like areas), verified by an independent tester.
- Secure-SDLC practice: static analysis and dependency scanning on every change, secret scanning (pre-commit + CI + history), SBOM (CycloneDX/SPDX) per build, container image scanning, pinned dependencies, rebuild on critical CVEs.

**Indian requirements reported by secondary sources (verify!)**
| Source | Requirement relevant to us |
|---|---|
| RBI cybersecurity / technology-risk directions for commercial banks (reported as issued 31 Jul 2026, replacing earlier circulars) | VA every 6 months, annual penetration test of critical internet-facing systems, half-yearly DR drills; central log aggregation with 180 days online and longer archive, SIEM and 24x7 SOC; encryption in transit (TLS 1.2+) and at rest (AES-256), keys in an HSM; incident reporting within 6 hours; data-localisation expectations |
| CERT-In Directions 2022 | report cyber incidents within 6 hours; keep ICT logs 180 days within India; synchronise clocks (NTP) |
| IRDAI information and cyber security guidelines (reported: 2023, replaced April 2026) | encrypt sensitive personal data in transit and at rest (AES-256 or equivalent, TLS 1.2+), stronger responsibility for third-party vendors |
| DPDP Act 2023 + Rules 2025 | "reasonable security safeguards" (encryption, masking, obfuscation, access control), breach intimation to affected people and a report to the Data Protection Board within 72 hours, logs and processing records kept at least a year; heavy penalties |
| UIDAI Aadhaar Data Vault rules | each Aadhaar number must be represented by a random REFERENCE KEY mapped only inside an Aadhaar Data Vault (ADV); HASHING IS NOT PERMITTED as the reference-key method; vault contents encrypted with keys in an HSM; ADV in an isolated network zone with access logging and alerts; full Aadhaar numbers must not be stored outside the vault |

**Finding that contradicts a current design choice (needs a decision):** we replace each Aadhaar number with a deterministic keyed token (HMAC) so that two records with the same Aadhaar can be matched. UIDAI's published position, as reported, rejects hashing as a way to make reference keys and expects a vault with random keys. A keyed HMAC is not a plain hash, but it is deterministic and we cannot claim UIDAI compliance. Options: (1) customers feed us THEIR reference keys (from their own ADV) instead of Aadhaar numbers, which still lets us match within one customer; (2) we run inside the customer's vault-governed zone and treat the token as a vault-managed value; (3) drop Aadhaar as a matching key. Recommendation: support (1) now (a canonical field for reference keys, same strong-ID weight), document (2), and get a compliance opinion before real Aadhaar numbers are processed.

**What we must build ourselves (application level, in 2.6a unless noted)**
1. Authentication: verify tokens from the customer's identity provider (signature via key set, issuer, audience, expiry, allowed algorithms only, no "none"), short-lived tokens, dev-token mode refused in production.
2. Authorization on every route with a role matrix and an automated test that tries every route as every role (function-level), plus object-level checks (a user may only act on records inside their scope: workspace now, business-unit scope later) and explicit field allow-lists for requests and responses (no mass assignment, no accidental PII in responses).
3. Resource limits: request size, page size caps, bulk-action caps, rate limiting per user and per route, timeouts, one pipeline run at a time, idempotency keys.
4. Input validation with strict pydantic models (reject unknown fields); consistent errors that never echo PII or internals; no stack traces to clients.
5. Access log of PII views (who viewed which person, which fields unmasked) and security event log (login failures, permission denials, config changes, exports), structured JSON, no PII in log text, tamper-evident (same hash-chain pattern), forwardable to the customer's SIEM, clock-synchronised timestamps.
6. Security headers, strict CORS (allow-list), TLS required outside development, cookies not used for auth.
7. Secrets only from environment or a secret manager; startup refuses insecure defaults in production (dev token key, dev signing key, sqlite).
8. Connector hardening (existing attack surface, to review in 2.6a): file connector path traversal, SQL connector (user-supplied queries or URLs, least-privilege read-only accounts, no multi-statement), Google Sheets / S3 / URL connectors (SSRF: block private and metadata addresses, allow-list hosts), CSV/Excel formula injection when exporting.
9. Delete / retention: DPDP erasure and retention rules need a defined way to erase a person from staging, outputs and logs (the append-only judgment log conflicts with erasure: design needed, e.g. keep keys not names, crypto-shredding). Not built.
10. Encryption at rest for staging and output files (envelope encryption: data key per file, key-encryption key in a KMS/HSM; AES-256-GCM) and database-level encryption (Phase 4 item, PULLED FORWARD as a gate, see below).

**CI / build-time controls to add (2.6a):** bandit and ruff security rules, `pip-audit` on pinned dependencies, secret scanning (gitleaks pre-commit + CI), CycloneDX SBOM artifact, container image scan (Trivy) once Docker works, dependency update bot, signed images later. Authorization-matrix, token-forgery (expired, wrong audience, wrong algorithm, tampered, unsigned) and BOLA tests run in the normal test suite.

**Deployment-side controls (customer / operations, documented not built):** TLS termination with 1.2+, WAF or API gateway in front, network segmentation and a restricted zone for identifier data, database and disk encryption, HSM/KMS, SIEM and SOC, log retention and India residency, backups and DR drills, vulnerability assessments and an annual independent penetration test (by a CERT-In-empanelled auditor where the regulator requires), incident-response runbook with the 6-hour (CERT-In, RBI) and 72-hour (DPDP) clocks, vendor-risk and outsourcing approvals.

**What can NEVER be claimed from engineering alone:** "most secure", certifications (ISO 27001, SOC 2, CERT-In empanelment), or "penetration-proof". Penetration testing must be done by an independent party on a deployed system; certifications need organisational processes and audits. What we can do is design to the standards above, test the controls automatically, publish a threat model, and fix what independent testing finds.

**Gates before ANY real customer data touches the system:** (1) encryption at rest for staging/output files and database, (2) role-based access and PII access logging in place, (3) Aadhaar approach decided and reviewed (option 1 above), (4) independent penetration test and fixes, (5) legal/compliance review (DPDP, RBI, IRDAI, UIDAI), (6) retention/erasure design, (7) incident-response and log-retention procedures agreed with the customer.

**Plan adjustments:** 2.6a grows from "login and roles" to "security baseline + login + roles" (items 1-8 above, CI scanning, authorization-matrix and token tests, `docs/SECURITY.md` with the threat model and the control-to-standard mapping). Encryption at rest moves from Phase 4 to just before the first real-data pilot (gate 1) and is scheduled right after the API. Reference-key support for Aadhaar is added to the ingestion schema in the same step as the connector review.

## 36. API security baseline (roadmap 2.6a)

**Built (`src/dedupe/api/`, `src/dedupe/connectors/guard.py`, `docs/SECURITY.md` has the full control table, gaps and configuration):**
- `settings.py`: everything from environment variables; production refuses dev tokens, wildcard hosts, plain http, sqlite, non-https JWKS, `*` CORS, shared-secret algorithms; docs and OpenAPI are off in production unless asked.
- `auth.py`: `TokenVerifier` (signature through the provider's published keys or the dev secret, issuer, audience, expiry, not-before, required claims, maximum lifetime, algorithm allow-list, role mapping from a configurable claim). Reasons for refusal go to the log, the caller sees one generic message.
- `roles.py`: one permission table; roles viewer / steward / approver / rule_admin / auditor / platform_admin with separated duties.
- `guards.py`: every route must declare `public()`, `authenticated()` or `require(permission)`; per-user and failed-login rate limits.
- `middleware.py`: request id, client address (trusted proxy only when configured), https enforcement, per-address rate limit, request-size cap (declared and streamed), security headers, catch-all that returns a generic 500 and logs only the error type and frames.
- `seclog.py`: `security_log` (append-only triggers, hash chain, scrubbing of PAN / Aadhaar / email / phone, allow-listed detail keys, JSON lines for a SIEM).
- `app.py` routes so far: `/health` (public), `/me`, `/roles`, `/security/events`, `/security/verify`. Run: `python -m dedupe.api`; dev token: `python -m dedupe.api.devtoken --sub asha --roles steward` (needs `DEDUPE_DEV_AUTH_SECRET`).
- Connectors: path confinement, SELECT-only single statement and plain identifiers, PostgreSQL read-only transactions, literal DB URLs refused in production, Google Sheets id/gid validation, safe redirects, size cap.
- Aadhaar reference keys (`aadhaar_reference_key` -> token `ref:<key>`), old canonical files without new columns still standardise.
- Tooling: `scripts/security_scan.py` (ruff, bandit, pip-audit, SBOM), `.github/workflows/ci.yml` (tests, scans, gitleaks, SBOM artifact, Trivy image scan), `.pre-commit-config.yaml`, multi-stage Dockerfile (non-root runtime image with health check), compose `api` service (localhost only, read-only filesystem, capabilities dropped), `.env.example` security section.

**Tests (55 new):** token forgery and expiry matrix; key-confusion; role matrix across every route and role (also catches any future unguarded route); duties-apart check; headers, host, https, CORS, docs off in production; body limit with and without a declared length; rate limits; validation errors without values; generic 500 without the message; security log denial/failed login/tamper/append-only/pagination/scrubbing; connector path, SQL, sheet and redirect checks; Aadhaar reference keys. A real server run (uvicorn + curl) confirmed headers, no server banner, 401 and the log rows.

**Scan results (local, 2026-10-06):** bandit clean after audit (96 B608 sites reviewed and skipped by configuration, see pyproject), pip-audit found only old pip/setuptools in the dev environment (upgraded), no known vulnerabilities in dependencies, SBOM generated.

**Honest limits:** see `docs/SECURITY.md` section 3 (encryption at rest not built, object-level authorization and masking enforcement wait for the endpoints, no token revocation, in-memory rate limits, erasure design missing, Docker/CI/Postgres not executed here, no independent test). This is a baseline that follows the standards; it is not evidence of security.

**Next:** 2.6b read endpoints (queue, person, golden record, runs) with object-level checks, response allow-lists, masking enforced by role and the unmask access log; then 2.6c write endpoints; 2.6d run jobs; then encryption at rest before any pilot.

## 37. Read endpoints (roadmap 2.6b)

**Routes (all GET, all guarded; `src/dedupe/api/routes_read.py`):** `/runs/latest` (run summary: counts, stage times, config versions and hashes; no evaluation or truth data), `/queue` (paged, `status` open/unsure/all, `min_probability`), `/queue/summary`, `/people/{id}`, `/people/{left}/compare/{right}` (why they match, why not, hard identifier conflicts, existing judgments), `/config/{name}`, `/config/{name}/versions/{n}`, `/config/{name}/diff?old&new`, `/config/{name}/history`, `/config-log/verify`.

**Controls:**
- Responses are built from explicit models; a test pins the exact field set of every response type.
- Two review-service instances: one that can never reveal identifiers (used by default) and one used only after the unmask checks, so a coding slip cannot leak by default.
- `unmask=true` needs the `pii.unmask` permission (stewards, auditors), a reason of at least 10 characters, and stays within `DEDUPE_UNMASK_LIMIT_PER_MIN` (default 30 per user); every unmasked view is logged as `pii_unmasked` (actor, person ids, the list of fields, the reason, which view). The log never contains the values (tested: the PAN string appears nowhere in the log). A viewer's attempt is logged as `permission_denied`.
- Person ids must match `P` + 9 digits; unknown ids and missing runs return the same generic 404; no echo of input; page size, offset and probability are range-checked and the page size is capped by the server.
- The automatic role-by-route matrix test covers the new routes without changes.

**Honest limits:** names, dates of birth and addresses are visible to every `person.read` holder (policy question for the customer); no business-unit scoping; reasons typed by users are scrubbed for PAN/phone/email patterns but are free text; the unmask rate limit is in memory per process; queue reads rescan parquet files per request (fast at 139k records, untested at millions); no caching or ETag.

**Next:** 2.6c write endpoints (approve/reject/unsure/split/override/undo, bulk, pending approvals, config propose/approve/activate/rollback/dry-run) acting as the verified user, with field allow-lists, idempotency keys, bulk caps, logged config and judgment changes; then 2.6d run jobs.

## 38. Write endpoints (roadmap 2.6c)

**Built (`src/dedupe/api/routes_write.py`, `tests/test_api_writes.py`):**
- Decisions (`judgment.write`): `POST /decisions/approve|reject|unsure`, `/people/{id}/split`, `/people/{id}/override`, `/decisions/{action_id}/undo`, `/decisions/bulk` (max 100 pairs, each decided alone, failures listed per pair).
- Four eyes (`judgment.approve`): `GET /approvals`, `POST /approvals/{id}/approve|decline`. The store compares identities, so a person holding steward + approver still cannot approve their own decision.
- Rules (`config.propose` / `config.approve` / `config.activate`): `POST /config/{name}/propose`, `/versions/{n}/dry-run` (decision rules only, exact, nothing changes), `/approve`, `/decline`, `/activate`, `/config/{name}/rollback`. Activation applies from the NEXT pipeline run.
- The actor is always the verified token identity; bodies are strict (unknown fields refused, sizes capped, `field` is a fixed list, person ids validated).
- Idempotency: optional `Idempotency-Key` header. The action id is a hash of caller + route + key + body, so a retry returns the first answer (`replayed: true`) and a reused key with a different body is a new action, never a silent drop.
- Every change writes a `judgment_change` / `config_change` row to the security log (who, what, which object, counts). Reasons and values (e.g. a new PAN) are never written to it; they live in the judgment log, the audited record.
- Errors: conflicts return 409 with our own message; invalid configs return 422 naming fields and error types, never values; a split naming a foreign record does not echo it.

**Honest limits:** replay detection scans the judgment history (fine for thousands of decisions, to be indexed later); bulk approve cannot confirm identifier conflicts (by design); no business-unit scoping; the in-memory rate limit still applies per process; no CSRF concern while auth is a bearer header, revisit if cookies are ever used by the UI.

**Next:** 2.6d run jobs (202 + status, one run at a time, separate worker), then 2.7 thin web UI, then encryption at rest before any real data.

## 39. Run jobs (roadmap 2.6d)

**Built (`api/jobs.py`, `api/routes_runs.py`, `tests/test_api_runs.py`):**
- `POST /runs` (`runs.start`, platform admin) returns 202 at once; `GET /runs` and `GET /runs/{id}` (`runs.read`) show status, who started it, times, exit code. States: queued, running, succeeded, failed, interrupted.
- The run is a separate OS process (`python -m dedupe.pipeline --database-from-env`), so a crash or memory spike cannot take the API down. State is in the `run_jobs` table; ONE run at a time is enforced from the database. The caller sends only a reason and an optional `Idempotency-Key` (same key = same job, `replayed: true`); sources, work folder, database and configuration are fixed by the operator (`DEDUPE_PIPELINE_SOURCES`, `DEDUPE_WORK_DIR`, `DEDUPE_DATABASE_URL`). Without `DEDUPE_PIPELINE_SOURCES`, starting a run is refused.
- The command is a fixed argument list (no shell). The database URL travels in the child's environment, not on the command line; the child does not receive `DEDUPE_DEV_AUTH_SECRET`. The child's output goes to `<work>/jobs/<id>.log`, never served by the API (it can hold record identifiers).
- Outputs are rewritten in place, so while a run is active the read endpoints answer 503 with Retry-After rather than showing a half-written state. Starting and finishing a run are written to the security log (`run_started`, `run_finished` with exit code only).
- A restarted API marks jobs it finds `running` as `interrupted` (their supervising thread died with the old process).

**Honest limits:** an interrupted job's child process may still be running (it is not killed on API restart; a real deployment should run the worker under a supervisor/orchestrator and this table becomes its queue); no cancel endpoint yet; no run scheduling; in-place output rewriting means reads are unavailable during a run (a write-to-new-folder-then-switch scheme is the fix when run times grow); the 100-household real run proves the plumbing, not scale.

**Next:** 2.7 thin web UI (sources + mapping, rule editor with dry-run, review screen, golden viewer), then encryption at rest before any real data.

## 40. Thin web screens (roadmap 2.7) and a test-suite correction

**Built (`api/ui/index.html`, `app.js`, `style.css`, `api/routes_ui.py`, `tests/test_api_ui.py`):** no build step and no third-party scripts; three static files served at `/ui`, `/ui/app.js`, `/ui/style.css`.
- Screens (shown only when the signed-in roles allow them): **Review queue** (ranked pairs, side-by-side comparison with why-same / why-different, Same person / Different people / Not sure with an idempotency key, "Show identifiers" that needs a reason and is logged), **People** (golden record + source records), **Approvals** (second-person approve/decline), **Rules** (version list, dry run, propose from JSON, approve / decline / make live / roll back, for decision, survivorship and settings), **Runs** (start, status).
- Browser safety: text is written with `textContent` only (a test bans `innerHTML`, `eval`, storage, cookies, absolute URLs); the access token lives in a variable, never in storage or the URL; the page gets its own strict CSP (scripts and styles from this origin only, no inline code, no framing, requests to this origin only). The files are public (they hold no data); every call they make is authenticated and authorised by the API like any other.
- Sign-in is a pasted token (development, or whatever the deployment's identity provider issues). A real OIDC browser login (authorization-code flow with PKCE) is a deployment piece not built here.

**Not built (be honest):** source connection and field-mapping screens (the API has no endpoints for them yet; permission `sources.manage` exists but nothing uses it); no business-unit scoping; no screen for splitting a person or overriding a field (the endpoints exist); the UI has NOT been exercised in a real browser (only `node --check` and static checks), so expect rough edges; no accessibility audit.

**Test-suite correction (important):** with the installed FastAPI (0.142) routes added through `include_router` are wrapped, and the helper in `test_api_security.py` only saw routes defined directly on the app. So the role-by-route matrix test had NOT been covering the 2.6b read routes (it covered 8 routes). Fixed: `api_routes()` in `api/guards.py` lists every route; the matrix now builds valid paths and covers 37 routes x 6 roles (all passed, so no authorization defect was hiding). Lesson: when a test says "covers every route", assert how many it covered.

## 41. Encryption at rest (gate 1 before real data)

**Research (DuckDB docs, verified on our DuckDB 1.5.6):** DuckDB implements Parquet Modular Encryption (AES-GCM-256, footer and columns, key named per session, per-column keys NOT supported); files start with the marker `PARE` instead of `PAR1`. DuckDB can also encrypt its own database files and, with `SET temp_file_encryption = true`, its spill files. Arrow/pyarrow files written with a KMS client store a wrapped key that DuckDB cannot use, so we write every file through DuckDB. Industry baseline is volume/disk encryption plus envelope-encrypted files with keys in a KMS; Indian rules reported by secondary sources (RBI AES-256, IRDAI encryption) are met by AES-256 at rest, still to be confirmed by the customer's compliance team.

**Built (`src/dedupe/crypto.py`, `tests/test_encryption.py`):**
- `DEDUPE_DATA_KEY` (32 bytes, base64) and `DEDUPE_DATA_KEY_ID` from the environment (the customer's secret store/KMS injects them); never written to disk, logged or shown in errors; `python -m dedupe.crypto new-key` makes one.
- With a key: every Parquet file (canonical, standardised, candidates, scores, decisions, clusters, golden records, queue, households, identity registry) is encrypted; the pipeline's DuckDB connection encrypts its spill files; the API reads encrypted outputs. Without a key nothing changes (development). All ~70 call sites go through `crypto.rp()`, `crypto.fmt()`, `crypto.connect()`, `crypto.write_table()`, `crypto.open_writer()` and `crypto.iter_batches()`.
- The work folder records WHICH key protects it (`.encryption.json`: key id and a one-way fingerprint, never the key). A missing key, a different key, or plain files next to a key are refused with a plain explanation, by the pipeline and at API start-up.
- `python -m dedupe.crypto status | convert | rekey`: encrypt existing plain files, or move everything to a new key (resumable, safe to repeat). Key rotation = rekey; there is no per-file key id, so a rotation rewrites the files.
- Production (`DEDUPE_ENV=production`): API settings and the pipeline refuse to start without a key.

**Tests (8):** a full run writes only `PARE` files and none of 25 sampled real names/PANs/mobiles/emails (nor the key) appears in any byte of the work or model folders; results are identical with and without encryption; wrong/missing/mixed key refusals; convert then rekey then the old key is refused; API reads encrypted outputs and will not start without the key; production refusal; key validation and hiding; a spill control proving that without a key DuckDB leaves readable data in its spill files and with a key it does not.

**Honest limits (do not claim "fully encrypted"):**
- NOT covered: the customer's own input files; the database holding the judgment, config and security logs (judgment evidence, override values such as a corrected PAN, and free-text reasons live there: use an encrypted database volume / managed encryption, field-level encryption of override values is not built); the job logs under `<work>/jobs/`; OS swap and crash dumps; backups of any of these; the JSON summaries and model file (they hold counts and weights, no personal data, but are not encrypted).
- One key per deployment, no per-column or per-business-unit keys (DuckDB limitation); no crypto-shredding for erasure yet (a design for DPDP erasure still missing).
- The key lives in process memory and the environment of the API and the run child; protect the host. A KMS client (fetch the key at start-up, rotate centrally) is a small addition behind `crypto.from_env`, not built.
- Encrypted spilling is much slower than plain spilling on this machine (software AES; DuckDB's faster OpenSSL path needs the httpfs extension): budget extra time for runs that exceed memory. Throughput of the encrypted pipeline at scale is NOT measured; only the 60-household run was timed.
- Whole-disk/volume encryption is still required underneath (BitLocker, LUKS, cloud volume keys): this layer protects copied files and backups, not a running, unlocked host.

**Next:** sources + mapping screen and endpoints (`sources.manage`), erasure/retention design, KMS client, then the remaining gates (compliance opinion on Aadhaar, independent penetration test, legal review).

## 42. Sources and field mapping from the screens (completes roadmap 2.7)

**Built (`mapping/check.py`, `api/routes_sources.py`, Sources screen in `api/ui/app.js`, `tests/test_api_sources.py`):**
- `GET /sources/options`, `POST /sources/inspect` (open a source, list columns, suggest a mapping from names and values, show 5 sample rows with every value shortened), `POST /sources/preview` (apply a mapping to a sample: how many rows fill each canonical field, masked canonical rows), `POST /sources/propose` (checks EVERY source against the real source, then proposes the next `sources` version). All need `sources.manage` (platform admin).
- `sources` is a fourth versioned configuration beside decision / survivorship / settings: same log, same propose -> approve (a DIFFERENT person with `config.approve`) -> activate, rollback, plain-language diff ("source 'crm': field city: ..."). The generic rules endpoint refuses to propose `sources` so nobody can skip the source checks.
- A run uses the ACTIVE `sources` version when there is one (its exact content is saved as `<work>/config_used/sources.json` and its version and hash appear in the run summary), otherwise the sources file as before. `POST /runs` works without `DEDUPE_PIPELINE_SOURCES` once a version is live.
- Source settings are untrusted input: per-connector allow-list (no literal URL, password or token can be stored: only the NAME of an environment variable, and only names starting `DEDUPE_SRC_`, so a typed name can never point at `DEDUPE_DATA_KEY` or other secrets), files stay inside `DEDUPE_DATA_ROOTS`, SQL is one SELECT, source ids are validated, errors are fixed sentences plus column/field names (never a path, URL, password or value; a driver error is reported by its type only).
- Reading a source is a sensitive act: rate limited (20/min per user), logged (`config_change` / `source_inspect`, who and which type, never a value), and values leave the server only shortened (first 2 and last character).
- Screen: list of live sources, a form per source (type, settings, id), "Open the source and suggest a mapping" gives a column picker per canonical field (suggestions marked), a record-id picker, carry-along columns, an advanced JSON box for combined columns and conditions, Preview, Propose. Approval and going live happen in the Rules screen (which now also lists `sources`).

**Honest limits:** only the connector types that exist (files, Google Sheets, SQL); no S3; the inspect call runs synchronously (a very large Excel file or slow database blocks that request up to the server timeout); the sample is the first rows only, so a column that is empty at the top of a file can look empty; the mapping picker handles plain column choices (combined columns need the JSON box); `config.read` holders (including viewers) can read source settings (file paths, environment variable NAMES, never secrets); the UI is still unverified in a real browser; a Google Sheets source is untested against live Google.

**Next:** erasure and retention design, a KMS client for the data key, S3 connector, business-unit scoping; then the gates: compliance opinion on Aadhaar, independent penetration test, legal review.

## 43. Screen redesign (workspace look)

**Request:** the screens should look like a professional data-platform workspace (the Databricks style), minimalist, easy to find options, none of the usual generated-interface decoration.

**Done (`api/ui/style.css`, `index.html`, `app.js`):**
- Frame: 48 px top bar (product name, who is signed in, sign out); left navigation grouped by task (Review: queue, people, approvals; Configure: sources, rules and settings; Operate: pipeline runs) showing only what the signed-in roles may use; content area with a page header (title, one-line description, the page's actions top right) and breadcrumbs on the comparison page.
- Components: flat tables with a grey header row, tabs with an underline for the four configurations, labelled form fields, small square-cornered status tags in plain words (no dots), banners for messages, a modal dialog for every written reason (replaces browser prompts; Escape cancels), dry-run result as a table (before, after, change) instead of raw JSON, queue filter (open / marked not sure / all), automatic refresh of the run list while a run is in progress.
- One accent colour (blue), 1 px borders, system font, 13 px text; no gradients, shadows, animation, icons, emoji, coloured side stripes, round pills or status dots. Light only (the earlier dark mode was dropped to keep one consistent look). `test_the_screens_stay_flat_and_plain` fails if any of those return.
- Checked by rendering the real CSS and JavaScript in Microsoft Edge (headless) against canned API answers: login, queue, comparison, approvals, rules, sources, runs and the reason dialog. Not yet checked against the live API in a browser, on a phone width, or with a screen reader.

**Honest limits:** this is the Databricks STYLE (layout and restraint), not a copy: no logo, no icons, no brand colours beyond a similar blue. The sources form is long (one column of fields); a two-column layout would be tidier. No keyboard-navigation or accessibility audit beyond labels, focus outlines and dialog focus.

## 44. Projects, connections, uploads and runs (the workspace)

**Request (user, with Databricks screenshots):** a workspace that looks like Databricks (dark, icon navigation, + New, search, home page with summary cards, an Add data page with connector tiles), where every run can use different sources (one Excel file, or several connectors), any past run can be opened and reviewed, and new runs can be started. Decisions taken with the user: group runs into projects; upload files from the browser; dark by default with a light switch; platform admins start runs over connections approved once by a second person.

**Engine (`src/dedupe/workspace/`):**
- `Workspace`: the root folder, the main database (projects, runs, uploads, connections, security log) and ONE database per project (development: `projects/<id>/project.db`; production: `DEDUPE_PROJECT_DATABASE_URL` with `{project}`, one server database per project, created by the operator). Each project has its own judgment log (steward decisions, four eyes), its own rules store and its own person-ID registry (`projects/<id>/identity`, kept across runs), so a one-off file in one project cannot disturb another project's customers.
- `RunJobs`: a run belongs to a project, reads the live versions of the chosen connections (snapshot saved as `runs/<run>/sources.json`), writes to its own folder, runs as a separate process (fixed argument list, project database URL in the child's environment, no login secret), one at a time on the machine; headline numbers stored when it finishes; earlier runs stay readable while a run is going; restart marks orphans interrupted; `import_results` registers an existing pipeline output as a finished run (migration; `python -m dedupe.workspace import`).
- `Uploads` + `connectors/upload.py`: browser uploads (CSV/TSV/XLSX) checked by content (not the name), Excel opened only after a zip-bomb check (ratio, total size, part count), header rules (non-empty, unique, <= 300 columns), <= 2,000,000 rows, converted to a text-column Parquet table, stored under a random id (encrypted with the data key), original bytes deleted; `upload` connector reads it by id only (never a path).
- Connections are `connection/<id>` versions in the main config store (same propose -> approve by someone else -> activate as rules; diff in plain language). The id is the `source_id` stamped on records, so decisions and person IDs stay attached across runs.
- Pipeline: `--registry-dir` and `--model-path` so a run folder and the project registry can live apart.

**API (53 routes, every one in the role matrix):** `/home`, `/projects` (+create), `/projects/{p}` (live rules), `/projects/{p}/runs` (list, start over connection ids or `from_run`), `/projects/{p}/runs/{r}` (detail with headline numbers, stages, configs, connection versions), run-scoped review (`.../queue`, `.../people/...`, decisions), project-scoped approvals, undo and rules (`/projects/{p}/config/...`, dry run against a chosen or the latest run), `/runs` (recent across projects), `/connections` (list, detail, diff, inspect, preview, propose, approve, decline, activate), `/uploads` (raw body, own size cap `DEDUPE_UPLOAD_MAX_BYTES`, default 50 MB), security log. A run is reachable only through its own project; unfinished or failed runs answer 409.

**Screens:** dark workspace (light switch, remembered), left navigation with icons (Home, Projects, Runs, Connections; Governance: Approvals, Audit log), + New menu (New project, New run, Add a connection, Upload a file), search across projects, runs and connections (Ctrl + K), home with summary cards, get-started tiles and recent runs, project pages (runs, rules and settings, approvals), run pages (overview with numbers, sources and rule versions, stages; review queue; people; comparison with decisions), new-run wizard (project, connection tiles, start), Add data tiles and a three-step connection wizard (connect or drag-and-drop upload, map columns with suggestions, preview and propose), connection pages with versions and approvals, approvals overview, audit log with integrity check. Checked by rendering the real CSS/JS in Edge against answers captured from a real demo workspace (`data/workspace`, built from synthetic data: 2 projects, 5 approved connections, 2 real runs).

**Tests:** rewritten for the new layout (reads, writes, runs incl. real Excel-only, five-connection and re-run with stable person IDs, connections, uploads incl. encrypted storage, bombs, wrong types, headers, size cap, unsafe names), role matrix over 53 routes, UI static checks (storage only for the theme, no absolute URLs except the SVG namespace name, menu permissions exist).

**Honest limits:** Postgres per-project databases are written but not executed; no per-project access control yet (any steward can review any project; business-unit scoping still open); runs cannot be cancelled; one run at a time per machine; the upload is held whole on disk before conversion (no streaming parse) and the 50 MB cap is a guess to tune; Google Sheets still untested against live Google; the UI was checked with captured data and headless rendering, not yet clicked through by a person against the live server; the old single-folder endpoints are gone (use `python -m dedupe.workspace import` to bring old outputs in).

## 45. Members-only projects, run cancel, Postgres test

**Per-project access (business-unit scoping).** New table `project_grants(project_id, subject, role)` in the main database; `Workspace.grant/revoke/members/roles_for`. Rule, enforced in one place (`api/scope.py`, called by every project- and run-scoped route after its normal permission guard): a project with NO grants stays open to anyone whose global role allows the action (existing behaviour, so the demo and old deployments are unchanged); once it has at least one grant it is members-only, except `platform_admin` and `auditor`, who always see every project. Outsiders get 403 (and a `permission_denied` row in the security log) and the project, its runs and `/home` counts disappear from their lists. Managing grants needs the new permission `project.access` (platform_admin only): `GET/POST /projects/{p}/access`, `DELETE /projects/{p}/access/{subject}/{role}`, audited as config changes. Screen: project > Access tab.
Design choice: the grant's role is recorded and shown, but membership alone decides access; the action still needs the matching GLOBAL role (a member with the viewer role cannot decide pairs). Per-project different permissions would be a larger change and are not built. Removing the last grant reopens the project (stated on the screen). Subjects are matched against the token's `sub`, so grants must use the identity provider's stable id.

**Run cancel.** `RunJobs.cancel` keeps each child's process handle, sends terminate (kill after 10 s), and the run ends as `cancelled`, distinct from `failed`; the machine is free for the next run straight away. `POST /projects/{p}/runs/{r}/cancel` needs `runs.start`; 409 if the run already finished, 404 if unknown; audited as `run_cancelled`. Screen: "Cancel run" on a running run, with a confirmation. Not stored: who cancelled (only the security log has it).

**Also fixed:** run lists could swap two runs created in the same second (timestamps had one-second resolution); run creation now uses microseconds.

**Postgres.** `tests/test_postgres_live.py` exercises the main database, per-project databases, grants, both hash-chained logs and a run through the API against a real server; it skips unless `DEDUPE_TEST_PG_MAIN` and `DEDUPE_TEST_PG_PROJECT` are set. NOT EXECUTED: Docker Desktop is not running on this machine (`docker info` fails; `wsl --shutdown` did not help) and psycopg2 is not installed here, so the Postgres path is still unproven and the test itself may need small fixes the first time it runs.

**Tests:** `tests/test_api_access_and_cancel.py` (open vs members-only, outsiders refused on every route, lists hide, platform_admin/auditor see, grant management and roles, cancel of a running run, finished and crashed runs not cancellable); role matrix now covers the new routes.

**Honest limits:** access is project-level membership only (no per-project permission sets, no groups); a cancel kills the pipeline process but a stage half-way through leaves a partial run folder (kept, marked cancelled, no results readable); the new screens (Access tab, Cancel button) were syntax-checked but not rendered or clicked through; Postgres unexecuted as above.

## 46. Local accounts: admin, users and roles the administrator defines

**Request (user):** a simple username and password sign-in; an admin user holding every permission who can create users; different permission levels and roles, with the admin deciding who gets what.

**What exists now**
- `DEDUPE_AUTH_MODE=local` (needs `DEDUPE_LOCAL_AUTH_SECRET`): `POST /auth/login` returns a signed token (lifetime = `DEDUPE_MAX_TOKEN_LIFETIME_S`, default 1 hour); the sign-in screen asks for username and password instead of a token (`GET /auth/config` tells it which). Dev tokens and OIDC are unchanged.
- First admin from the command line (needs the server's own database access, password asked for, never on the command line): `python -m dedupe.api.accounts_cli create-admin --username admin`; `reset-password` for recovery.
- Built-in `admin` role = every permission, including three new ones: `users.manage`, `roles.manage` and `projects.all` (open every project; platform_admin and auditor hold it too, replacing the earlier hard-coded role names).
- Users and custom roles live in the main database (`users`, `user_roles`, `custom_roles`). A token only proves who signed in; permissions are read from the database on EVERY request, so disabling a user, editing a role or changing roles applies at once. Each user has an `epoch` counter inside their tokens: disabling, a password reset or a password change moves it on and ends all their earlier sessions.
- Roles: the six built-ins (viewer, steward, approver, rule_admin, auditor, platform_admin) plus `admin` are fixed; the administrator creates custom roles from any subset of the 17 permissions (screen: Roles) and assigns roles to users (screen: Users). Project grants accept custom roles too.
- Safeguards: nobody can give more than they hold (role contents and role assignment); a user manager cannot manage someone with more access; you cannot change your own roles, disable yourself or reset your own password through the admin routes; the last enabled user who can manage users cannot be removed or demoted, and a role edit that would do so is refused; a role in use (by a user or a project grant) cannot be deleted; admin-created users get a temporary password and can do nothing but change it until they do.
- Passwords: salted scrypt (N=2^17, r=8, p=1, parameters stored per hash), 12-128 characters, not containing the username, not in a short list of very common passwords; five wrong passwords lock an account for 15 minutes (a reset unlocks); unknown user, wrong password, disabled and locked give one identical answer and cost the same time; sign-in is also limited to 10 attempts a minute per address; successful sign-ins (`login`) and failures (`auth_failed`, with the reason, never the password) go to the hash-chained security log; user and role changes are logged as `config_change`. Production refuses local mode unless `DEDUPE_ALLOW_LOCAL_AUTH=yes`.

**Honest limits**
- This is weaker than single sign-on: no multi-factor authentication, no email password reset (an administrator resets it), no password expiry or history, no check against breached-password lists (only a tiny built-in list), and an attacker who knows a username can lock that account for 15 minutes by guessing (limited by the per-address cap). Banks and insurers should use their identity provider (OIDC) in production; the flag exists so that choice is explicit.
- Segregation of duties: the `admin` role holds every permission, including `pii.unmask` and decision rights. The four-eyes checks still compare identities (an admin cannot approve their own decision), but an admin who also steward-decides is a concentration of power; use admin only for administration and give people narrower roles. This is how the user asked for it; it is a policy choice to review with the customer.
- The temporary password is chosen and passed on by the administrator (shown once on screen, never stored in clear). No invitation emails.
- No per-permission "layers" inside a project: a project grant records membership; what a person can do still comes from their roles everywhere.
- Passwords are checked in the main database used by the API: with SQLite on one machine that is fine; production uses the server database.
- The new screens (sign-in, password, Users, Roles and their dialogs) were rendered in Edge from real API answers; the sign-in round trip itself is covered by tests, not clicked through by a person against the live server.

**Tests:** `tests/test_api_local_auth.py` (13): hashing and policy, settings, sign-in config, admin holds all permissions, identical failures and lockout, per-address limit, forced password change and old-session death, disable/role-edit take effect on the next request, role validation and in-use deletion, delegation limits, last-admin and self protection, custom roles in project access and `projects.all`, command-line bootstrap. The role matrix now also covers the new routes.

## 47. Project-owned work (step 1 of 6): private projects, connections inside the project, activity, schema upgrades

**Why (user, after the industry research of this session):** each project is its own piece of dedupe work (a client, or a task) with its own set of connections (one Excel file, or Excel + database + Google Sheets + RDS, ...); a person sees everything about THEIR projects; sharing a project with others comes later; several people use one deployment at the same time, each in their own isolated space; copying a connection between projects is enough; results export (Excel, database table new/existing) and scheduled runs are wanted NOW. The research (Tamr, AWS Entity Resolution, Airbyte, Dataiku, Databricks, Alteryx) showed that dedup tools keep the source and its mapping inside the project and share at most the credentials.

**What changed**
- **Private by default.** `POST /projects` (new permission `projects.create`) makes the creator the project's `owner` and the project members-only from the first second. Owners add members and change settings without any admin (`require_or_owner("project.access")`); the owner cannot be removed. Lists (`/projects`, `/runs`, `/home`) show only projects you may open. Administration (`projects.all`: admin, platform_admin, auditor) still sees every project. New built-in role `analyst`: everything for work inside one's own projects, nothing for users, roles or logs.
- **Connections and uploads belong to a project.** Routes moved under `/projects/{p}/connections...` and `/projects/{p}/uploads`; they live in the project's own rules store (`connection/<id>`), so another project (or client) never sees them, the same id can exist in two projects, and a run reads only its own project's live connections. An `upload` connection may only use a file uploaded to the same project (checked at inspect, preview and propose). `POST /projects/{p}/connections/copy` copies a connection you can read into this project as a new proposal (uploaded-file connections cannot be copied). `/connectors` lists the connector types (the same for everyone). The old global `/connections` routes are gone.
- **Second approval is a project setting.** `projects.second_approval`: off for projects made in the portal (a one-person project would otherwise be unable to approve its own connection or rule changes), on for projects created through the engine (tests) and for projects that existed before. When off: connections and rules can be made live by their proposer, and the sensitive-decision approvals (ID conflict, split, PAN/DOB overrides) are not required. The owner can turn it on once at least two people are in the project (`PATCH /projects/{p}/settings`). THIS IS A GOVERNANCE DECISION to confirm for banking/insurance customers: the safe default for regulated use is on.
- **Activity per project.** The security log gained `project_id` (set automatically for every request under `/projects/{p}/`, and part of the hash when present, so older rows keep verifying); `GET /projects/{p}/activity` returns the project's events newest first; screen: Activity tab with plain wording.
- **Schema upgrades.** `dedupe/schema.py` `create_and_upgrade` adds missing columns to existing tables (additive only; a new NOT NULL column must have a server default or the first test run fails). Used by the workspace, runs, uploads, accounts and the security log. Not Alembic (tables belong to several modules); adopt Alembic if a rename or retype is ever needed.
- **Screens.** Navigation is now Home, Projects, Runs, Approvals, Audit log, Users, Roles. A project opens on Overview (a "where you are" checklist: add data, make it ready, run, review) with tabs Connections (add-data tiles, copy from another project, the list), Runs, Rules, Approvals, Activity, Access and Settings (for owners). The connection wizard, connection page and new-run flow work inside the project. Rendered in Edge from real answers of a rebuilt demo workspace (2 private projects, each with its own connections and a real run).

**Honest limits**
- Existing demo data from earlier sessions was discarded and rebuilt (its connections were global). The migration path for a real old workspace (moving global connections into projects) is NOT written; there is none to migrate yet.
- Projects created before this step stay open to anyone with the right role until someone adds a member (the old rule), which is why the demo workspace was rebuilt.
- Uploaded files are shared storage on disk (a random id, encrypted when a key is set); ownership is enforced in the application, not by separate folders.
- The Postgres paths are still not executed (no server here); copying/ownership/activity use the same SQL as on SQLite but are unproven on Postgres.

**Tests:** `tests/test_projects_private.py` (9: private by default and owner-managed access, project-creation permission, isolated connections, uploads tied to a project, copy rules, second-approval setting, activity feed, security-log upgrade and tamper evidence, schema helper); existing connection, run, security-matrix, UI tests moved to the project routes (the role matrix covers the new routes automatically).

## 48. Run queue, run log and connection health (step 2 of 6)

**Run queue.** Starting a run no longer fails when another is going. `RunJobs` keeps runs in order and starts them by itself: `DEDUPE_MAX_CONCURRENT_RUNS` run at once (default 1: a big run needs the machine's memory, 4.6 GB at 694k records), `DEDUPE_MAX_QUEUED_RUNS` may wait (default 20; beyond that a start is refused with a plain sentence). A queued run shows its place (`queue_position`, 1 = next), can be cancelled (it simply never starts) and stays queued across an API restart, where a run that was going is marked interrupted. Claiming a queued run is an atomic database update, so a second API process could not start it twice.
**Run log.** `GET /projects/{p}/runs/{r}/log` returns the end of the run's log with anything that looks like PAN, Aadhaar, phone or email removed (the same scrubber as the security log), for people who can open the project and read runs; shown on the run page (Log tab, and under a failed run). It is the first thing to read when a run fails.
**Rows per connection.** The run summary gained `records_by_source`; the run's Overview lists the rows each connection delivered.
**Connection health.** New table `connection_checks` (main database). `POST /projects/{p}/connections/{c}/test` opens the source with its live settings (newest when none is live), reads a sample, applies the mapping and records ok/failed, rows sampled and DRIFT (columns added or removed since the last good check). Every finished run also records a health entry per connection it used (rows read, or failed). Errors are fixed sentences: they may name a missing column, never a value, path or driver text. Screens: Health panel with Test now on the connection page, Last check column in the list.

**Honest limits**
- Only the order of queued runs is guaranteed, not fairness between projects or users (one person can queue 20 runs). A per-project cap is easy to add if it matters.
- Scheduling and exports come in the next steps. "Interrupted" runs are not retried automatically.
- A test reads a sample of up to 200 rows, so it proves the source opens, reads and maps; it does not prove every row is good (the pipeline's own data-quality report does that).
- Nothing here ran against PostgreSQL or a real remote source.

**Tests:** `tests/test_runs_queue_health.py` (7: order, cancel of a queued run, automatic start, concurrency and queue limit, restart behaviour, scrubbed log and access, rows and health from runs, test-now results and failure wording, drift added/removed); the one-at-a-time expectation of the older run test was updated; the allowed summary keys include `records_by_source`.

## 49. Results export (step 3 of 6)

**What a person can do (Results tab of a project).** Pick a finished run, then for each dataset: download it as Excel, CSV or Parquet, or write it into a database. Datasets (`dedupe/results/export.py`): `golden_records` (one row per person, best value of every field, `person_id` first, lists as "a; b"), `person_records` (source record -> person id, to carry the id back into your systems; the internal row number is not offered), `households` (person -> household).
**Database writes.** Modes: create a NEW table (refused if it exists), ADD rows to an existing table (every exported column must exist in it), REPLACE an existing table's rows (needs an explicit confirmation). Everything is ONE transaction: a refused row, a missing column or a lost connection leaves the table exactly as it was (tested: a failed replace keeps the old rows). Table and schema names are plain identifiers (letters, digits, `_`); the database's own error text is never repeated (it can quote a value or the URL).
**Destinations.** A project saves where it may write: the NAME of an environment variable that must start with `DEDUPE_DST_` and holds the database URL (never a typed URL or password) plus an optional default schema. A destination is versioned and approved exactly like a connection (a second person when the project asks for one), is tried on the spot (an unreachable one is refused) and can be tested again. A project can only write to its own live destinations, so it cannot reach another client's database. Stored in the project's rules store as `destination/<id>`.
**Safeguards for personal data leaving the platform.** New permission `results.export` (admin, analyst; never viewer, steward, approver, rule_admin or auditor by default); a reason of at least 10 characters; a per-person limit (`DEDUPE_EXPORT_LIMIT_PER_MIN`, default 6 a minute); an `export` security-log event with dataset, format or table, mode and ROW COUNT (never a value) and the reason; downloads are made as a temporary file in `<work>/exports`, sent with `Cache-Control: no-store` and deleted right after; Excel cells are stored as text and CSV values that start with `=`, `+`, `-`, `@` get a leading apostrophe (formula injection), switchable off for machine use of CSV.
**Limits.** Excel holds at most 1,048,575 rows (a clear refusal, use CSV, Parquet or a database); the temporary download file is plain text on disk for the moment of the download; only SQLite was exercised here for database writes (the code uses SQLAlchemy and plain types, but PostgreSQL, MySQL and SQL Server are not tested); no "update changed rows only" mode (replace or append only); the three datasets are fixed; results are exported unmasked because the customer's purpose is to load them elsewhere, so who holds `results.export` is a decision for the customer.

**Tests:** `tests/test_results_export.py` (11): dataset list, each format returns the same rows, permission/reason/run/membership refusals, audit without values and the rate limit, formula safety (CSV and Excel), destination lifecycle and refusals (unset variable, wrong prefix, extra fields, unreachable without leaking), create/append/replace, all-or-nothing on failure, engine refusals. The role matrix covers the new routes.

## 50. Scheduled runs (step 4 of 6)

**What it does.** A project's Schedules tab lists and creates schedules: a name, the connections to read, how often (every hour at minute :MM, every day at HH:MM, every week on a weekday at HH:MM) and a time zone (any IANA name, default UTC; the screen offers the browser's zone). When one is due the scheduler starts an ORDINARY run (so it queues behind other runs, is logged, and can be reviewed and exported like any other) over the connections' live versions at that moment, as actor `schedule:<id>`, with the reason "scheduled: <name>". Managing schedules needs `runs.start` (reading them `runs.read`), project membership, and every change is in the project's activity feed.
**Guarantees (all tested).** Claimed exactly once: a due schedule is claimed with one atomic update that moves its next time on (a second process or a slow tick gets "claimed elsewhere"), and the run's idempotency key `<schedule>:<due time>` is a second guard. After downtime a missed schedule runs ONCE, never once per missed slot. A schedule never starts a new run while its previous run is still queued or running (recorded as "skipped"). Three runs in a row that fail (or "could not start", e.g. a connection that is no longer live) pause the schedule with the reason shown; resuming resets the count. With local sign-in, a schedule whose creator was disabled or removed pauses itself instead of running as them. Times are stored in UTC and computed in the schedule's zone, daylight-saving aware (09:00 New York is 13:00 UTC in summer and 14:00 in winter).
**Settings.** `DEDUPE_SCHEDULER` (on by default; turn it off on a second API process), `DEDUPE_SCHEDULER_INTERVAL_S` (default 30, 5-3600). The scheduler is one daemon thread started with the API and stopped with it; an error in one tick is logged and the loop continues. The `tzdata` package is declared for Windows in the `api` extra (the venv has it).

**Honest limits**
- The scheduler lives inside the API process: while the API is down nothing runs (a missed schedule then runs once when it returns). A separate scheduler service would be needed for guaranteed uptime-independent runs.
- Only hourly, daily and weekly; no "every 15 minutes", monthly, or calendar-aware schedules, and no holidays.
- A schedule uses the live connections at fire time: a connection changed overnight (approved) is picked up, which is usually wanted but means the same schedule can read different mappings on different days (the run records the versions it used).
- A scheduled run that fails sends no notification (no email or chat integration exists); the schedule shows its last status and pauses itself after three failures.
- Only exercised with the injected clock and fast stand-in runs here, and a real scheduler thread for its start/stop and error handling; not left running for days.

**Tests:** `tests/test_schedules.py` (9): next-run arithmetic (zones, strictly after, weekly, hourly, daylight saving), validation, one run exactly once and claiming, one catch-up run, no pile-up, three failures pause and resume, not-live connection and gone creator, permissions and access, the background loop surviving an error and stopping. The role matrix covers the new routes.

### 2026-10-07 - Company branding
The portal shows the Aurionpro logo (sign-in page: logo with tagline; top bar: name only) instead of the "Dedupe" text. Four transparent PNGs in `src/dedupe/api/ui/` (`logo-*`, `wordmark-*`; light/dark theme variants, cut from the supplied artwork), served from a whitelist route in `routes_ui.py` (CSP unchanged: images from this origin). Page title is "Aurionpro". The accent colour is still blue; the logo's teal could replace it if wanted.

## 51. Scoring when the data has no identifiers (found by the user's own file)
**Problem found:** a user's Excel file with no PAN/Aadhaar/document numbers scored 48.6% for an obvious duplicate (same mobile, email, address, surname). The scoring learned its weights only from pairs that identifiers prove, found none, and left every weight at 0 without saying so. 318 of 355 pairs went to review, none auto-merged.
**Fix (`scoring/unsupervised.py`, `scoring/train.py`):** three modes, chosen per run and recorded in the model, the run summary and the portal:
- `id_anchored`: at least 20 same-person and 20 different-people pairs proven by identifiers (the old route, unchanged).
- `unsupervised_em`: otherwise, if there are 150+ candidate pairs and 15+ plausible matches, the Fellegi-Sunter model is fitted by EM: u (chance agreement) measured on random pairs and fixed, m (agreement for the same person) learned from a rough first guess (names sound alike plus other agreeing fields). Weights are tempered (x0.8) and capped (9 bits) because the independence assumption makes EM over-confident. Identifier and age-based levels keep their fixed weights.
- `insufficient`: too small to learn from; weights stay 0, every pair is near 50%, the portal says so in red.
The run overview, review queue and compare page show a notice for the last two modes.
**Measured (SYNTHETIC, IDs removed from the sources, kyc_documents dropped):** 300 households: precision 0.991, 14 false of 1,224 auto-merged pairs; 100 households at double noise: precision 1.000, 4 false of 423; 40 households: precision 1.000, 0 false of 153. Recall is only 0.43-0.50: of the true pairs that reached scoring almost all were found (reject zone held 25 of ~1,250), so the loss is mostly pairs that blocking never proposed (without IDs there is little to link records whose name or phone changed). **CORRECTED 2026-10-07 (section 53 step 4): that recall was a measurement error.** The test dropped the kyc_documents source from the input but the ground truth still held its ~500 records, so every one counted as a missed match. Measured on the records actually read: recall 0.88-0.89, precision 0.986-0.997 (seeds 101-103, 300 households, IDs removed).
**Honest limits:** independence assumption; needs enough overlap between records; only tested on synthetic data; a file that is almost all duplicates makes EM see nearly no non-matches (the user's file: 40 people x 5 copies, all 355 pairs auto-merged, checked by hand as plausible). Odd inputs (empty file, one record, empty names, names only, 3 rows) complete without crashing (`tests/test_scoring_modes.py`).

## 52. Portal audit and fixes: flows, accessibility, a dedicated review section
**How:** three read-only audits ran in parallel (process flow and API-vs-screen completeness; accessibility and design consistency; the human-in-the-loop journey with a look at Informatica MDM, Reltio, Tamr and Senzing). About 70 findings, 11 blockers. Then fixed, and clicked through the LIVE portal in Edge with a Playwright script (every screen, both themes, phone width, keyboard only).
**Blockers fixed:**
- One-person projects could never make a rules change live (the button ignored the project's second-approval setting).
- No addresses: Back left the app, reload lost the screen, nothing could be linked. Now every screen has an address (#/projects/x/runs/R.../compare/A/B); Back/Forward work; after a reload and sign-in the same screen opens. The address is read in exactly one place and every part is checked against a fixed pattern (test updated to pin this).
- The audit log showed the OLDEST 200 events. Now newest first with "Show older events".
- Approvers approved connection/rule versions they could not see. Now the waiting version, its differences from the live one, and a "View" of any rule version are shown.
- Double clicks could start two runs or write rows twice: every button is disabled while its action runs; one idempotency key per form.
- Keyboard users could not open table rows (projects, runs, connections, review pairs): the first cell is now a real button.
- Dark-theme primary buttons failed contrast; input edges failed in both themes. The accent is now the brand teal with AA contrast in both themes.
- Merging people with conflicting identifiers was impossible in the portal: now a confirmation box plus a reason.
**New (backend + screens):**
- Review section in the menu: **Review inbox** (every project's newest run: pairs open, not sure, waiting for a second person, decisions waiting for the next run; Start reviewing), **Decisions** (every decision with its status: in force / waits for the next run / waits for a second person / declined / undone; Undo). Routes `GET /review/inbox`, `GET /projects/{p}/decisions`.
- Compare page: decide and the next pair opens; keys S/D/U/N/P; "Pair 3 of 267"; field-by-field comparison (same / differs / only one side; masked values flagged as "looks the same"); source records of both people; earlier decisions on the records; Undo in the message; warning when the run is not the newest.
- Person page: split ticked records into a new person; correct a golden value (override). Both existed in the API with no screen.
- Review queue: paging, score band filter, tick several pairs and decide them together (bulk).
- Approvals page: review decisions with score, main evidence and a link to the comparison; connection, rule and destination changes; your own items say "someone else approves it" instead of a button that fails. Stewards now see their own waiting decisions (`/approvals` needs queue.read; others' only for approvers).
- Delete: runs (results erased, row kept as 'deleted', decisions stay), whole projects (type the id + reason; owner or admin once it has members; refused on a shared database server), unused uploaded files (files left by an abandoned wizard). New permission `data.delete` (admin, analyst, platform_admin). Every delete is a `data_deleted` audit event with the reason.
- Rename a project; edit a schedule (was delete-and-recreate); rules editor starts from the built-in defaults (`GET .../config/{name}/default`); results tab on the run page includes database destinations; run overview shows how many steward decisions the run applied and how many it could not keep.
- Accessibility: labels tied to inputs, errors announced, dialogs trap focus (page behind is inert) and return focus, Escape closes, tabs with arrow keys, page title per screen, focus moves to the new heading, skip link, search as a combobox with arrow keys, messages fixed in the corner (no longer wiped by background refreshes), "Loading..." when the server is slow, tables scroll inside their box on phones, browser-tab icon.
**Measured:** 9 new API tests; the live click-through ends with 0 console errors, 0 error messages, no sideways scrolling at 375px.
**Not done (honest list):** assigning/claiming pairs, ageing/SLA and reviewer statistics; retiring a connection or destination (versions can be declined, not removed); search does not find connections or people; threaded comments on pairs; a light-theme user sees a dark flash before the script loads; the Approvals page makes several requests per project (slow with many projects); bulk "same person" merges without opening each pair (a 10-character reason is required).

## 53. Robust golden records and matching across any sources (phase asked for 2026-10-07: "fix all and thoroughly")
**Why:** the user asked whether the engine really uses every source to build the master record, and wants an industry-ready tool that
handles any data. Audit found: a run DOES combine all chosen connections and matches within and across them, but the golden-record choice
was tuned to the synthetic source names (trust list and date columns named kyc_documents, bank_crm...), so for portal projects every source
tied on trust and no record had a date: "most recent" silently fell back to alphabetical source order.
**Research (2026-10-07):** MDM tools (Informatica, Reltio, Profisee) choose per attribute with source priority, recency, completeness, frequency;
Informatica lowers trust for poor-quality values and lets trust decay with age; Reltio defaults to recency, and recency needs reliable record
timestamps. Splink recommends 3-10 blocking rules, phonetic keys, so that a true match failing every rule is implausible; Splink's
`find_matches_to_new_records` matches only new records against existing clusters (incremental).
Sources: community.reltio.com/reltio-best-practices/mdm-survivorship, onlinehelp.informatica.com (B360 survivorship rules),
profisee.com/blog/mdm-survivorship, moj-analytical-services.github.io/splink (blocking, real-time linkage).
**Plan (one commit each):** 1 golden-record choice; 2 golden-record rules screen; 3 customer columns in the golden record; 4 blocking recall
without identifiers; 5 source reliability in scoring; 6 incremental runs; 7 odd data shapes.

**Step 1 (done): golden-record choice that works for any project.**
- Each record's own date: standardisation now outputs `record_date` (ISO; future dates and dates before 1900 are dropped as typos) from the
  mapped "record date" field; `source_dates` still overrides per source. "Most recent" therefore works with no source named in the rules.
- Trust order: configured `source_trust` first; every other source is ranked AUTOMATICALLY by how many golden fields its records fill on
  average (stated in the run summary as `trust_order` / `auto_ranked_sources`; completeness is not the same as trustworthiness, so the
  rules screen in step 2 lets people set it). New `field_trust`: a source order for one field or group (e.g. phones from the CRM, IDs from KYC).
- Quality: a per-field quality score (full first name over an initial, a middle name adds, a PAN as written over a repaired one, an address
  with house number and PIN over a bare line) is the new `best_quality` strategy (now the default for names) and the tie-breaker in the others.
- Shared values: a phone or email held by more than `shared_value_limit` (3) different people (office line, agent, placeholder) only becomes a
  golden value when the person has nothing else. Counted in the summary (`shared_values_passed_over`).
- `sources_without_dates` in the summary says which sources cannot take part in "most recent".
- `configs/survivorship_default.json` no longer names the synthetic sources.
- Synthetic 150-household run: auto order bank_crm, loans, insurance, kyc_documents, leads; kyc_documents undated. Tests: 6 new in
  `tests/test_survivorship.py`; 355 passed. Honest limit: no trust decay with age yet; quality rules are hand-written, not learned.

**Step 2 (done): golden-record rules as a form** (Project > Rules > Golden record). Three parts: (1) which source to trust most: "rank
automatically" or an explicit order with Up/Down buttons (keyboard focus stays on the moved source; the list starts from the order the
latest run actually used); (2) where each source's record date comes from: the mapped record date or one of the connection's extra columns,
with a warning listing the sources the latest run could not date; (3) per field or group: the rule (newest record, most trusted source, most
records agree, most complete, best quality, each explained on the page) and an optional preferred source. Plus the shared-phone limit. It
proposes a normal versioned change (approval rules unchanged); the JSON editor is still there under "Advanced". New read endpoint
`GET /projects/{p}/golden-record-guide` (fields, strategies, the project's live sources with their extra columns, how the latest run ranked
them; no personal data). The run overview gains "How the golden records were chosen" (trust order, which sources were ranked automatically,
undated sources, conflicts, shared values passed over). Checked live in Edge, both themes, 0 console errors; one test proposal (version 1 of
the demo project's golden-record rules, "phones from one source...") was left waiting for approval in `customer-master`.

**Step 3 (done): the sources' own columns reach the golden record.** A connection's extra columns ("attributes", ticked in the field
mapping) used to stop at the staging file. Now each becomes a golden-record column `extra_<name>` (lower case, other characters as _;
same name in different sources = one field; 'Branch Code' and 'branch_code' kept apart with a _2 suffix), chosen like any field: rule
`extra_default_strategy` (newest record) or per column in `extra_fields`; `carry_extra_columns: false` turns it off. Names with quotes or
symbols, or beyond 60 columns, are skipped and listed (`extra_columns_skipped`). The person page shows them under "Other columns from the
sources", MASKED like identifiers unless revealed (their content is unknown); exports carry them (they are ordinary golden_records columns).
The rules form has a section "Your own columns" (carry on/off, rule per column, which columns cannot be carried and why).
Not done: a steward cannot correct an extra column's golden value (field overrides cover the standard fields only).

**Step 4 (done, with a correction): matching without identifiers.** Measuring blocking first showed blocking was NOT the problem: without
IDs it proposes 88.8% of true pairs (91.2% reachable through other records). The 0.43-0.50 recall of section 51 came from the evaluation:
records of the source left out of the run were still in the ground truth and counted as misses. Fix: the pipeline's evaluation scores only
records that were read (`truth_records_not_in_input` says how many were left out). Corrected, no IDs, 300 households: seed 101 precision 0.997
recall 0.894; 102: 0.986 / 0.886; 103: 0.987 / 0.881. With IDs (seed 101): 0.998 / 0.857 (more pairs go to the review queue, which this
figure counts as not merged).
Tried and NOT adopted: a name-only blocking rule for records without a birth date (`name_undated`, kept as an optional rule). It finds more
true pairs (no IDs: 88.8% -> 93.1%; with IDs 81.9% -> 88.7%) but those pairs share nothing except the name, so scoring rejects them (recall
unchanged without IDs) and with IDs the extra pairs lowered recall 0.857 -> 0.763. The remaining misses are record pairs with no shared
evidence besides a (sometimes misspelt) name: one record has only a phone, the other only a date of birth. They cannot be merged safely
without more data.

**Step 5 (done, re-scoped after measuring): source reliability.** Planned: weigh evidence by source. Measured first (seed 101): wrong merges
do not concentrate in a source (6 with IDs, 5 without, spread over source pairs); misses concentrate on pairs with the leads source (13-31%
missed) because leads records are sparse (age instead of birth date), not because the source is wrong. Weighting by source would not change
that, so it was not built. Built instead: a per-source data-quality profile in every run (standardisation counts, per source, how many
records carry a usable name, birth date, birth year or age, phone, email, PIN, an ID, a record date, and how many supplied values failed
validation; counts only, no values), shown on the run page as "Source data quality". It tells people which source to trust and why a
source finds few duplicates. Tests: 3 new API tests (guide + form-style proposal, masked extra columns, source profile), 2 new golden tests.

**Step 6 (done): incremental runs** (`src/dedupe/incremental.py`). Every run now stores a fingerprint per record as read
(`record_hashes.parquet`, taken before Aadhaar is erased) and the fingerprints of its cleaning and comparison logic. A portal run hands the
project's latest successful run to the pipeline (`--previous-run`, decided when the run starts, not when it was queued) unless "Full re-run"
is ticked on the New run page. The pipeline then: copies the standardised rows of unchanged records and standardises only new or changed
ones (the data-quality report is now computed from the standardised file, so it still covers every record: standardisation keeps per row
which fields were supplied and which failed validation, names only); re-blocks ALL records; reuses the previous model and the previous
scores of pairs whose two records are both unchanged, scoring only pairs that touch a new or changed record; clusters, golden records and
households over all records as usual. The model is learned again when more than 30% of records are new, changed or removed, or when the
earlier run could not score meaningfully. Reuse is refused (full run, with the reason on the run page) when the previous run lacks a file
(runs made before this change), was made by different cleaning/comparison logic, or used different run settings. The run page shows
"Work reused from the previous run" (records unchanged/new/changed/removed, pairs reused/scored, model reused or why it was learned again).
**Measured (SYNTHETIC, 1,500 households, 10,434 records, 20 changed + 10 removed):** full re-run 15.9 s vs incremental 7.7 s (scoring
6.7 s -> 0.25 s, standardise 1.7 s -> 0.24 s); precision/recall 0.9968/0.8549 full vs 0.9974/0.8553 incremental.
Also fixed on the way: the golden-record stage had become slow after step 1 (each field's query carried ~110 columns through window
functions): 25 s -> 3.2 s on 2,100 records by selecting only the columns each field needs; it is now the largest stage of an incremental
run (about 4 s at 10k records).
**Honest limits:** sources are still read in full every time (needed to see what changed; a database source could later read only rows
changed since a timestamp); reused scores keep the term frequencies of the run that made them, so a common value becoming more common is
only noticed by a full re-run; "same people as a full run" is not guaranteed because a full run re-learns the model (tests check precision
and recall within 0.03 of a full run on synthetic data). Tests: `tests/test_incremental.py` (4).

**Step 7 (done): data in awkward shapes** (`src/dedupe/standardise/oddities.py`, used by standardisation). Tested the old cleaning on
common real-world shapes first and found: two phones in one cell were LOST; a phone stored as `9876543210.0` (a number column) was lost;
a phone in scientific notation was turned into a WRONG number (9.87654321E9 -> 9876543219); `17-May-90`, `1990-05-17 00:00:00`,
`May 17, 1990` and Excel serial dates were not read; emails with a stray space (`name@example. com`, 39 of 200 rows in the user's Trial
file) were lost; placeholder names ("Unknown", "Test", "NA"), company rows, joint names ("Rahul & Anita Sharma") and "Rahul S/O Ramesh"
went through as ordinary people. Now:
- phones: every valid mobile in a cell is kept (first = main, next = alternate); `.0` removed; scientific notation read exactly when it
  still holds 9+ significant digits, otherwise refused (`phone_digits_lost`: never guess digits);
- emails: first real address in a cell; spaces around `.`/`@` repaired; placeholders judged by the part before the @ only (test@, none@,
  na@...); a domain alone (example.com) is NOT a placeholder, because test files give each person their own address there;
- names: placeholder names emptied; "S/O, D/O, W/O, C/O, son of..." split into name and father's/husband's name (used when no father's name
  is given); joint names keep the first person; company names (Pvt, Ltd, LLP, Traders, Enterprises, Trust, Hospital, HUF...) are flagged
  `organisation` and, like a header line repeated inside the data, kept OUT of matching (`skip_matching`: still in the output, never paired);
- dates: a time part, two-digit years with a month name, "May 17, 1990" and Excel serial numbers are read;
- duplicate record ids within a source: later copies become `<id>#2`, `#3`... in file order (nothing lost, nothing joined twice); count reported.
Per record the flags are stored (names, never values); the run page shows "Unusual data found and how it was handled" per source.
STANDARDISE_VERSION raised to 2 (earlier runs are not reused by incremental runs).
**Checked:** synthetic benchmark unchanged (seed 101, 300 households: with IDs 0.9977/0.8575, without 0.9965/0.8941) and NO synthetic
person flagged as company or placeholder; on the user's Trial file usable emails rose from 155 to 194 of 200 and nothing was flagged.
Live portal (Edge): two runs of the demo project, the second reused the first (8,178 records unchanged, 12,430 pair scores reused,
7.4 s); new sections render. Fixed on the way: the run-finished message showed a stray "null" (and two other panels could): `fill()`.
**Honest limits:** the company and placeholder word lists are hand-written for Indian data and will need tuning on real files (a person
named after a word in the list would be flagged; nothing is deleted, the record is only kept out of matching and counted); joint names keep
only the first person; scientific-notation phones with fewer than 9 digits are dropped, not repaired. Tests: `tests/test_oddities.py` (13).

**Phase result (section 53, all on SYNTHETIC data):** golden records now choose by each record's own date, an automatic or chosen source
order, per-field trust and value quality, set from a form; customers' own columns reach the golden record; matching without identifiers is
measured correctly (recall ~0.89, precision ~0.99); every run reports per-source data quality and unusual data; repeat runs reuse unchanged
records' work (about 2x faster at 10k records, more as data grows); awkward data shapes are handled instead of silently lost.

## 54. User review round 2 (2026-10-07): connections, results preview, deletes, no-code rules
The user's review found: (1) no way to edit a connection from the project's list; (2) run results show only download buttons, no look
at the golden records; (3) the connection wizard's steps cannot be clicked; (4) the column-mapping page is hard to follow, does not show
which field comes from which source, does not ask which columns identify a person, and there is no no-code way to add business rules;
(5) deleting projects and runs is hard to find; (6) the Matching rules tab showed "[object HTMLParagraphElement],..." instead of the editor.
**Fix 1 (done): the empty rules page.** A regression from section 53 step 2 (an array handed to DOM append, which prints it as text);
the live check then only opened the Golden record tab. Same class of bug fixed in the review-queue and decisions panels (a stray "null"
when the viewer cannot decide). New helpers `fill()`/`add()` drop empty parts; both click-through scripts now fail on "[object ...]",
"undefined", "null" or "NaN" anywhere on screen, and the second one opens all three Rules tabs. Pitfall: the portal reads app.js once at
start-up, so UI changes need a portal restart before a live check.
