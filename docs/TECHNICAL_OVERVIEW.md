# Dedupe platform: technical overview

Short and factual. Results are on **synthetic data only**; nothing has run on real customer data yet.

## 1. What it does

It reads customer records from many sources, finds the records that belong to the same person, and builds one clean **golden record** per
person. Each run also produces a stable person ID for every source record, and a review queue of doubtful pairs for a human. Every
decision, rule change and data export is logged and can be undone.

There are three ways to use it, all on one engine:
- the **portal**: web screens;
- the **REST API**, which also runs **headless** (no screens);
- the **library / terminal command**: `pip install`, then `dedupe run …` or `from dedupe import sdk`.

## 2. Pipeline (one run)

| Stage | What happens | Technique |
|---|---|---|
| Ingest | Read every source into one canonical schema; extra columns kept as JSON | 26 connector types, streamed in batches through Arrow |
| Column mapping | Detect what each column holds and map it to a field (name, DOB, PAN, Aadhaar, phone, GSTIN…) | Header synonyms plus value profiling (regex, checksums, value spread) |
| Standardise | Clean names, dates, phones, emails, addresses and IDs; reject invalid values | Rule-based text normalisation (section 3) |
| Blocking | Pick candidate pairs instead of comparing all n² pairs | 18 SQL key rules (same PAN, phone, surname-sound + DOB, name + PIN…) plus the project's own rules; capped key sizes |
| Scoring | A match probability for every candidate pair | Fellegi-Sunter weights (section 4) |
| Decision | Merge, ask a person, or keep apart | JSON rules compiled to SQL, in the order never merge > ask a person > merge > not enough evidence, plus score thresholds |
| Clustering | Group merged pairs into people | Connected components, then a split wherever a group holds a contradiction (section 4) |
| Person IDs | Stable `P…` IDs across runs (on a merge, the oldest ID is kept) | ID registry kept between runs |
| Golden record | The best value of each field | Survivorship rules per field: newest, most trusted source, most frequent, most complete, best quality |
| Review queue | Doubtful pairs, ranked, for people | Priority from score and rule; human decisions applied in every later run |
| Households | Related people (family) | Shared address, phone and surname evidence |
| Incremental | Re-process only changed records | Row fingerprints; reuse of the previous run's scores and model |

## 3. Text processing ("NLP")

Rule-based and tuned for Indian data. **No large language model or neural network is used.**
- **Names:** removal of honorifics (Mr, Smt, Dr…); "S/O", "W/O" and "&" joint names split out; OCR repair; initials detected.
- **Name sounds:** a phonetic skeleton tuned for Indian spellings (Mohd / Muhammad / Md, Sharma / Sarma).
- **Name similarity:** Jaro-Winkler and token comparisons (names in any order).
- **Dates:** many formats; swapped day and month; year only; age or age band; placeholder dates (1 Jan, 1900) flagged.
- **Phones:** +91 and leading 0 handled; several numbers in one cell split; numbers that Excel rounded rejected.
- **IDs:**
  - PAN: shape check and OCR repair.
  - Aadhaar: Verhoeff checksum. It is never stored in clear: only an HMAC token and the last 4 digits.
  - GSTIN recognised by its checksum, passport, driving licence, voter ID.
- **Addresses:** normalised text, PIN code and house number.
- Placeholders ("Unknown", "test@", "NA") count as empty.
- **Not done:** transliteration between scripts (Devanagari to Latin).

## 4. Machine learning and matching

- **Probabilistic record linkage (Fellegi-Sunter).** Each field comparison has levels (exact, sounds alike, fuzzy, differs, missing).
  Each level carries a weight, log(m/u). The weights are summed and turned into a probability.
- **How the weights are learned (no hand labels needed):**
  1. *ID-anchored:* pairs whose PAN or Aadhaar agree, or conflict, are used as labels. An L2-regularised logistic regression learns the
     weights of the other fields, which avoids double-counting correlated fields.
  2. *No identifiers in the file:* unsupervised EM (expectation maximisation), as in Splink, judged against a random-pair prior.
  3. Term-frequency adjustment: a rare agreeing name counts more than a common one (clipped at 16×).
- **Rules engine:** conditions in JSON (all / any / not; field levels; a column's values; one column against another column; the source;
  the score), compiled to SQL. A no-code builder in the portal writes them.
- **Clustering:** a graph of merges, then a guard. A group holding two different valid PANs or Aadhaar numbers, a vetoed pair, or a
  person's "different" decision is split at its weakest link (max-bottleneck path). It is deterministic and every split can be traced.
- **Human in the loop:** decisions (same, different, split, corrected value) are hard constraints in every later run. Reviewer decisions
  also produce suggestions to change the rules; they are not used for automatic retraining, on purpose.
- **Ready-made settings:** careful (rules only), balanced (rules plus the score; recommended) and catch_more.

**Measured (synthetic):**
- 1,200 households: F1 0.931 with identifiers, 0.918 without.
- 692,537 records on 8 CPUs: 445 seconds, precision 0.993, recall 0.851.

## 5. Scheduling and running

- **Run queue:** runs go first in, first out, with a limit on how many run at once (`DEDUPE_MAX_CONCURRENT_RUNS`, default 1). A project
  never has two runs at once. Each run is its own subprocess and can be cancelled. Runs interrupted by a restart are marked as such.
- **Schedules:** hourly, daily or weekly in the schedule's own time zone (daylight saving handled).
  - Each due schedule is claimed with one atomic database update, so a second server never starts it twice, and the run carries an
    idempotency key.
  - Missed slots run once after downtime.
  - A schedule never piles up behind its own run.
  - It pauses itself after 3 failures in a row, when a source is no longer in use, or when its owner loses access.
- **Incremental or full** reload per run.

## 6. Tech stack

| Layer | Used |
|---|---|
| Language | Python 3.10+ (tested on 3.12) |
| Data engine | DuckDB (SQL on Parquet, spills to disk), Apache Arrow / PyArrow, Polars, NumPy |
| Storage | Parquet files per run (optionally AES-GCM encrypted); SQLite or PostgreSQL for metadata, logs and decisions (SQLAlchemy) |
| API | FastAPI + Uvicorn, Pydantic models, JWT sign-in (OIDC single sign-on, or local accounts with scrypt passwords) |
| Portal | Plain JavaScript, HTML and CSS (no framework, no build step), served by the API |
| Connectors | Files (CSV, Excel, JSON, XML, Parquet, Avro, ORC, fixed-width, folders); databases (PostgreSQL, MySQL, SQL Server, Oracle, DB2, Snowflake, BigQuery, Redshift, Databricks, SQLite, DuckDB); MongoDB; S3, GCS, Azure Blob; SFTP; Google Sheets; REST APIs, Salesforce, HubSpot, Dynamics 365 |
| Packaging | pip package with a `dedupe` command; Docker image; docker compose with PostgreSQL |
| Quality | pytest (about 600 tests), ruff, bandit, pip-audit, SBOM |

**Security:**
- roles and per-project membership, with four-eyes approval where required;
- a hash-chained audit log;
- masked identifiers, with every unmask logged;
- rate limits;
- secrets only from environment variables;
- encryption at rest.

Details are in `docs/SECURITY.md`.

## 7. Use it without a screen

### Install (any Linux machine with Python 3.10+)
```bash
git clone <repository> dedupah && cd dedupah
python3 -m venv .venv && . .venv/bin/activate
pip install .                    # the library and the `dedupe` command
pip install ".[api]"             # also the REST service
pip install ".[connectors]"      # optional: database and cloud connectors
export DEDUPE_TOKEN_KEY='<a long random secret>'   # keys the Aadhaar tokens; keep it the same across runs
```
Use a virtual environment: the public PyPI package `dedupe` (a different project) uses the same import name.

### Terminal
```bash
dedupe inspect crm.csv loans.xlsx                 # what each column holds and which field it fills (no data printed)
dedupe run crm.csv loans.xlsx -o out              # deduplicate with the automatic mapping
dedupe run crm.csv loans.xlsx -o out --format xlsx --preset balanced --outputs golden,map,review,clean,links
dedupe init crm.csv loans.xlsx -o job.json        # write a job file (mapping, preset, outputs) to edit ...
dedupe run --job job.json -o out                  # ... and run it
dedupe explain out P000000010                     # why that person's records were joined
dedupe run ... --rules my_rules.json              # your own matching rules (JSON, as the portal shows them)
```

**Outputs**, as csv, xlsx, parquet or json, each written to `out/`. A `summary.json` with the counts is always written.

| Output | Contents |
|---|---|
| `golden` | One clean record per person |
| `map` | Each source record with its person ID |
| `review` | Pairs for a person to decide |
| `clean` | Every record as cleaned for matching |
| `links` | Every merge with the rule or score that made it |

Exit code 0 means success; 2 means a problem you can fix, with the message on stderr.

### Python
```python
from dedupe import sdk
print(sdk.inspect("crm.csv")["mapping"])
res = sdk.run(["crm.csv", "loans.xlsx"], out="out", preset="balanced", fmt="parquet", outputs=("golden", "map", "review"))
res.summary["people"], res.table("golden").num_rows, res.explain("P000000010")
```

### Headless service
```bash
DEDUPE_UI=0 dedupe serve --host 0.0.0.0 --port 8000      # or: dedupe serve --no-ui
```
This is the same REST API the portal uses, with no screens. Other systems:
1. sign in (`POST /auth/login`, or OIDC tokens);
2. create a project and its sources (`POST /projects`, `/projects/{p}/connections`);
3. start runs (`POST /projects/{p}/runs`), or set schedules;
4. read results (`GET /projects/{p}/runs/{r}/datasets/golden_records/rows`, `POST .../export`).

They can also decide review pairs (`POST .../decisions/approve|reject`). With `DEDUPE_ENV=production`, sign-in, HTTPS, an allowed-host
list and an encryption key are required.

## 8. Next phase: integration with Shankar's image engine

**Not built yet.** I have not looked at Shankar's service and have no interface specification for it. The plan below assumes it can read
ID-card images (OCR) and give face embeddings; check that with him first.

1. **Extraction as a source.** A new connector, `image_engine`, sends each image (or a folder or bucket of them) to the engine's API and
   gets back fields: name, DOB, PAN, Aadhaar, address, document type, confidence. Those become ordinary records and go through the same
   cleaning, matching and golden record. Low-confidence fields arrive marked as such and count less.
2. **Face match as evidence.** The engine returns a face embedding, or an ID for one stored on its side. A new comparison, `face`, has
   levels (same face / similar / different / no photo) from cosine similarity, and carries a weight like any other field. Rules can use it,
   for example "same face but different PAN: ask a person". Blocking on approximate nearest neighbours finds the same face across sources
   (face deduplication).
3. **Review with images.** The pair screen shows both document images and the face score, fetched from the engine when needed and never
   copied into our files.
4. **Contract:** a versioned REST/JSON interface (extract, embed, compare), service-to-service authentication, timeouts and retries,
   batch calls. The run keeps the engine's version for audit.
5. **Compliance before real data:**
   - face images and embeddings are biometric data, so consent and purpose limits apply;
   - Aadhaar images must follow UIDAI masking rules: our side keeps tokens only;
   - retention and deletion must cover the engine too;
   - a security review of the link between the two services.

**Steps:**
1. agree the interface with Shankar;
2. build the connector and an extraction-quality report on synthetic card images;
3. add the face comparison and blocking;
4. add the review screen;
5. pilot on consented test data.
