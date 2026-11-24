"""Builds docs/Technical_Overview.docx: a short technical overview with step-by-step use of the portal, the command line and the service.
Edit the text here and run `python scripts/make_technical_overview.py` (needs python-docx). The detailed reference is README.md."""
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).resolve().parents[1] / "docs" / "Technical_Overview.docx"
doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
for side in ("left_margin", "right_margin"):
    setattr(sec, side, Cm(2.2))
sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(2.0)

styles = doc.styles
base = styles["Normal"]
base.font.name, base.font.size = "Arial", Pt(10.5)
base.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
base.paragraph_format.space_after = Pt(5)
base.paragraph_format.line_spacing = 1.15
for name, size, before, after in (("Title", 24, 0, 4), ("Heading 1", 15, 16, 6), ("Heading 2", 12, 10, 4)):
    st = styles[name]
    st.font.name, st.font.size, st.font.bold = "Arial", Pt(size), True
    st.font.color.rgb = RGBColor(0x1F, 0x1F, 0x1F)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    st.paragraph_format.space_before, st.paragraph_format.space_after = Pt(before), Pt(after)
    st.paragraph_format.keep_with_next = True
for name in ("List Bullet",):
    styles[name].font.name = "Arial"


def shade(cell_or_par, fill):
    el = cell_or_par._tc if hasattr(cell_or_par, "_tc") else cell_or_par._p
    pr = el.get_or_add_tcPr() if hasattr(cell_or_par, "_tc") else el.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def para(text="", bold_lead=None, italic=False, size=None, after=None):
    p = doc.add_paragraph()
    if bold_lead:
        r = p.add_run(bold_lead)
        r.bold = True
    r = p.add_run(text)
    r.italic = italic
    if size:
        r.font.size = Pt(size)
    if after is not None:
        p.paragraph_format.space_after = Pt(after)
    return p


def bullet(text, bold_lead=None):
    p = doc.add_paragraph(style="List Bullet")
    if bold_lead:
        p.add_run(bold_lead).bold = True
    p.add_run(text)
    p.paragraph_format.space_after = Pt(2)
    return p


def step(n, text, bold_lead=None):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.8)
    p.paragraph_format.first_line_indent = Cm(-0.8)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.tab_stops.add_tab_stop(Cm(0.8))
    p.add_run(f"{n}.\t").bold = True
    if bold_lead:
        p.add_run(bold_lead).bold = True
    p.add_run(text)
    return p


def code(*lines):
    for i, line in enumerate(lines):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0) if i < len(lines) - 1 else Pt(7)
        p.paragraph_format.space_before = Pt(5) if i == 0 else Pt(0)
        p.paragraph_format.line_spacing = 1.0
        p.paragraph_format.left_indent = Cm(0.3)
        r = p.add_run(line if line else " ")
        r.font.name, r.font.size = "Consolas", Pt(8.5)
        r.element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
        shade(p, "F2F2F2")


def table(header, rows, widths):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold, r.font.size = True, Pt(9.5)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade(c, "262626")
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            if isinstance(v, tuple):
                p.add_run(v[0]).bold = True
                v = v[1]
            r = p.add_run(v)
            r.font.size = Pt(9.5)
    for i, w in enumerate(widths):
        t.columns[i].width = Cm(w)
    for row in t.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
            for p in row.cells[i].paragraphs:
                p.paragraph_format.space_after = Pt(1)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def note(text, bold_lead):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    c = t.rows[0].cells[0]
    c.width = Cm(16.6)
    shade(c, "FFF4D6")
    p = c.paragraphs[0]
    p.add_run(bold_lead).bold = True
    p.add_run(text)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


# ---- footer with page numbers
fp = sec.footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
fr = fp.add_run("Dedupe platform: technical overview  ·  page ")
fr.font.size = Pt(8)
for kind, txt in (("begin", None), (None, "PAGE"), ("end", None)):
    r = fp.add_run()
    r.font.size = Pt(8)
    if kind:
        f = OxmlElement("w:fldChar")
        f.set(qn("w:fldCharType"), kind)
        r._r.append(f)
    else:
        it = OxmlElement("w:instrText")
        it.set(qn("xml:space"), "preserve")
        it.text = txt
        r._r.append(it)

# ---- title
doc.add_paragraph("Dedupe Platform", style="Title")
para("Technical overview: what it does, how it works, and how to use it from the web portal, the Linux command line and as a "
     "headless service", size=12, after=2)
para("Version of 24 November 2026", italic=True, size=9, after=10)
note(" This is a short overview for readers who need the big picture and the main steps. The complete reference is README.md in the "
     "repository: every command and option, every setting, the rule language, the REST endpoints, the output files and troubleshooting. "
     "The design record with every decision and its reasons is docs/DEVELOPMENT_LOG.md.", "Overview only.")
note(" Everything described here is tested on synthetic (generated) data only. It has not run on real customer data, has had no "
     "independent security test, and the legal points (DPDP Act 2023, RBI KYC, IRDAI, UIDAI rules on Aadhaar) have not been reviewed.",
     "Status.")

# ---- 1
doc.add_heading("1. What the platform does", level=1)
para("It reads customer records from many sources (files, databases, cloud storage, SaaS systems) and finds the records that belong to "
     "the same person. It joins them under one stable person ID and builds one clean golden record per person. Pairs the engine is unsure "
     "about go to people for a decision. Every decision, rule change and data export is logged and can be undone. It is built for Indian "
     "banking and insurance data (PAN, Aadhaar, Indian names, addresses and phones). A project can also define fields of its own, so it "
     "works for other kinds of records too.")
para("One engine, three ways to use it:", after=2)
table(["Way", "For", "How it starts"], [
    ("Web portal", "Business users, data stewards, administrators", "dedupe serve, then open /ui in a browser"),
    ("Command line and Python library", "Analysts and engineers on a terminal, scripts, notebooks", "pip install, then dedupe run …"),
    ("Headless REST service", "Other systems: schedulers, ETL jobs, applications", "dedupe serve --no-ui"),
], [4.2, 6.4, 6.0])

# ---- 2
doc.add_heading("2. How it works", level=1)
table(["Stage", "What happens", "Technique"], [
    ("Ingest", "Read every source into one customer schema", "26 connector types, streamed in batches (Apache Arrow)"),
    ("Column mapping", "Detect what each column holds and map it to a field", "Header synonyms plus value profiling: patterns, checksums, value spread"),
    ("Standardise", "Clean names, dates, phones, emails, addresses and IDs; reject invalid values", "Rule-based text processing tuned for Indian data (section 3)"),
    ("Blocking", "Choose which pairs to compare instead of all n² pairs", "18 SQL key rules (same PAN, phone, surname sound + birth date, name + PIN…) plus the project's own"),
    ("Scoring", "A match probability for every candidate pair", "Fellegi-Sunter weights learned without hand labels (section 4)"),
    ("Decision", "Merge, ask a person, or keep apart", "JSON rules compiled to SQL; never merge > ask > merge > not enough evidence; score thresholds"),
    ("Clustering", "Group merged pairs into people", "Connected components, split at the weakest link wherever a group holds a contradiction"),
    ("Person IDs", "Stable IDs across runs", "An ID registry; on a merge the oldest ID survives"),
    ("Golden record", "The best value per field", "Survivorship strategies: newest, most trusted source, most frequent, most complete, best quality"),
    ("Review and households", "Doubtful pairs for people; family links", "Ranked queue; decisions applied in every later run"),
    ("Incremental runs", "Process only what changed", "Row fingerprints; reuse of earlier scores and model"),
], [3.0, 6.0, 7.6])

# ---- 3
doc.add_heading("3. Text processing (NLP)", level=1)
para("Rule-based and tuned for Indian data. No large language model or neural network is used.", after=3)
bullet("titles (Mr, Smt, Dr…) removed; \"S/O\", \"W/O\" and joint names split; OCR errors repaired; initials detected.", "Names: ")
bullet("a phonetic skeleton tuned for Indian spellings (Mohd / Muhammad / Md, Sharma / Sarma).", "Name sounds: ")
bullet("Jaro-Winkler and token comparisons, so names match in any order.", "Name similarity: ")
bullet("many formats, swapped day and month, year only, age or age band; placeholder dates (1 January, 1900) flagged.", "Dates: ")
bullet("+91 and leading 0 handled; several numbers in one cell split; numbers that Excel rounded are rejected.", "Phones: ")
bullet("PAN shape check and repair. Aadhaar is checked with the Verhoeff checksum and never stored: only a keyed HMAC token and the "
       "last 4 digits are kept. GSTIN, passport, driving licence and voter ID are also handled.", "Identifiers: ")
bullet("normalised text, PIN code and house number. Placeholders (\"Unknown\", \"test@\") count as empty.", "Addresses: ")

# ---- 4
doc.add_heading("4. Machine learning and matching", level=1)
bullet("each field comparison has levels (exact, sounds alike, fuzzy, differs, missing). Each level has a weight, log(m/u), and the weights "
       "add up to a match probability.", "Probabilistic record linkage (Fellegi-Sunter): ")
bullet("pairs whose PAN or Aadhaar agree or conflict serve as labels for an L2-regularised logistic regression. It learns the weights "
       "of the other fields without double-counting correlated fields.", "Weights learned without hand labels: ")
bullet("when a file has no identifiers, unsupervised EM (expectation maximisation) learns the weights, judged against a random-pair prior.",
       "No identifiers: ")
bullet("a rare agreeing name counts more than a common one.", "Term-frequency adjustment: ")
bullet("conditions in JSON, compiled to safe SQL and written with a no-code builder in the portal.", "Rules engine: ")
bullet("a group holding two different valid PANs or Aadhaar numbers, a vetoed pair or a person's \"different\" decision is split at its "
       "weakest link. It is deterministic and every split can be traced.", "Clustering guard: ")
bullet("people's decisions are hard constraints in every later run. They also produce suggestions to change the rules; they do not "
       "silently retrain the model.", "Human in the loop: ")
para("Measured on synthetic data: F1 0.931 with identifiers and 0.918 without (1,200 households, Balanced setting); 692,537 records "
     "in 445 seconds on 8 CPUs.", italic=True)

# ---- 5
doc.add_heading("5. Scheduling and running", level=1)
bullet("runs go first in, first out, with a limit on how many run at once (default 1). A project never has two runs at once. Each run is "
       "its own process and can be cancelled.", "Run queue: ")
bullet("hourly, daily or weekly in the schedule's own time zone, daylight saving included.", "Schedules: ")
bullet("each due schedule is claimed with one atomic database update and carries an idempotency key, so it never starts twice.",
       "Never started twice: ")
bullet("a missed slot runs once after downtime, and a schedule never piles up behind its own run.", "Downtime: ")
bullet("a schedule pauses itself after 3 failures in a row, or when its source or its owner's access goes away.", "Pausing: ")
bullet("each run is incremental (unchanged records reuse earlier work) or a full reload.", "Loads: ")

# ---- 6
doc.add_heading("6. Technology stack", level=1)
table(["Layer", "Used"], [
    ("Language", "Python 3.10+ (tested on 3.12)"),
    ("Data engine", "DuckDB (SQL on Parquet, spills to disk), Apache Arrow / PyArrow, Polars, NumPy"),
    ("Storage", "Parquet files per run (optionally AES-GCM encrypted); SQLite or PostgreSQL for users, decisions, rule versions and logs (SQLAlchemy)"),
    ("API", "FastAPI and Uvicorn, Pydantic, JWT sign-in: single sign-on (OIDC) or local accounts with scrypt passwords"),
    ("Portal", "Plain JavaScript, HTML and CSS served by the API (no framework, no build step)"),
    ("Sources", "CSV, Excel, JSON, XML, Parquet, Avro, ORC, fixed-width, folders; PostgreSQL, MySQL, SQL Server, Oracle, DB2, Snowflake, BigQuery, "
                "Redshift, Databricks, SQLite, DuckDB; MongoDB; S3, GCS, Azure Blob; SFTP; Google Sheets; REST APIs, Salesforce, HubSpot, Dynamics 365"),
    ("Packaging", "A pip package with the dedupe command; Docker images; docker compose with PostgreSQL"),
    ("Quality", "About 600 automated tests (pytest), ruff, bandit, pip-audit, SBOM"),
], [3.2, 13.4])
para("Security in short: roles and per-project membership, optional second approval, masked identifiers with every unmask logged, a "
     "hash-chained audit log, rate limits, credentials only from environment variables, and encryption at rest.")

# ---- 7
doc.add_heading("7. Requirements", level=1)
table(["Item", "Minimum"], [
    ("Operating system", "Linux (Ubuntu 22.04+, Debian 12, RHEL 9 or similar); macOS also works"),
    ("Python", "3.10 or newer, with venv"),
    ("CPU and memory", "2 cores and 4 GB for up to about 100,000 records; 8 cores and 8 GB handled 692,537 records"),
    ("Disk", "About 10 times the size of the input files, free"),
    ("Database (server)", "SQLite for trying it out; PostgreSQL 14+ for shared or production use"),
    ("Optional", "Docker 24+ with compose; a current desktop browser for the portal"),
    ("Network", "None needed: the engine never calls out; the server listens on 127.0.0.1 unless told otherwise"),
], [4.0, 12.6])

# ---- 8 portal
doc.add_heading("8. Using the web portal, step by step", level=1)
para("Each project goes through five steps, shown as a step bar on every project page: Data, Rules, Run, Review, Results.", after=3)
step(1, " Open the portal address (for example http://127.0.0.1:8000/ui) and sign in. Home lists what needs you now, each item with the "
     "button that does it.", "Sign in.")
step(2, " Projects, New project: give an ID, a name and a description. The project is private until you add members on its Access tab.",
     "Create a project.")
step(3, " Data, Add data: upload a file (CSV, Excel, JSON, XML, Parquet) or connect a database, cloud storage, SFTP, Google Sheets or an API. "
     "Passwords are never typed in: the screen asks for the name of an environment variable the server's operator set.", "Add data.")
step(4, " The engine sorts the columns into Please check (with reasons), Mapped for you, and Not used. Correct anything, join columns "
     "or add a field of your own, preview the rows, and Save.", "Check the mapping.")
step(5, " Pick a ready-made setting: Careful (rules only), Balanced (recommended) or Catch more. Switch rules on or off, or Edit one in the "
     "no-code builder (All / Any / None groups, tested live on the latest run). Choose how the golden record picks values, then Save.",
     "Choose the rules.")
step(6, " Start run. The run page shows records read, people found, the people built from several records and those with no match, "
     "pairs merged and pairs for a person, and data quality per source. Schedules can repeat runs hourly, daily or weekly.", "Run.")
step(7, " Review shows each doubtful pair side by side with the evidence. Decide Same person, Different people or Not sure; the "
     "keyboard works too. Take a pair to keep it yours, or, as a lead, give pairs to a reviewer. Another reviewer's decision is never "
     "overwritten silently.", "Review.")
step(8, " On the Customers tab, open a person to see the golden record, the source records behind it and why they were joined. "
     "Correct a value or split wrong merges. Results downloads Excel, CSV or Parquet, or writes to a saved database table; every export "
     "asks for a reason and is logged.", "Use the results.")
step(9, " Admin has Users (with an optional email for review notices), Roles, the Audit log and Approvals.", "Administer.")

# ---- 9 CLI
doc.add_heading("9. Using the command line on Linux, step by step", level=1)
step(1, " Install Python, then the package into a virtual environment:", "Install.")
code("sudo apt-get install -y python3 python3-venv git",
     "git clone <repository-url> dedupah && cd dedupah",
     "python3 -m venv .venv && . .venv/bin/activate",
     "pip install .",
     "dedupe --help")
step(2, " Set it once and keep it the same for every run:", "Set the Aadhaar token secret.")
code("export DEDUPE_TOKEN_KEY='<a long random secret>'")
step(3, " See what the engine finds in your files. Only column names and types are printed, never data:", "Inspect.")
code("dedupe inspect crm.csv loans.xlsx")
step(4, " Choose the setting, the file type and the outputs:", "Run.")
code("dedupe run crm.csv loans.xlsx -o out \\", "    --preset balanced --format xlsx \\", "    --outputs golden,map,review,clean,links")
para("It prints a summary, for example: \"2,404 records became 1,997 people: 345 built from several records, 1,652 no match, 475 pairs "
     "merged, 4 pairs for a person\". The folder out/ then holds one file per output and summary.json:", after=2)
table(["Output", "Contents"], [
    ("golden", "One clean record per person, with how many records built it and from which sources"),
    ("map", "Every source record with its person ID"),
    ("review", "Pairs for a person to decide"),
    ("clean", "Every record as cleaned for matching"),
    ("links", "Every merge, with the rule or score that made it"),
], [2.6, 14.0])
step(5, " To fix the mapping, write a job file, edit it, and run it:", "Adjust if needed.")
code("dedupe init crm.csv loans.xlsx -o job.json", "dedupe run --job job.json -o out")
step(6, " To see why a person's records were joined:", "Explain.")
code("dedupe explain out P000000010")
para("From Python the same is one call: from dedupe import sdk; sdk.run([\"crm.csv\"], out=\"out\"). Exit code 0 means done; 2 means a "
     "problem you can fix, explained on standard error.")

# ---- 10 headless
doc.add_heading("10. Running it as a headless service, step by step", level=1)
step(1, " Install the package with the server extras:", "Install.")
code("pip install \".[api]\"")
step(2, " Set the sign-in mode and secrets. Use PostgreSQL for anything shared:", "Configure.")
code("export DEDUPE_AUTH_MODE=local",
     "export DEDUPE_LOCAL_AUTH_SECRET=\"$(openssl rand -base64 48)\"",
     "export DEDUPE_TOKEN_KEY='<a long random secret>'",
     "export DEDUPE_WORK_DIR=/srv/dedupe/work",
     "export DEDUPE_DATABASE_URL='postgresql://dedupe:<password>@db-host:5432/dedupe'")
step(3, " The password is asked for and never typed on the command line:", "Create the first administrator.")
code("python -m dedupe.api.accounts_cli create-admin --username admin")
step(4, " Start the REST API with no screens:", "Start the service.")
code("dedupe serve --no-ui --port 8000", "curl http://127.0.0.1:8000/health      # {\"status\":\"ok\"}")
step(5, " Other systems sign in, then work through the REST API:", "Call it.")
code("API=http://127.0.0.1:8000",
     "TOKEN=$(curl -s -X POST $API/auth/login \\",
     "  -H 'Content-Type: application/json' \\",
     "  -d '{\"username\":\"admin\",\"password\":\"<password>\"}' | jq -r .token)",
     "curl -s -X POST $API/projects \\",
     "  -H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' \\",
     "  -d '{\"id\":\"crm-check\",\"name\":\"CRM check\",\"description\":\"\"}'")
para("The remaining calls, all shown with tested commands in README section 8:", after=2)
bullet("upload a file and let the engine map it;")
bullet("save the source and the rules;")
bullet("start a run and wait for it;")
bullet("read or export the golden records, and decide review pairs.")
para("For production, single sign-on, HTTPS, a host allow-list, PostgreSQL and an encryption key are required. The server refuses to "
     "start without them (README section 6.3).")

# ---- 11
doc.add_heading("11. Known limits", level=1)
bullet("Accuracy on real data is unknown until it is measured with labelled samples.")
bullet("It runs on one machine; about 5 million records would need more memory than a typical server has.")
bullet("There is no transliteration between scripts (Devanagari to Latin), and no large language model.")
bullet("There is no call yet to match a single new record against the existing people.")

# ---- 12
doc.add_heading("12. Next phase: integration with Shankar's image engine", level=1)
para("Not built yet. Shankar's engine has not been looked at and there is no interface specification for it yet. The plan below assumes "
     "it can read ID-card images (OCR) and produce face embeddings, and must be agreed with him first.", italic=True)
step(1, " A new connector sends images (a folder or a bucket) to the engine and receives fields: name, date of birth, PAN, Aadhaar, "
     "address, document type and confidence. They become ordinary records, so cleaning, matching and the golden record work unchanged; "
     "low-confidence fields count less.", "Extraction as a source.")
step(2, " A new face comparison with levels (same face, similar, different, no photo) from embedding similarity. It counts in the score "
     "and in rules, for example \"same face but a different PAN: ask a person\". Nearest-neighbour blocking finds the same face across "
     "sources.", "Face match as evidence.")
step(3, " The pair screen shows both document images and the face score, fetched from the engine when needed and never copied.",
     "Review with images.")
step(4, " A versioned REST/JSON interface (extract, embed, compare) with service-to-service authentication, timeouts, retries and "
     "batches. Each run records the engine's version.", "Contract.")
step(5, " Face images and embeddings are biometric data: consent and purpose limits apply. Aadhaar images must follow UIDAI masking rules, "
     "and retention and deletion must cover both systems. A security review is needed before any real data.", "Compliance first.")

doc.add_heading("Where to find more", level=1)
bullet("every command, option, setting, rule condition, REST endpoint, output file and troubleshooting step.", "README.md: ")
bullet("where the work stands now, open decisions and how to continue.", "PROGRESS.md: ")
bullet("the design record: what was built, why, how it was tested, and the known gaps.", "docs/DEVELOPMENT_LOG.md: ")
bullet("the security design and threat model.", "docs/SECURITY.md: ")
bullet("answers to the questions raised at handover.", "docs/HANDOVER_QUESTIONS.md: ")

doc.save(OUT)
print("saved", OUT)
