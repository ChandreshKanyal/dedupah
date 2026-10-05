# Customer data deduplication platform

Finds records that refer to the same customer across many data sources, groups them into one person, links people
into households, and explains every decision. Built for insurance and banking data in India. CPU only.

**Status:** Phase 1 (core engine) complete and measured on synthetic data. Not yet validated on real data.
See [PROGRESS.md](PROGRESS.md) for the full design record, decisions and caveats, and [docs/BENCHMARK.md](docs/BENCHMARK.md) for results.

## Run it (development, local Python)

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"          # Windows; use .venv/bin/python on Linux/macOS
.venv/Scripts/python -m pytest -q                         # 80+ tests

# 1. make a synthetic multi-source dataset with ground truth (deterministic per seed)
.venv/Scripts/python -m dedupe.synthetic --households 1200 --seed 42 --out data/synthetic

# 2. run the whole pipeline with one command (and score it against the ground truth)
.venv/Scripts/python -m dedupe.pipeline --sources configs/synthetic_sources.json --truth data/synthetic

# 3. reproduce the published benchmark
.venv/Scripts/python -m dedupe.benchmark --out docs/BENCHMARK.md
```

Docker: `Dockerfile` and `docker-compose.yml` are provided (app + Postgres) but have **not been verified** yet
(the Docker engine on the development machine was failing). Verify with `docker compose run --rm --build app` before relying on them.

## Pipeline

```
sources (CSV, Excel, Parquet, any SQL database, Google Sheets)
  -> ingest          connector plugins + schema mapping onto one canonical customer schema      dedupe.connectors, dedupe.mapping, dedupe.ingest
  -> standardise     names, phones, PAN, Aadhaar (tokenised), dates, addresses                  dedupe.standardise
  -> block           which record pairs are worth comparing (union of rules, frequency cap)     dedupe.blocking
  -> score           per-field evidence -> match probability, explainable                       dedupe.scoring
  -> decide          auto-merge / review / reject; never-merge vetoes; business rules (JSON)    dedupe.decision
  -> cluster         one group per person, contradiction guard                                  dedupe.clustering
  -> households      families as relationships between people, not merges                       dedupe.households
```

Every stage reads and writes Parquet, so stages can be run separately (`python -m dedupe.<stage> --help`) and later sharded across machines.
Outputs land in `data/staging/`: `clusters/clusters.parquet` (record -> person), `clusters/possible_matches.parquet` (steward queue),
`households/*.parquet`, `decisions.parquet` (with the rule or score that decided each pair), `scored_pairs.parquet`
(per-field contributions in bits), `data/models/fs_model.json` (readable model weights).

## Privacy and security (read before using real data)

- Raw Aadhaar numbers are erased from the canonical file right after standardisation; downstream only a keyed HMAC token and the last four digits exist.
  Set `DEDUPE_TOKEN_KEY` (a secret, kept outside the repo) and `DEDUPE_ENV=production` to refuse the insecure development key.
- Database credentials are read from environment variables (`url_env`), never from config files. Use read-only database users.
- Staging files contain other personal data (names, PAN, phone, email, addresses) **unencrypted**. Encryption at rest, access control and
  audit logging are planned (Phase 4). Do not run on real data on an unprotected machine.
- Legal points (DPDP Act 2023, RBI KYC, IRDAI, UIDAI rules on Aadhaar) have not been reviewed by counsel.

## Tuning without code

Business behaviour is data: `configs/decision_default.json` (zones, never-merge / auto-merge / force-review rules in a safe condition language;
`python -m dedupe.decision` prints a dry-run impact report), `configs/synthetic_sources.json` (source mapping), blocking rules in `dedupe/blocking/rules.py`
(SQL), household evidence weights in `dedupe/households/evidence.py`.
