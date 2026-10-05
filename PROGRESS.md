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
- [ ] 1.1 Synthetic Indian customer generator with ground-truth duplicates (PAN, Aadhaar-like, phones, names, addresses; corruptions incl. initials, transliteration, swaps, missing fields, family-shared-attribute trap)
- [ ] 1.2 Evaluation harness (pairwise precision/recall/F1 + cluster metrics)
- [ ] 1.3 Connector plugin interface + schema-mapping layer (see 7a); first connectors: CSV/XLSX/Parquet, Postgres/MySQL (SQLAlchemy), Google Sheets
- [ ] 1.4 Standardisation (names, phones, PAN/Aadhaar validators, addresses/PIN)
- [ ] 1.5 Blocking
- [ ] 1.6 Scoring with Splink (unsupervised EM start)
- [ ] 1.7 Clustering (guard against chaining)
- [ ] 1.8 Baseline benchmark recorded here

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
- **2026-10-05** — Phase 0.2–0.4 done (repo, skeleton, Dockerfile, compose). 0.5 blocked by Docker engine failure on the host (engine pipe missing, `wsl` command hangs). Waiting on user to restart Docker Desktop / reboot. No commits yet. Fallback: dev proceeds in `.venv` (`.venv/Scripts/python -m pytest`).
- **2026-10-05** — User answered open questions: no real data (synthetic only); platform must ingest from any source. Added connector design (7a), updated roadmap 1.3 and decisions log.
