# PROGRESS.md — Customer Data Deduplication Platform

> **Living document. Read this first at the start of every session, update it at the end of every step.**
> Last updated: 2026-10-05 (project kickoff)

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

## 3. Architecture (planned)

Pipeline stages (each a module):

```
connectors → standardise → block → score → cluster → survivorship (golden record) → review queue / store
                                   ↑
                         rule engine (JSON → SQL), user-defined
documents: classify → OCR/MRZ → validate → extracted fields join the same pipeline (face match = optional module)
```

Planned stack: Python 3.10+ · DuckDB/Polars (compute) · Splink (scoring) · rapidfuzz, phonenumbers, libpostal (standardisation) · ONNX Runtime + PaddleOCR (documents) · FAISS/hnswlib (face vectors, optional) · Postgres (golden records, lineage, audit) · FastAPI (API) · thin web UI later · Docker / docker-compose.

Planned repo layout (to be created step by step):

```
Dedupe/
  PROGRESS.md            this file
  CLAUDE.md              pointer for Claude sessions
  README.md              how to run on any machine
  pyproject.toml         dependencies / packaging
  Dockerfile             app image
  docker-compose.yml     app + postgres (+ later services)
  .env.example           configuration template
  src/dedupe/            python package
    config/  connectors/  standardise/  blocking/  scoring/
    clustering/  survivorship/  rules/  documents/  store/  api/
  tests/
  data/                  (gitignored) local data, synthetic datasets, parquet outputs
  scripts/               generators, benchmarks
  docs/                  design notes, decisions
```

## 4. Environment (this machine)

- OS: Windows 11 Pro; shell: PowerShell/Git Bash
- Python 3.10.11, Docker 29.4.3, git 2.46.2 available
- Folder is **not yet a git repository**

## 5. Roadmap

### Phase 0 — Foundation (portability + modularity)
- [x] 0.1 PROGRESS.md + CLAUDE.md pointer
- [x] 0.2 Git init, .gitignore, .dockerignore
- [x] 0.3 Project skeleton (`src/dedupe` package, pyproject.toml, tests dir)
- [x] 0.4 Dockerfile + docker-compose (app + Postgres), `.env.example`
- [~] 0.5 Smoke tests pass in local `.venv` (3 passed, 2026-10-05). Container run still unverified — **BLOCKED**: Docker Desktop engine not running / containers hang in "Created" (WSL likely stuck). Needs Docker Desktop restart / PC reboot, then re-run `docker compose run --rm --build app`. Not yet verified.
- [ ] 0.6 README with "run on a new machine in N commands"

### Phase 1 — Core engine
- [x] 1.1 Synthetic Indian customer generator with ground-truth duplicates — see section 9. Run in `.venv`: `python -m dedupe.synthetic --households 1200 --seed 42 --out data/synthetic`
- [x] 1.2 Evaluation harness (pairwise precision/recall/F1, false merges split same-family vs cross-family, per-hard-case breakdown) — `src/dedupe/evaluation/`; run `python -m dedupe.evaluation baseline|score --data data/synthetic [--pred file.csv]`. Prediction file format: CSV `source,source_record_id,cluster_id`.
- [x] 1.3 Connector plugin interface + schema-mapping layer (see 7a and section 11). Connectors: CSV/TSV, XLSX, Parquet, generic SQL (SQLAlchemy), Google Sheets. Run: `python -m dedupe.ingest --config configs/synthetic_sources.json`
- [x] 1.4 Standardisation (names, phones, PAN/Aadhaar validators, addresses/PIN) — see section 12. Run: `python -m dedupe.standardise`; report: `python -m dedupe.standardise.quality`
- [ ] 1.5 Candidate generation (blocking): UNION of several rules (strong-ID equality; phonetic surname + DOB; first-name + DOB; DOB + PIN prefix + initial...). **Frequency cap**: values shared by too many records (family phones, common names) are not used as keys. Measure pairs completeness (true pairs kept), reduction ratio and pairs generated, per rule.
- [ ] 1.6 Pair scoring: Fellegi-Sunter via Splink on DuckDB. Comparison levels per field (exact / phonetic / fuzzy / initials / swapped-DOB / missing). **Term-frequency adjustment** so rare agreeing values weigh more and common/shared ones less. Unsupervised EM, seeded by strong-ID pairs. Keep per-field contributions for explainability/audit.
- [ ] 1.7 Decisions + hard constraints: score -> auto-merge / review / reject zones; **never-merge vetoes** (two different valid PANs or Aadhaar tokens, conflicting gender, large DOB gap, twin guard). This is the seed of the Phase 2 rules engine.
- [ ] 1.8 Clustering: only auto-merge edges form clusters; connected components + **conflict check that splits clusters** + size alerts (no black-hole entities); review pairs stay unmerged. Add cluster-level metrics (purity, B-cubed).
- [ ] 1.9 Household/relationship layer: families are NOT merges. A separate graph of 'related' links between resolved people (shared address, phone, nominee/co-applicant, father name, surname) -> household groups. Evaluate against household truth.
- [ ] 1.10 Final Phase 1 benchmark recorded here (see section 10 for running results)

### Phase 2 — Rules and golden record
- [ ] 2.1 Rule schema (JSON): auto-merge / review / never-merge
- [ ] 2.2 Rule → SQL compiler; dry-run impact preview
- [ ] 2.3 Rule versioning + audit trail
- [ ] 2.4 Survivorship rules + golden record + lineage
- [ ] 2.5 Review queue + merge/unmerge model
- [ ] 2.6 API (FastAPI) over the above

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

## 7. Open questions

- Web UI timing (after Phase 2 API assumed).

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

- **2026-10-05** — Research completed (entity resolution tools, Splink/Zingg/dedupe, KYC/doc pipelines). Requirements gathered. Created PROGRESS.md. Next: Phase 0.2–0.6.
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
