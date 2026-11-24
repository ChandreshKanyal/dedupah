# Customer data deduplication platform

Finds the records that belong to the same customer across many data sources and joins them into one person with a stable ID. It builds
one clean **golden record** per person, and sends doubtful pairs to people for a decision. It is built for banking and insurance customer
data in India (PAN, Aadhaar, Indian names, addresses and phone numbers). A project can also define fields of its own, so any other kind
of record works too. CPU only; runs on one machine.

There are three ways to use the same engine:

| Way | For | Start |
|---|---|---|
| **Web portal** | business users, data stewards, administrators | `dedupe serve`, then open `/ui` |
| **Command line and Python library** | analysts and engineers on a terminal, scripts, notebooks | `pip install .`, then `dedupe run …` |
| **Headless REST service** | other systems: schedulers, ETL jobs, applications | `dedupe serve --no-ui` |

> **Status.** Everything is tested on **synthetic data only**: about 600 automated tests and benchmarks on generated customers. It has
> not run on real customer data and has had no independent security test. The legal points (DPDP Act 2023, RBI KYC, IRDAI, UIDAI rules
> on Aadhaar) have not been reviewed by counsel. Read [Security and privacy](#13-security-and-privacy) before using real data.

Other documents:

| Document | What it is |
|---|---|
| [docs/Technical_Overview.docx](docs/Technical_Overview.docx) | a short overview (Word) for readers who do not need every detail |
| [PROGRESS.md](PROGRESS.md) | where the work stands, the open decisions, how to continue |
| [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md) | the full design record, with every decision and its reasons |
| [docs/SECURITY.md](docs/SECURITY.md) | the security design and threat model |
| [docs/BENCHMARK.md](docs/BENCHMARK.md) | measured accuracy and speed on synthetic data |
| [docs/HANDOVER_QUESTIONS.md](docs/HANDOVER_QUESTIONS.md) | answers to the questions raised at handover |
| [docs/RESEARCH_MULTIMODAL.md](docs/RESEARCH_MULTIMODAL.md) | research for the next phase (ID-card images, face matching) |

## Contents

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Quick start](#3-quick-start)
4. [Command line reference](#4-command-line-reference)
5. [Python library](#5-python-library)
6. [Running the server (portal and REST API)](#6-running-the-server-portal-and-rest-api)
7. [Using the web portal, step by step](#7-using-the-web-portal-step-by-step)
8. [Using the REST API headless, step by step](#8-using-the-rest-api-headless-step-by-step)
9. [How matching works](#9-how-matching-works)
10. [Matching rules](#10-matching-rules)
11. [Golden-record rules](#11-golden-record-rules)
12. [Review, decisions and governance](#12-review-decisions-and-governance)
13. [Security and privacy](#13-security-and-privacy)
14. [Configuration reference (environment variables)](#14-configuration-reference-environment-variables)
15. [Output files](#15-output-files)
16. [Development](#16-development)
17. [Troubleshooting](#17-troubleshooting)
18. [Known limits](#18-known-limits)

---

## 1. Requirements

| | Minimum | Notes |
|---|---|---|
| Operating system | Linux (Ubuntu 22.04+, Debian 12, RHEL 9 or similar) | Also runs on macOS; Windows works for development (paths in the examples differ) |
| Python | 3.10 or newer | Tested on 3.12 |
| CPU | 2 cores | More cores make blocking and scoring faster (DuckDB uses them all) |
| Memory | 4 GB for up to ~100,000 records | Measured: 692,537 records took 445 s on 8 CPUs, peak about 4.6 GB with the default DuckDB memory cap. About 30 GB is extrapolated for 5 million records |
| Disk | about 10× the size of the input files, free | Each run keeps its working files (Parquet) |
| Database (server only) | SQLite (built in) for trying it out; **PostgreSQL 14+** for anything shared or production | Holds users, decisions, rule versions and logs |
| Docker (optional) | Docker 24+ with compose | For the packaged server and PostgreSQL |
| Browser (portal) | a current Chrome, Edge or Firefox at desktop width | Not designed for phones |
| Network | none | The engine never calls out; the server listens on 127.0.0.1 unless told otherwise |

Optional extras, installed only when needed (`pip install ".[name]"`):

| Extra | Adds |
|---|---|
| `api` | the server: FastAPI, Uvicorn, JWT sign-in |
| `postgres`, `mysql`, `oracle`, `sqlserver`, `db2`, `redshift`, `snowflake`, `bigquery`, `databricks` | database connectors |
| `mongodb`, `sftp`, `files` | MongoDB, SFTP, and older or rarer file types (.xls, Avro, hardened XML) |
| `connectors` | the common connectors together (`postgres, mysql, files, oracle, mongodb, sftp`) |
| `dev` | tests, lint, security scanners |

---

## 2. Installation

### 2.1 Library and command line (any Linux machine)

```bash
sudo apt-get install -y python3 python3-venv git          # Debian / Ubuntu; use dnf on RHEL
git clone <repository-url> dedupah
cd dedupah
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install .                                              # the engine, the `dedupe` command and the Python library
dedupe --help                                              # check it works
```

Always install into a virtual environment: an unrelated public package on PyPI is also called `dedupe` and uses the same import name.

Before working with real data, set a secret for Aadhaar tokens. Keep it **the same for every run**, so the same Aadhaar number always
gives the same token:

```bash
export DEDUPE_TOKEN_KEY='<a long random secret>'           # generate one: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2.2 The server (portal and REST API) without Docker

```bash
pip install ".[api]"                     # ".[api,connectors]" to also read from databases
```

Then follow [section 6](#6-running-the-server-portal-and-rest-api).

### 2.3 The server with Docker

```bash
cp .env.example .env                     # edit it: database password, sign-in mode and secrets (section 14). Never commit .env
docker compose up -d postgres            # PostgreSQL 16, data in a Docker volume
docker compose build api
docker compose up -d api                 # the portal and API on http://127.0.0.1:8000 (this machine only)
docker compose run --rm api python -m dedupe.api.accounts_cli create-admin --username admin     # asks for the password
```

The `api` container:
- runs as an unprivileged user with a read-only file system;
- has all Linux capabilities dropped;
- keeps its results in `./data`.

Publish it beyond `127.0.0.1` only behind your own HTTPS proxy.

---

## 3. Quick start

Deduplicate two files from a terminal:

```bash
dedupe inspect crm.csv loans.xlsx        # 1. what the engine found in each file (no data is printed)
dedupe run crm.csv loans.xlsx -o out     # 2. deduplicate
ls out                                   # 3. golden.csv  map.csv  review.csv  summary.json  (plus work/ and identity/)
```

Example output of step 2, on synthetic bank CRM and loan files:

```
2,404 records became 1,997 people (balanced):
  built from several records: 345
  no match (one record each): 1,652
  pairs merged automatically: 475
  pairs for a person to decide: 4
  values rejected by the checks (not used): bank_crm: mobile 17, dob 29; loans: mobile 5, dob 11
```

To try it without your own data, make synthetic files first:

```bash
python -m dedupe.synthetic --households 500 --seed 42 --out demo     # bank_crm.csv, loans.csv, ... with known duplicates
dedupe run demo/bank_crm.csv demo/loans.csv -o out
```

---

## 4. Command line reference

```
dedupe inspect FILE... [--sheet NAME] [--json]
dedupe init    FILE... [-o job.json] [--preset P] [--format F] [--outputs LIST] [--sheet NAME]
dedupe run     FILE... | --job job.json  [-o OUT] [--preset P] [--format F] [--outputs LIST]
                                         [--rules rules.json] [--golden-rules golden.json] [--sheet NAME] [--json] [-v]
dedupe explain OUT PERSON_ID [--json]
dedupe serve   [--host 127.0.0.1] [--port 8000] [--no-ui]
```

**Files it reads:** `.csv`, `.txt`, `.tsv`, `.xlsx`, `.xls`, `.json`, `.jsonl` / `.ndjson`, `.xml`, `.parquet`, `.avro`, `.orc`. For
Excel, `--sheet` picks the sheet (default: the first). Databases and cloud storage are read through the server's connections
([section 7.3](#73-add-data)).

### `dedupe inspect`

For each file it shows:
- its columns;
- the column that looks like each row's own ID;
- the field each column would fill, how sure it is, and whether the column **name**, the **values** or both said so;
- the columns it will not use, with what their values look like.

It prints column names and types only, never values. `--json` prints the same as JSON.

```
/data/loans.csv: 25 columns; each row's id: loan_id
  aadhaar       <- aadhaar_no   (100% sure, from the name and values)
  dob           <- applicant_dob   (100% sure, from the name and values)
  full_name     <- applicant_name   (100% sure, from the name and values)
  record_date   <- disbursed_date   (85% sure, from the values)
  not used: employer (A short list of repeated values (a category)), loan_amount (Numbers or amounts), ...
```

Fields it can fill: `full_name`, `father_name`, `dob`, `gender`, `marital_status`, `mobile`, `mobile_alt`, `email`, `address`,
`address_alt`, `city`, `state`, `pincode`, `customer_number`, `pan`, `aadhaar`, `aadhaar_last4`, `aadhaar_reference_key`, `passport_no`,
`dl_no`, `voter_id`, `occupation`, `annual_income`, `related_name`, `party_role`, `relation_to_proposer`, `age`, `record_date`.

### `dedupe run`

Runs the whole pipeline (section 9) on the files with the automatic mapping, and writes the results to the folder given by `-o` (default
`dedupe_out`). A new run into the same folder replaces the previous results.

| Option | Values | Default | Meaning |
|---|---|---|---|
| `--preset` | `careful`, `balanced`, `catch_more` | `balanced` | How bold merging is (section 10.1) |
| `--format` | `csv`, `xlsx`, `parquet`, `json` | `csv` | File type of the results |
| `--outputs` | any of `golden,map,review,clean,links` | `golden,map,review` | Which results to write (section 15) |
| `--rules` | a JSON file | the shipped rules | Your own matching rules (section 10) |
| `--golden-rules` | a JSON file | the shipped rules | Your own golden-record rules (section 11) |
| `--job` | a job file | | Run a job made with `dedupe init` instead of files |
| `--json` | | | Print the summary as JSON (for scripts) |
| `-v` | | | Show the engine's own progress lines |

Exit codes:
- **0**: done.
- **2**: a problem you can fix: a missing file, an unknown file type, a mapping that names a column the file does not have, or an
  unknown output. The message goes to standard error and starts with `dedupe:`.

### `dedupe init` and job files

When the automatic mapping is not what you want, write a job, edit it, and run it:

```bash
dedupe init crm.csv loans.xlsx -o job.json --preset balanced --format xlsx --outputs golden,map,review
# edit job.json
dedupe run --job job.json -o out
```

A job file:

```json
{
  "sources": [
    {"source_id": "crm",
     "connector": {"type": "csv", "path": "/data/crm.csv"},
     "record_id": "cust_id",
     "fields": {"full_name": "full_name", "dob": "dob", "mobile": "mobile", "pan": "pan",
                "address": {"concat": ["addr_line1", "addr_line2", "locality"], "sep": ", "}},
     "attributes": ["segment", "branch_code"]}
  ],
  "preset": "balanced",
  "format": "xlsx",
  "outputs": ["golden", "map", "review"],
  "rules": null,
  "golden_rules": null
}
```

- `fields`: field name -> column. A field can also join columns (`{"concat": [...], "sep": " "}`) or use the first filled one
  (`{"coalesce": [...]}`).
- `attributes`: columns carried into the golden record unchanged.
- `record_id`: the column holding each row's own ID; `null` uses the row number.
- `rules`, `golden_rules`: full rule sets inline, instead of `--rules` / `--golden-rules`.

### `dedupe explain`

```
dedupe explain out P000000010
P000000010: 3 records
  bank_crm CIF00000013  +  loans LN00000242  <- match_name_dob_contact  score 100%
  bank_crm CIF00000013  +  loans LN00000241  <- match score alone  score 100%
  loans LN00000241  +  loans LN00000242  <- match score alone  score 100%
```

It lists every link that joined two of the person's records: the rule that decided it (or the match score alone), the score, and the
other rules that also held.

### `dedupe serve`

Starts the server ([section 6](#6-running-the-server-portal-and-rest-api)). `--no-ui` serves the REST API only, with no screens; it is
the same as `DEDUPE_UI=0`. It needs `pip install ".[api]"`.

---

## 5. Python library

```python
from dedupe import sdk

found = sdk.inspect("crm.csv")                 # {"columns", "record_id", "mapping": {field: {"column", "confidence", "why"}}, "types", "unused"}

res = sdk.run(["crm.csv", "loans.xlsx"], out="out", preset="balanced", fmt="parquet",
              outputs=("golden", "map", "review", "clean", "links"))
res.summary                                    # records, people, built_from_several_records, no_match, merged_pairs, pairs_for_a_person, files ...
golden = res.table("golden")                   # a pyarrow Table (golden.to_pandas() with pandas installed)
res.explain("P000000010")                      # why that person's records were joined

job = sdk.job_for(["crm.csv"], preset="careful")    # the automatic job as a dict: change it in code ...
job["sources"][0]["fields"]["mobile"] = "phone_2"
sdk.run(job=job, out="out2")                        # ... and run it
```

Errors you can fix raise `sdk.SdkError` with a plain message. To pick a sheet, give the file as `{"path": "book.xlsx", "sheet": "Customers"}`.

---

## 6. Running the server (portal and REST API)

The server holds projects, users, versioned rules, decisions and logs. It queues and schedules runs and serves the portal. First choose
how people sign in:

| Mode | `DEDUPE_AUTH_MODE` | Use for |
|---|---|---|
| Single sign-on | `oidc` (default in production) | Real deployments: your identity provider (Entra ID, Okta, Keycloak...) issues the tokens; roles come from a token claim |
| Username and password | `local` | Small teams and trials; users and roles are managed on the portal's Users and Roles screens |
| Development tokens | `dev` | Development only; refused in production |

### 6.1 Try it on one machine (username and password, SQLite)

```bash
. .venv/bin/activate
pip install ".[api]"
export DEDUPE_AUTH_MODE=local
export DEDUPE_LOCAL_AUTH_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"   # signs sign-in tokens
export DEDUPE_TOKEN_KEY='<a long random secret>'
export DEDUPE_WORK_DIR="$PWD/data/work"                    # projects, uploads and run files go here
mkdir -p "$DEDUPE_WORK_DIR"
python -m dedupe.api.accounts_cli create-admin --username admin                                   # asks for the password twice
dedupe serve --port 8000                                   # or: python -m dedupe.api --port 8000
```

Open `http://127.0.0.1:8000/ui` and sign in as `admin`. When the server is remote, forward the port to your laptop:
`ssh -L 8000:127.0.0.1:8000 <server>`. Keep the same `DEDUPE_LOCAL_AUTH_SECRET` across restarts, or everyone is signed out.

### 6.2 With PostgreSQL

```bash
export DEDUPE_DATABASE_URL='postgresql://dedupe:<password>@db-host:5432/dedupe'
```

Tables are created and upgraded at start-up; nothing else is needed. In production each project also gets its own database:
`DEDUPE_PROJECT_DATABASE_URL='postgresql://dedupe:<password>@db-host:5432/dedupe_{project}'`.

### 6.3 Production checklist

`DEDUPE_ENV=production` refuses to start until all of these are set:

- [ ] `DEDUPE_AUTH_MODE=oidc` with `DEDUPE_OIDC_ISSUER`, `DEDUPE_OIDC_AUDIENCE`, `DEDUPE_OIDC_JWKS_URL` (https) and
      `DEDUPE_OIDC_ROLE_MAP`. Local passwords only with `DEDUPE_ALLOW_LOCAL_AUTH=yes`.
- [ ] `DEDUPE_ALLOWED_HOSTS` (the real host names) and HTTPS (`DEDUPE_REQUIRE_HTTPS`, behind your TLS proxy with `DEDUPE_TRUST_PROXY=true`)
- [ ] `DEDUPE_DATABASE_URL` and `DEDUPE_PROJECT_DATABASE_URL` on PostgreSQL (not SQLite)
- [ ] `DEDUPE_DATA_KEY` (encryption at rest; generate it with `python -m dedupe.crypto new-key`) and `DEDUPE_TOKEN_KEY`
- [ ] `DEDUPE_DATA_ROOTS`: the only folders file sources may be read from

Also recommended:
- run the Docker `api` image (unprivileged, read-only);
- back up PostgreSQL and the work folder;
- keep the keys in your secret store.

Full security design: [docs/SECURITY.md](docs/SECURITY.md).

### 6.4 Headless

`DEDUPE_UI=0`, or `dedupe serve --no-ui`, serves the REST API without the portal's screens: `/ui` returns 404 and everything else is the
same. Use it where only other systems talk to the engine. The interactive API description is at `/docs` in development; set
`DEDUPE_API_DOCS=1` to keep it in production.

---

## 7. Using the web portal, step by step

Every project goes through the same five steps, shown as a step bar at the top of each project page:
**Data → Rules → Run → Review → Results**.

### 7.1 Sign in and Home

Sign in with your username and password, or your company sign-in. **Home** lists what needs you now, each item with the button that
does it:
- data to add;
- rules to choose;
- a run to start;
- pairs given to you;
- pairs to review;
- changes waiting for your approval.

Below that are your projects and how far each one is.

### 7.2 Create a project

**Projects → New project**: an ID (lower-case letters, digits, dashes), a name and a description. The project is private to you until you
add members on its **Access** tab. Each member gets a role: viewer, steward, approver, rule admin, analyst, or a custom role.

### 7.3 Add data

**Data → Add data**, then either:
- **upload a file** (CSV, Excel, JSON, XML, Parquet; up to 50 MB by default), or
- **connect a source**: a database, cloud storage, SFTP, Google Sheets or a REST API. Database passwords are never typed in: the screen
  asks for the **name of an environment variable** (starting with `DEDUPE_SRC_`) that the server's operator set.

The **mapping** screen then shows every column in three groups:
- **Please check**: the engine was not sure, and says why;
- **Mapped for you**: confident matches, where the name and the values agree;
- **Not used**: left out, or carried along unchanged as an extra column.

You can change any choice, join two columns, or define a field of your own (company name, GSTIN, policy number...). **Preview rows
first** shows the result, and **Save** adds the source. In a project with second approval, someone else approves it before it is in use.

The **Data** page then shows one card per source, with fill rates and the problems found (empty columns, rejected values).

### 7.4 Choose the rules

**Rules** is one page:
1. **Ready-made settings:** Careful, **Balanced** (recommended), or Catch more (section 10.1).
2. **Matching rules**, shown as cards in plain words. Switch any rule on or off, or **Edit** it in the rule builder:
   - group conditions as *All / Any / None / Not all of these*;
   - each line compares a field, checks a column's values, compares one column with another column of the other record, names a
     source, or uses the match score;
   - each rule is tested live on the latest run.
3. **Golden record:** the usual way to choose a value when records disagree, plus exceptions per field (section 11).
4. **What the reviewers' decisions say:** suggestions drawn from the decisions people made (appears once decisions exist).
5. **Advanced → Fields and search:** your own fields, how they are compared, and how candidate pairs are found.

One **Save** saves both rule sets as new versions. Every version is kept and can be restored.

### 7.5 Run

**Start run** (or **Run again**). Runs queue, and the page refreshes by itself. The run page shows:
- records read and people found;
- the people **built from several records** and those with **no match** (each number opens that list);
- pairs merged automatically and pairs sent to a person;
- anything the run could not use, such as a rule whose field is missing or an empty field;
- under **Details**: data quality per source (fill rates, rejected values), work reused from the previous run, and timings.

**Schedules** (a project tab) start runs hourly, daily or weekly. Each run is either incremental (unchanged records reused) or a full
reload.

### 7.6 Review

**Review** lists the pairs that need a person, from the newest run. For each pair it shows:
- why it is here;
- a side-by-side comparison;
- both people's records;
- the evidence for and against;
- earlier decisions.

Decide **Same person**, **Different people** or **Not sure**. The keyboard works too: S, D, U to decide, then N and P for next and
previous. Tick several pairs to decide them together.

Work is shared out like tickets:
- **Take this pair** makes it yours; **Give back** frees it.
- A lead (anyone who can approve decisions) can **Give** pairs to a named reviewer, who then sees them on Home and under **Given to me**.
- A pair given to someone else cannot be decided by others.
- Nobody can silently overwrite another reviewer's decision: only a lead can replace one, after confirming.

**Decided** lists every decision with who made it, its status and **Undo**. Decisions apply at the project's next run and stay in force
in every later run.

### 7.7 Customers and results

On the run's **Customers** tab you can search by name, ID, a whole mobile number or a whole email, and filter to *Built from several
records* or *No match*. Opening a customer shows:
- the golden record: **Correct** any value, and the correction stays from the next run on;
- the source records behind it: **Split** records that are not the same person;
- **Why these records were joined**: each link, with its rule or score.

**Results → Get the results** downloads the golden records, the customer-to-record map or households as Excel, CSV or Parquet. It can
also write them into a database table the project saved as a destination. Each download asks for a reason and is logged.

### 7.8 Administration

| Screen | What it does |
|---|---|
| **Admin → Users** | Create users (with an optional email for review notices), set roles, disable users, reset passwords (local sign-in) |
| **Admin → Roles** | Custom roles: any set of the permissions |
| **Admin → Audit log** | Security-relevant events, newest first, in a tamper-evident chain |
| **Approvals** | Decisions and changes waiting for a second person, across your projects |
| **Project → Access** | Members and their roles in the project; second approval on or off |
| **Project → Activity** | The project's history |

Identifiers (PAN, Aadhaar last 4, phone, email) are masked on every screen. **Show identifiers** needs the `pii.unmask` permission and is
logged.

---

## 8. Using the REST API headless, step by step

Every screen of the portal is built on this API, so a program can do anything the portal does. The steps below were run exactly as
written, with local sign-in and one CSV file. They need `curl` and `jq`.

```bash
API=http://127.0.0.1:8000

# 1. sign in (local mode); with single sign-on, use the token from your identity provider instead
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"<password>"}' | jq -r .token)
H="Authorization: Bearer $TOKEN"; J='Content-Type: application/json'

# 2. a project
curl -s -X POST $API/projects -H "$H" -H "$J" -d '{"id":"crm-check","name":"CRM check","description":""}'

# 3. upload a file, let the engine map it, save it as a source and put it in use
UP=$(curl -s -X POST "$API/projects/crm-check/uploads?filename=crm.csv" -H "$H" \
       -H 'Content-Type: application/octet-stream' --data-binary @crm.csv | jq -r .upload_id)
curl -s -X POST $API/projects/crm-check/connections/inspect -H "$H" -H "$J" \
     -d "{\"connector\":{\"type\":\"upload\",\"upload_id\":\"$UP\"}}" > insp.json
FIELDS=$(jq -c '[.suggestions[] | {key: .field, value: .column}] | from_entries' insp.json)
RID=$(jq -r .record_id insp.json)
V=$(curl -s -X POST $API/projects/crm-check/connections -H "$H" -H "$J" -d "{\"connection\":{\"source_id\":\"crm\",
      \"connector\":{\"type\":\"upload\",\"upload_id\":\"$UP\"},\"record_id\":\"$RID\",\"fields\":$FIELDS,\"attributes\":[]},
      \"reason\":\"add the CRM file\"}" | jq -r .version)
curl -s -X POST $API/projects/crm-check/connections/crm/versions/$V/activate -H "$H" -H "$J" -d '{"reason":"use this source"}'

# 4. rules: start from the standard ones (use_score=true is "balanced"), save them and put them in use
for n in decision survivorship; do
  C=$(curl -s $API/projects/crm-check/config/$n/default -H "$H" | jq -c .content)
  [ $n = decision ] && C=$(echo "$C" | jq -c '.use_score=true')
  V=$(curl -s -X POST $API/projects/crm-check/config/$n/propose -H "$H" -H "$J" \
        -d "{\"content\":$C,\"reason\":\"standard rules, balanced\"}" | jq -r .version)
  curl -s -X POST $API/projects/crm-check/config/$n/versions/$V/activate -H "$H" -H "$J" -d '{"reason":"use these rules"}'
done

# 5. run, and wait for it to finish
R=$(curl -s -X POST $API/projects/crm-check/runs -H "$H" -H "$J" \
      -d '{"connections":["crm"],"reason":"first run of the CRM file"}' | jq -r .id)
while true; do
  S=$(curl -s $API/projects/crm-check/runs/$R -H "$H" | jq -r .status)
  [ "$S" = succeeded ] || [ "$S" = failed ] && break; sleep 3
done
curl -s $API/projects/crm-check/runs/$R -H "$H" | jq '.summary | {records, people, merged_people, single_people, review_items}'

# 6. results: a page on screen, or a whole file
curl -s "$API/projects/crm-check/runs/$R/datasets/golden_records/rows?limit=25&built=several" -H "$H"
curl -s -X POST $API/projects/crm-check/runs/$R/export -H "$H" -H "$J" \
     -d '{"dataset":"golden_records","format":"csv","reason":"hand the golden records to the CRM team"}' -o golden.csv

# 7. the pairs for a person, and a decision on one
curl -s "$API/projects/crm-check/runs/$R/queue?limit=50" -H "$H"
curl -s -X POST $API/projects/crm-check/runs/$R/decisions/reject -H "$H" -H "$J" \
     -d '{"person_l":"P000001593","person_r":"P000001594","reason":"different fathers named on the KYC"}'
```

Rules for every call:
- JSON bodies are strict: unknown fields are refused.
- A reason of at least 10 characters is required wherever something changes.
- Personal values are masked, unless a person allowed to see them asks with `unmask=true`.
- Errors come back as `{"error": {"code", "message", "fields", "request_id"}}`.
- Decisions accept an `Idempotency-Key` header, so a retry never decides twice.

### Endpoint reference

| Area | Endpoints |
|---|---|
| Sign-in and you | `POST /auth/login`, `POST /auth/password`, `GET /auth/config`, `GET /me`, `GET /home` |
| Users and roles (admin) | `GET POST /users`, `PATCH /users/{u}`, `POST /users/{u}/password`, `GET POST /roles`, `PUT DELETE /roles/{r}` |
| Projects | `GET POST /projects`, `GET /projects/{p}`, `PATCH /projects/{p}/settings`, `POST /projects/{p}/delete`, `GET POST /projects/{p}/access`, `DELETE /projects/{p}/access/{subject}/{role}`, `GET /projects/{p}/activity` |
| Data | `GET /connectors`, `GET POST /projects/{p}/uploads`, `DELETE /projects/{p}/uploads/{id}`, `POST /projects/{p}/connections/inspect`, `.../preview`, `.../copy`, `GET POST /projects/{p}/connections`, `GET .../connections/{c}`, `.../diff`, `.../profile`, `POST .../test`, `POST .../versions/{v}/approve`, `decline`, `activate` |
| Rules | `GET /projects/{p}/config/{name}` (name: `decision`, `survivorship`, `settings`), `.../versions/{v}`, `.../default`, `.../diff`, `.../history`, `POST .../propose`, `.../versions/{v}/dry-run`, `approve`, `decline`, `activate`, `POST .../rollback`, `POST /projects/{p}/config/decision/try`, `.../test-rule`, `GET /rules-catalogue`, `GET /projects/{p}/rules-catalogue`, `GET /projects/{p}/golden-record-guide`, `GET /projects/{p}/config-log/verify` |
| Runs | `GET /runs`, `GET POST /projects/{p}/runs`, `GET /projects/{p}/runs/{r}`, `POST .../cancel`, `GET .../log`, `POST .../delete` |
| Schedules | `GET POST /projects/{p}/schedules`, `PATCH DELETE /projects/{p}/schedules/{s}` |
| Review | `GET /review/inbox`, `GET /projects/{p}/runs/{r}/queue` (filters: `status`, `min_probability`, `max_probability`, `forced`, `q`, `assigned=me\|free\|others`), `.../queue/summary`, `GET .../people/{id}`, `.../people/{id}/links`, `.../people/{a}/compare/{b}`, `POST .../decisions/approve`, `reject`, `unsure`, `bulk`, `POST .../people/{id}/split`, `.../override`, `POST /projects/{p}/decisions/{action}/undo`, `GET /projects/{p}/decisions`, `GET /projects/{p}/reviewers`, `POST /projects/{p}/review/assign`, `GET /projects/{p}/review/lessons`, `GET /projects/{p}/approvals`, `POST .../approvals/{id}/approve`, `decline` |
| Results | `GET /projects/{p}/runs/{r}/datasets`, `.../datasets/{name}/rows` (`golden_records` takes `built=several\|single`), `POST .../export`, `POST .../export/database`, `GET POST /projects/{p}/destinations`, and their `test`, `approve`, `decline`, `activate` |
| Health and logs | `GET /health`, `GET /security/events`, `GET /security/verify` |

---

## 9. How matching works

```
sources ─► ingest ─► standardise ─► blocking ─► scoring ─► decision ─► clustering ─► person IDs ─► golden record
                                                                                                 └► review queue, households
```

| Stage | What happens |
|---|---|
| **Ingest** | Every source is read in batches and mapped onto one customer schema; extra columns are kept as JSON |
| **Standardise** | Cleans every field: names, dates, phones, IDs and addresses (details in the list below this table) |
| **Blocking** | Finds candidate pairs from 18 key rules (same PAN, Aadhaar token, phone, email, surname sound + birth date, name + PIN...), plus the project's own rules and the two-column rules. Very common keys are capped |
| **Scoring** | Compares each pair field by field into levels (exact, sounds alike, fuzzy, differs, missing). Each level has a weight (Fellegi-Sunter, log m/u), learned without hand labels: from identifier-proven pairs with a regularised logistic regression, or with EM when a file has no identifiers. Rare values count more (term-frequency adjustment). The weights add up to a probability |
| **Decision** | Rules first, in the order never merge > ask a person > merge > not enough evidence. When no rule holds and the score is on, the score decides: merge above `auto_merge_at`, ask a person above `review_at` |
| **Clustering** | Merged pairs form groups. A group is split at its weakest link when it holds two different valid PANs or Aadhaar numbers, a vetoed pair, or a person's "different" decision. People's decisions are hard constraints |
| **Person IDs** | Stable `P` + 9 digits across runs. On a merge the oldest ID survives; retired IDs say where their records went |
| **Golden record** | One value per field, chosen by the golden-record rules (section 11); conflicts are listed per person |
| **Review queue** | The pairs of whole people to ask about, ranked |
| **Households** | Family links between people (shared address, phone, surname), not merges |
| **Incremental** | Unchanged records reuse the previous run's work |

What standardising does:
- **Names:** titles removed, and a phonetic form tuned for Indian spellings.
- **Dates:** many formats, including an age, a year only, or swapped day and month.
- **Phones:** reduced to 10 digits.
- **PAN:** shape check and OCR repair.
- **Aadhaar:** checked with the Verhoeff checksum, then turned into an HMAC token plus the last 4 digits. The number itself is erased.
- **Addresses:** split into text, PIN code and house number.
- **Placeholders** ("Unknown", "test@") count as empty.
- **Invalid values** are counted and not used.

Measured on synthetic data: F1 0.931 with identifiers and 0.918 without (1,200 households); 692,537 records in 445 s on 8 CPUs.

---

## 10. Matching rules

### 10.1 Ready-made settings

| Setting | `use_score` | `auto_merge_at` | `review_at` | Measured on synthetic data (with IDs / without IDs) |
|---|---|---|---|---|
| `careful` | false: rules only | | | finds about 39 / 37 in 100 duplicates; almost no wrong merges |
| `balanced` (recommended) | true | 0.9 | 0.1 | finds about 87 / 85 in 100; about 1 / 4 wrong merges in 1,000 |
| `catch_more` | true | 0.8 | 0.05 | finds about 88 / 88 in 100; slightly more wrong merges |

New portal projects start on Careful; the command line defaults to Balanced.

### 10.2 The rule file

```json
{
  "auto_merge_at": 0.9, "review_at": 0.1, "use_score": true,
  "rules": [
    {"id": "veto_pan_conflict", "type": "never_merge", "reason": "Two different valid PANs",
     "when": {"all": [{"comparison": "pan", "is": "different PANs"},
                      {"not": {"comparison": "aadhaar", "in": ["same Aadhaar token", "same last-4 (no full number on one side)"]}}]}},
    {"id": "match_name_dob_contact", "type": "auto_merge", "enabled": true,
     "reason": "Similar name, same birth date, and a phone, email or house agrees",
     "when": {"all": [{"comparison": "name", "in": ["identical cleaned name", "same name sounds (any order)"]},
                      {"comparison": "dob", "in": ["exact date", "day and month swapped"]},
                      {"any": [{"comparison": "phone", "is": "same primary number"}, {"comparison": "email", "is": "same email"}]}]}}
  ]
}
```

Rule types:
- `never_merge`: a veto, which wins over everything.
- `force_review`: always ask a person; `priority_offset` moves the pair up or down the queue.
- `auto_merge`: merge.
- `not_enough_evidence`: keep apart without asking.

A pair is decided **once**, by the strongest type that holds. Other rules that also hold change nothing: not the decision, not the score.
The shipped set is `configs/decision_default.json`.

### 10.3 Conditions

| Condition | Example |
|---|---|
| Groups | `{"all": [...]}`, `{"any": [...]}`, `{"not": {...}}` |
| A comparison's level | `{"comparison": "pan", "is": "same PAN"}`, `"is_not"`, `"in": [...]`, `"not_in": [...]`, `{"comparison": "phone", "missing": true}` |
| A field on both records | `{"field": "gender", "op": "differs"}` (or `same`), `{"field": "dob_year", "op": "abs_diff_gt", "value": 10}`, `both_gte`, `both_lte`, `both_born_within_last` |
| A column's values | `{"field": "x_segment", "op": "both_in", "values": ["Corporate"]}` (or `either_in`, `neither_in`), `both_contain`, `either_contains`, `within` (amount), `within_pct`, `within_days`, `either_empty`, `both_empty`, `both_filled` |
| The data source | `{"field": "source_id", "op": "differs"}` (also `state`, `record_date`) |
| Two different columns | `{"cross": ["x_parent_gstin", "x_gstin"], "op": "same"}` (or `similar`); `same` also finds such pairs |
| The score | `{"score": "probability", "op": ">=", "value": 0.97}` (also `match_weight`) |
| Any own field agrees | `{"own_fields": "agree"}` |

Comparisons and their levels:

| Comparison | Levels (best first) |
|---|---|
| `name` | identical cleaned name · same name sounds (any order) · first+last sound equal · initial + same surname · one side first-name only, first names agree · fuzzy full-name match (JW>=0.90) · same surname, different first name · different |
| `dob` | exact date · exact date, but a 1-Jan default · day and month swapped · same year (partial or differing day/month) · same day and month, different year · age-based: compatible · age-based: incompatible · different |
| `phone` | same primary number · number matches an alternate number · different |
| `email` | same email · different |
| `pan` | same PAN · different PANs |
| `aadhaar` | same Aadhaar token · same last-4 (no full number on one side) · different Aadhaar |
| `gov_id` | same passport / licence / voter ID · different document numbers |
| `address` | same PIN + house number + similar text · same PIN + similar text · same PIN · same city, PIN missing on one side · same city or PIN area, different PINs · different |
| `gender` | same gender · different gender |
| `customer_number` | same customer number · different customer numbers |
| `x_<own field>` | by kind: `text`, `company`, `name`, `code`, `number`, `date`, `category`, `phone`, `email` (see `src/dedupe/custom/fields.py`) |

Rules never contain SQL: the engine checks them and compiles them. A rule that names a field the run does not have is skipped and listed.

---

## 11. Golden-record rules

For each field, the strategy decides which value is kept when a person's records disagree:

| Strategy | Keeps |
|---|---|
| `most_recent` | the newest record's value (by record date), then the best quality, then the most trusted source |
| `most_trusted_source` | the value from the most trusted source (`source_trust` order), then the best quality, then the newest |
| `most_frequent` | the value most records agree on (outvotes typos) |
| `most_complete` | the longest, most filled-in value |
| `best_quality` | the cleanest value (a full name over an initial) |

The file (`configs/survivorship_default.json`) sets:
- `default_strategy`;
- `fields`: the strategy per field;
- `groups`: fields kept together, such as the whole address;
- `source_trust` and `field_trust`;
- `source_dates`: which column dates a source's records;
- `shared_value_limit`: a phone or email shared by more people than this is not chosen.

---

## 12. Review, decisions and governance

- **Decisions:** same person, different people, not sure, split a person, correct a golden value. They are stored in an append-only,
  hash-chained log with who, when, why and the evidence, and are applied in every later run.
- **Not sure** leaves the pair for someone else. **Undo** revokes a decision: your own, or anyone's if you are an approver.
- **Assignment:** take, give back or give pairs. A pair given to someone else cannot be decided by others. Another reviewer's decision is
  never overwritten silently: only a lead (with `judgment.approve`) can replace it.
- **Email notices** (optional) go to reviewers who have an address: after a run that leaves pairs, and when pairs are given to them. Set
  `DEDUPE_SMTP_HOST` and `DEDUPE_SMTP_FROM` (section 14). A notice contains no customer data.
- **Second approval (four eyes)**, switched on per project:
  - every change to sources, rules and destinations needs a second person;
  - so do some decisions: merging people with conflicting IDs, big merges, and overriding identifiers;
  - nobody approves their own change.
- **Lessons:** the Rules page counts decisions by the rule or score band that sent the pairs to people, and suggests rule changes. It
  changes nothing by itself.
- **Versions:** every rule and source change is a version with a reason. Any version can be compared, dry-run on the latest run, and
  restored.

Built-in roles:

| Role | Can |
|---|---|
| `viewer` | read queues, people, rules and runs (masked) |
| `steward` | + decide pairs, split, correct, unmask (logged) |
| `approver` | + approve changes and put them in use |
| `rule_admin` | + propose rule and setting changes |
| `analyst` | everything in their own projects |
| `auditor` | read everything, including logs and unmasked identifiers; changes nothing |
| `platform_admin` | runs, sources, projects, deletion, access |
| `admin` | everything, including users and roles |

---

## 13. Security and privacy

- **Aadhaar:** the full number is never stored. It is checked, turned into a keyed HMAC token (`DEDUPE_TOKEN_KEY`) plus the last 4
  digits, and erased from every file right after cleaning. A test checks that no raw Aadhaar appears in any output.
- **Encryption at rest:** with `DEDUPE_DATA_KEY` set, every Parquet file is AES-GCM encrypted. It does not cover your input files, the
  database or backups: use an encrypted volume and database for those.
- **Credentials:** only as environment variables (`DEDUPE_SRC_*` for sources, `DEDUPE_DST_*` for result databases). The screens store
  variable names, never secrets. `DEDUPE_ENV_GRANTS` decides which project may use which variable.
- **Access:** roles, per-project membership, masked identifiers with logged unmasking, rate limits, and a tamper-evident security log.
- **Network:** the server listens on 127.0.0.1 by default. Production requires HTTPS, a host allow-list and single sign-on. Sources can be
  limited to listed hosts (`DEDUPE_SOURCE_HOSTS`) and folders (`DEDUPE_DATA_ROOTS`).
- **Scans:** `python scripts/security_scan.py` runs lint, bandit and a dependency audit, and writes an SBOM.

Details and the threat model: [docs/SECURITY.md](docs/SECURITY.md).

---

## 14. Configuration reference (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DEDUPE_ENV` | `dev` | `production` turns every strict check on |
| `DEDUPE_AUTH_MODE` | `dev` (`oidc` in production) | `oidc`, `local` or `dev` |
| `DEDUPE_LOCAL_AUTH_SECRET` | | local sign-in: signs tokens (32+ characters) |
| `DEDUPE_ALLOW_LOCAL_AUTH` | no | allow local passwords in production |
| `DEDUPE_DEV_AUTH_SECRET` | | dev tokens (32+ characters); never in production |
| `DEDUPE_OIDC_ISSUER`, `DEDUPE_OIDC_AUDIENCE`, `DEDUPE_OIDC_JWKS_URL` | | single sign-on |
| `DEDUPE_OIDC_ALGORITHMS` | `RS256,ES256` | asymmetric only |
| `DEDUPE_OIDC_ROLES_CLAIM` | `roles` | where the roles are in the token (dotted path) |
| `DEDUPE_OIDC_ROLE_MAP` | `{}` | JSON: provider group -> role |
| `DEDUPE_MAX_TOKEN_LIFETIME_S` | 3600 | longest accepted token |
| `DEDUPE_DATABASE_URL` | SQLite in the work folder | users, decisions, rules, logs |
| `DEDUPE_PROJECT_DATABASE_URL` | | production: a per-project database URL template containing `{project}` |
| `DEDUPE_WORK_DIR` | `data/staging` | projects, uploads and run files |
| `DEDUPE_DATA_KEY`, `DEDUPE_DATA_KEY_ID` | | encryption at rest (`python -m dedupe.crypto new-key`) |
| `DEDUPE_TOKEN_KEY` | a development key (with a warning) | the Aadhaar token key; keep it stable |
| `DEDUPE_DATA_ROOTS` | | folders file sources may read (required in production) |
| `DEDUPE_SOURCE_HOSTS` | | hosts database and API sources may reach |
| `DEDUPE_SRC_*`, `DEDUPE_DST_*`, `DEDUPE_ENV_GRANTS` | | source and destination credentials, and which project may use them |
| `DEDUPE_UI` | 1 | 0 = headless (REST API only) |
| `DEDUPE_API_DOCS` | on in dev | `/docs` and `/openapi.json` |
| `DEDUPE_ALLOWED_HOSTS` | `*` | host-header allow-list (required in production) |
| `DEDUPE_REQUIRE_HTTPS` | on in production | refuse plain http |
| `DEDUPE_TRUST_PROXY` | off | read the client address from your proxy |
| `DEDUPE_CORS_ORIGINS` | none | other web origins allowed to call the API |
| `DEDUPE_MAX_CONCURRENT_RUNS` | 1 | runs at once (1-16) |
| `DEDUPE_MAX_QUEUED_RUNS` | 20 | runs waiting (1-1000) |
| `DEDUPE_SCHEDULER`, `DEDUPE_SCHEDULER_INTERVAL_S` | on, 30 | start scheduled runs; how often to check (5-3600 s) |
| `DEDUPE_UPLOAD_MAX_BYTES` | 50,000,000 | largest upload |
| `DEDUPE_MAX_BODY_BYTES`, `DEDUPE_MAX_PAGE_SIZE` | 1,000,000; 200 | request limits |
| `DEDUPE_RATE_LIMIT_PER_MIN`, `DEDUPE_IP_RATE_LIMIT_PER_MIN`, `DEDUPE_AUTH_FAIL_LIMIT_PER_MIN` | 240; 600; 20 | rate limits |
| `DEDUPE_UNMASK_LIMIT_PER_MIN`, `DEDUPE_UNMASK_REASON_REQUIRED` | 30; off | showing identifiers |
| `DEDUPE_EXPORT_LIMIT_PER_MIN` | 6 | downloads and database writes per person |
| `DEDUPE_SMTP_HOST`, `DEDUPE_SMTP_PORT`, `DEDUPE_SMTP_FROM`, `DEDUPE_SMTP_USER`, `DEDUPE_SMTP_PASSWORD`, `DEDUPE_SMTP_STARTTLS` | off; 587; none; none; none; on | email notices |
| `DEDUPE_PORTAL_URL` | | the portal's address, for links in notices |

`.env.example` lists the common ones. Copy it to `.env`, and never commit `.env`.

---

## 15. Output files

### From the command line (`-o out`)

| File | One row per | Columns |
|---|---|---|
| `golden.<fmt>` | person | `person_id`, `record_count`, `sources`, every golden field, extra columns, `conflict_fields` |
| `map.<fmt>` | source record | `person_id`, `source_id`, `source_record_id` |
| `review.<fmt>` | pair of people to decide | `person_a`, `name_a`, `person_b`, `name_b`, `match_score`, `sent_by_a_rule`, `records_a`, `records_b`, `record_pairs` |
| `clean.<fmt>` | source record | `person_id`, the record as cleaned for matching (`name_clean`, `dob`, `mobile`, `pan`, `aadhaar_last4`, `address_pin`...), `invalid_fields`, `oddities` |
| `links.<fmt>` | merge between two records | `person_id`, both records, `joined_by` (rule or match score), `rule`, `reason`, `match_score`, `also_true` |
| `summary.json` | | the counts, rejected values per source, rules not applied, files written |
| `work/`, `identity/` | | the engine's working files (kept for `dedupe explain`) and the person-ID registry |

Run again into the same folder and the same people keep the same person IDs.

### Inside a run folder (server: `<DEDUPE_WORK_DIR>/projects/<p>/runs/<r>/`)

| File | Holds |
|---|---|
| `canonical.parquet` | the records on one schema |
| `standardised.parquet` | the cleaned records |
| `candidates.parquet` | the candidate pairs |
| `scored_pairs.parquet` | per-field evidence for each pair |
| `decisions.parquet` | each pair's decision, the deciding rule, and every rule that held |
| `clusters/clusters.parquet` | record -> person |
| `clusters/possible_matches.parquet` | pairs between people |
| `review_queue.parquet` | the pairs for a person |
| `golden/golden_records.parquet` | the golden records |
| `households/` | family links |
| `model.json` | the learned weights, readable |
| `pipeline_summary.json` | the run's counts and timings |
| `config_used/` | the exact rules of the run |
| `run.log` | the run's log |

---

## 16. Development

```bash
pip install -e ".[dev,api,connectors]"
pytest -q                                   # about 600 tests, ~10 minutes
ruff check src tests                        # lint
python scripts/security_scan.py             # bandit, pip-audit, SBOM

# in Docker (as the project does it)
docker build -q --target test -t dedupe-test .
docker run --rm -v $PWD/src:/app/src:ro -v $PWD/tests:/app/tests:ro -v $PWD/configs:/app/configs:ro dedupe-test python -m pytest -q

# synthetic data with ground truth, the pipeline stage by stage, the benchmark
python -m dedupe.synthetic --households 1200 --seed 42 --out data/synthetic
python -m dedupe.pipeline --sources configs/synthetic_sources.json --truth data/synthetic
python -m dedupe.<stage> --help             # standardise, blocking, scoring, decision, clustering, survivorship, households
python -m dedupe.benchmark --out docs/BENCHMARK.md
```

`configs/*_default.json` and `src/dedupe/defaults/*.json` must stay identical; a test checks this. Change both together.

Layout:

| Folder | Holds |
|---|---|
| `src/dedupe/connectors`, `mapping`, `ingest.py` | reading sources, detecting column types, mapping |
| `src/dedupe/standardise` | cleaning (names, dates, phones, IDs, addresses) |
| `src/dedupe/blocking`, `scoring`, `decision`, `clustering`, `identity`, `survivorship`, `households` | the pipeline stages |
| `src/dedupe/custom` | a project's own fields |
| `src/dedupe/review`, `store` | review service, decision log, assignments, lessons |
| `src/dedupe/governance` | versioned rules, approvals, dry runs |
| `src/dedupe/workspace` | projects, uploads, run queue, schedules |
| `src/dedupe/api` | the REST API and the portal (`api/ui`: plain JavaScript, no build step) |
| `src/dedupe/sdk.py`, `cli.py`, `defaults/` | the library, the `dedupe` command, the shipped rules |
| `configs/` | the shipped rule files and the synthetic sources' mapping |
| `tests/` | the test suite |
| `docs/` | the documents listed at the top of this file |
| `scripts/` | `security_scan.py` (lint, bandit, dependency audit, SBOM), `make_technical_overview.py` (rebuilds the Word overview) |

Before changing anything, read `PROGRESS.md` (current state and working agreements). Record each piece of work as a numbered section in
`docs/DEVELOPMENT_LOG.md`.

---

## 17. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `DEDUPE_TOKEN_KEY not set: using an insecure development key` | Set `DEDUPE_TOKEN_KEY` (section 2.1). Fine for trying synthetic data |
| `dedupe: ... unknown file type` | Use a supported extension (section 4), or convert the file |
| `dedupe: ... the mapping names columns the file does not have` | Fix the column names in the job file; `dedupe inspect` lists them |
| The server refuses to start with a list of problems | The production checks: set what they name (section 6.3) |
| Everyone is signed out after a restart | `DEDUPE_LOCAL_AUTH_SECRET` changed: keep it fixed |
| A source "could be reached but not read" | Wrong sheet, encoding or delimiter, or a missing connector extra (`pip install ".[postgres]"` etc.) |
| A file source is refused as "outside the folders" | Add its folder to `DEDUPE_DATA_ROOTS` |
| Very few duplicates found | The project is on Careful: choose Balanced (section 10.1). Also check the mapping and the rejected values on the run page |
| One customer has many records | A shared office phone or a default ID can chain people together. Open the customer, read "Why these records were joined", Split, and consider a never-merge rule |
| A run is "interrupted" | The server restarted during it: run again |
| `dedupe serve needs the API extras` | `pip install ".[api]"` |

---

## 18. Known limits

- Tested on synthetic data only. Accuracy on real data is unknown until it is measured with labelled samples.
- Runs on one machine. Around 5 million records need more memory than a typical server has, because clustering is done in Python.
- No transliteration between scripts (Devanagari to Latin), and no large language model or neural network.
- Email notices have not been tried against a real mail server. Users who sign in through single sign-on have no email address in the
  portal.
- There is no call yet to match one new record against the existing people: only whole runs.
- The package name `dedupe` clashes with an unrelated PyPI package: install in a virtual environment.
- The portal is for desktop browsers.
