# Dedupe — handover guide for the new owner

> Short entry point for the person taking over. It points to the project's own documents, which are the reference and are
> kept up to date in the repository. Facts below are as of the latest commit (2026-11-24).
> Anything marked **Unknown – ask Chandresh** is not stated in the files.

## 1. What it is

A **customer-data deduplication and golden-record platform** for Indian **banking and insurance**. It reads many sources, finds the
records that belong to the same customer, gives each customer a **stable ID**, builds one **golden record** per customer, links
households, and sends doubtful pairs to people for a decision. Every decision is explainable and reversible. CPU only, one machine.
Branded Aurionpro in the portal.

Three ways to use the same engine: the **web portal** (`dedupe serve`, then `/ui`), the **command line / Python library**
(`dedupe run …`) and a **headless REST service** (`dedupe serve --no-ui`).

**Client / buyer:** a product for B2B sale; no customer, design partner or real data yet. Target opportunity and business owner:
Unknown – ask Chandresh.

## 2. Read in this order

| Document | Why |
|---|---|
| `docs/Technical_Overview.docx` | Short overview for a new reader (stack, algorithms, scheduling, next phase) |
| `README.md` | The reference: install, quick start, command line, portal step by step, REST API, rules, security, env vars, troubleshooting |
| `PROGRESS.md` | Where the work stands, open decisions, working agreements, **how to work on the EC2 server** |
| `docs/HANDOVER_QUESTIONS.md` | Answers to 8 handover questions (rules, results, review assignment, PAN/Aadhaar handling, learning from reviewers) |
| `docs/SECURITY.md` | Threat model, controls, known gaps, the gates before any real data |
| `docs/BENCHMARK.md` | Accuracy and speed on synthetic data |
| `docs/DEVELOPMENT_LOG.md` | Full history (sections 1-108) with every decision and its reason; code comments refer to it |
| `docs/RESEARCH_MULTIMODAL.md` | Research for the next phase (ID-card images, face matching) |

## 3. Status at handover

| Area | State |
|---|---|
| Product | Engine, portal, REST API (also headless), command line and Python library all work |
| Tests | 606 passed, 4 skipped (~10 min, in Docker); ruff clean |
| Accuracy | **Synthetic data only.** Balanced settings: F1 0.931 with identifiers, 0.918 without (1,200 households) |
| Scale | 692,537 records in 445 s on 8 CPUs |
| Real-data readiness | **No.** No independent security test, no legal review (DPDP, RBI KYC, IRDAI, UIDAI) |
| Code | GitHub `ChandreshKanyal/dedupah` (private, Chandresh's personal account) |

## 4. How to turn it on

Quickest test on any Linux/macOS machine (README §2.1, §3):
```
python3 -m venv .venv && . .venv/bin/activate
pip install .
python -m dedupe.synthetic --households 500 --seed 42 --out demo
dedupe run demo/bank_crm.csv demo/loans.csv -o out        # golden.csv, map.csv, review.csv, summary.json
```
Portal on one machine (README §6.1): `pip install ".[api]"`, set `DEDUPE_AUTH_MODE=local`, `DEDUPE_LOCAL_AUTH_SECRET`,
`DEDUPE_TOKEN_KEY`, `DEDUPE_WORK_DIR`, create an admin with `python -m dedupe.api.accounts_cli create-admin --username <you>`, then
`dedupe serve --port 8000` and open http://127.0.0.1:8000/ui. With Docker: README §2.3. Windows works for development; paths differ.

**On the shared EC2 server:** read `PROGRESS.md` → "How to work on this server" first. In short: your portal preview is the
`dedupe-preview` container on 127.0.0.1:8001; Shankar's service is on 127.0.0.1:8000; never touch the host PostgreSQL (5432), Superset
(8088), gunicorn (8080/8793), Ollama (11434) or Zabbix (10050); never run `docker compose down -v`. Reach the portal through an SSH port
forward. The `docker run` command for the preview after a reboot is in the AI assistant's memory notes on that server (file
`ec2-server-constraints`), not in the repository — copy it into the repo or a runbook before handover.

## 5. Rules that must survive the handover (from `PROGRESS.md`)

- After every change: a numbered section in `docs/DEVELOPMENT_LOG.md`, update `PROGRESS.md`, pytest + ruff clean, commit.
- Never store raw Aadhaar; credentials only in environment variables; never commit a secret.
- **Keep `DEDUPE_TOKEN_KEY` the same forever and back it up** (and `DEDUPE_DATA_KEY` when encryption is on): changing or losing it changes every Aadhaar token / makes encrypted files unreadable.
- Confirm before anything outward-facing (pushing, opening ports, deleting).

## 6. Open decisions and next steps (from `PROGRESS.md`)

1. New portal projects: start on **Balanced** instead of Careful? (Careful finds ~4 in 10 duplicates, Balanced ~9 in 10.)
2. **Next phase: integrate Shankar's image engine** (ID-card OCR, face matching). Only a proposal exists; agree the interface with Shankar first.
3. Engine robustness: ~5 million records (clustering is pure Python), resuming a run that dies, sources that change shape.
4. Smaller: match one new record against existing people; end-to-end checks of second approval, schedules and DB destinations;
   auto-start of the preview after a reboot; rename the package before any PyPI release (`dedupe` is taken).

Known gaps: email notices untested against a real mail server; no due dates/workload view for assignments; no transliteration between
scripts; no HTTPS/domain/backups/production mode on the server per the earlier notes (confirm current state).

## 7. Handover actions

- Transfer `ChandreshKanyal/dedupah` to a company GitHub organisation (or add the new owner).
- Give the new owner SSH access to the EC2 server (own key; add their IP to the security group).
- Back up the server's `.env` and `DEDUPE_TOKEN_KEY` somewhere the new owner can reach.
- Move the preview's `docker run` command and the `~/agent-review/` browser/benchmark scripts (on the server, not in git) into the repo or a runbook.
- Introduce the new owner to Shankar (image engine). A branch named `shankars-edits` also exists on the surveillance repo.

## 8. Questions for Chandresh

- Who is the business owner and target customer? Is development continuing?
- Is there HTTPS, a domain or any backup on the server now?
- Should Balanced become the default (open decision 1)?
