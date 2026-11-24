# Answers to the handover questions (2026-11-24)

These questions were asked against an older version. Each answer says what the **current** version does: what already existed, and what was
added for the question. Everything was tested on synthetic data only (`tests/test_review_questions.py`, browser checks in
`~/agent-review/questions/`). PROGRESS.md section 106 has the engineering details.

| # | Section | Question (short) | Status |
|---|---|---|---|
| 1 | Rules | Name, birth date, phone, email repeated across rules | Changed: one combined starter rule |
| 2 | Results | Unmatched records and golden records with their sources | Added: the counts, a filter, links to source records |
| 3 | Review | How a reviewer who is not the admin finds work | Existed in the portal; added assignment and optional email |
| 4 | Review | Assigning pairs, tracking who reviewed what, no double work | Added: assignment and a guard against overwriting |
| 5 | Matching | Missing or invalid PAN and Aadhaar | Existed: explained below |
| 6 | Rules | A pair that matches more than one rule | Existed: explained; now also shown on screen |
| 7 | Results | Which rule or score joined a customer's records | Added: "Why these records were joined" |
| 8 | Review | Does the engine learn from reviewers? | Explained; added suggestions from their decisions |

---

## 1. Rules: why do Name, Date of birth, Phone and Email appear again and again?

**Before:** a new project started with three separate rules: "name + birth date + phone", "name + birth date + email" and "name + birth date
+ same house". Name and birth date were written out three times.

**Now:** one rule, **"Similar name, same birth date, and a phone, email or house agrees"**. It is a group: *all of* (name, birth date,
*any of* (phone, email, house)). It decides exactly as the three did: the benchmark is unchanged (`~/agent-review/scoring/presets.py`:
Balanced F1 0.931 with identifiers, 0.918 without). The rule builder (Rules, Edit on any rule) makes such groups for any rule.

Other rules still name some of these columns again, on purpose. Each rule is a separate business decision that can be switched off on
its own. For example, "never merge when birth years are more than 10 years apart" and "ask a person when only a similar name and a birth
date agree" have different outcomes, so they cannot be one rule. Most of the repetition is the safety clause "unless a PAN or Aadhaar
agrees", which keeps a veto from overruling an identifier.

Code: `PROJECT_MATCHES` in `src/dedupe/api/routes_read.py`; title in `RULE_TITLE` (`ui/js/rules.js`). Existing projects keep the rules
they saved; only new projects start with the combined rule.

## 2. Results: unmatched records, and golden records with their source records

**Before:** the run page said "2,404 records became 1,997 customers", without the split.

**Now** (run page, Overview), a second row of numbers. On the synthetic CRM and loans files:
- **Built from several records:** 345 (these are the duplicates found);
- **Records behind them:** 752;
- **No match (one record each):** 1,652.

Clicking a number opens **Customers** filtered to that group. The filter ("All / Built from several records / No match") is also on the
Customers tab. Opening a customer shows the **golden record**, the **source records** that built it (source and record id), and why they
were joined (question 7).

For use outside the portal, the Results download has the golden records with `record_count` and `sources` per customer, and the
"customer-to-record map" (which source record belongs to which customer).

Code: `headline()` in `src/dedupe/workspace/runs.py` (`single_people`, `merged_people`); `built=several|single` on
`GET .../datasets/golden_records/rows` (`results/export.py: preview`); `runOverview` and `customersPanel` in `ui/js/runs.js`.

## 3. Review: how does a reviewer who is not the admin find out there is work?

**Already there (inside the portal):** every person who can decide pairs in a project sees, on **Home**, "N pairs in <project> need your
decision" with a Review button. The **Review** inbox lists every project's open pairs from its newest run. Nothing needs to be sent:
the work list is shared, and a decided pair leaves everyone's list at once.

**Added:**
- **Pairs given to you:** a lead can give pairs to a named reviewer (question 4). That person's Home then says "N pairs in <project> are
  given to you", and Review opens on **Given to me**.
- **Email (optional):** an administrator can give each user an email address (Admin, Users, Edit). When the server is given a mail server,
  each reviewer of the project with an address gets an email in two cases: after a run that leaves pairs for a person, and when pairs are
  given to them. The email holds the project name, the count and a link, and no customer data. It is **off** until whoever runs the
  server sets `DEDUPE_SMTP_HOST`, `DEDUPE_SMTP_FROM`, and optionally `DEDUPE_SMTP_USER` / `DEDUPE_SMTP_PASSWORD`, `DEDUPE_SMTP_PORT`
  and `DEDUPE_PORTAL_URL` (see `docs/SECURITY.md`). The password only ever comes from the environment.
- **Not tested against a real mail server** (none is set up here); the sending code is tested with a stand-in server. Users who sign in
  through a company identity provider (OIDC) have no address in the portal yet, so they rely on Home and Review.

Code: `src/dedupe/api/notify.py`, `notify_run_finished` and `reviewers` in `api/routes_review.py`, the `email` column in `api/accounts.py`.

## 4. Review: assigning pairs, tracking who reviewed what, and stopping two reviewers working on the same pair

**Already there:** every decision records who made it, when, and why, in a tamper-evident log. Review, **Decided** lists each decision with
its reviewer, its status (in force, waiting for the next run, waiting for a second person, undone) and Undo. Optional second approval:
a second person must approve some decisions.

**Added: assignment.**
- In Review, each pair shows who has it: **Take this pair**, **Give back**, and for a lead (anyone who can approve decisions) **Give to
  <reviewer>**. In the list, tick pairs and give several at once.
- The filter **Everyone's pairs / Given to me / Not given to anyone / Given to others** sits next to Search. A reviewer with pairs given to
  them starts on "Given to me".
- A pair given to someone else shows no decision buttons ("Given to Ravi: only they or a lead decide it"). The server refuses it too, for
  single and bulk decisions.
- Assignments name the two customers by their stable IDs, so they survive the next run. Who gave what to whom is in the activity log.

**Added: no silent overwriting.** Before, if two people opened the same pair and decided differently, the second decision quietly replaced
the first. Now the second person is told "Asha already decided this pair: different (2026-11-24)…" and nothing is saved. Only a lead can
replace another reviewer's decision, after a confirmation; both decisions stay in the history.

Code: `src/dedupe/review/assign.py` (table `review_assignments`), `POST /projects/{p}/review/assign`, `GET /projects/{p}/reviewers`,
`assigned=me|free|others` on the queue (`api/routes_read.py`), `_may_decide` (`api/routes_write.py`), `decided_by_others` / `_guard`
(`review/service.py`), Review screen in `ui/js/review.js`.

## 5. Matching: many records have no PAN or Aadhaar, and many Aadhaar numbers are rejected

**This already works this way; nothing was changed:**
- **An identifier is never required.** Every pair is compared on name (spelling and sound), birth date (also swapped day and month, year
  only, age), phone (any number against any number), email, address (PIN, house number, text), gender and other IDs. A missing value
  counts neither for nor against a match.
- **An invalid Aadhaar is treated as missing, not as a conflict.** Numbers failing the official checksum (Verhoeff) or the 12-digit
  shape, and placeholders, are set aside, so they can neither merge nor separate two records. A masked number (XXXX XXXX 1234) keeps only
  its last 4 digits, and those count only as supporting evidence, never alone. Full Aadhaar numbers are never stored: only a keyed token
  and the last 4 digits. How many were rejected, per source and field, is on the run page under Details, "Source data quality", "Rejected values". A
  high count usually means a column holds something else, or masked numbers: check the mapping.
- **The rules match without identifiers:** the combined rule (question 1) merges similar name + same birth date + a shared phone, email or
  house. The "not enough evidence" rule keeps apart pairs with too little in common.
- **The match score:** with **Balanced** (Rules page, ready-made settings), a score learned from the data decides the pairs no rule
  decides. On synthetic data **without any identifier** it found about 85 of 100 duplicates with about 4 wrong merges in 1,000
  (F1 0.918). **Careful** (rules only) found about 37 in 100.

**Open decision for the team:** new projects still start on Careful. Our recommendation is Balanced. With many records lacking IDs,
choose Balanced on the Rules page.

## 6. Rules: when a pair matches more than one rule, does it count once? Does it change the score or the decision?

**This already works this way:**
- A pair is **decided once**, by the strongest kind of rule that holds: **never merge** first, then **ask a person**, then **merge**,
  then **not enough evidence**. Only if no rule holds does the score decide (when the score is on). So a never-merge rule always wins over
  a merge rule, whatever the order on screen.
- Other rules that also hold **change nothing**: not the decision, not the score, not the count. The match score comes from comparing
  the fields only; rules never add to it.
- The engine records every rule that held for each pair (`fired` in `decisions.parquet`). The decision is credited to one rule (the
  first listed of the winning kind).

**Added on screen:** the customer page's "Why these records were joined" (question 7) has an **Also true** column: the other rules that
held for that pair.

Code: `PRECEDENCE` in `src/dedupe/decision/config.py`, `decide()` in `decision/apply.py`.

## 7. Results: one customer got many records. Which rule or score joined them?

**Before:** the customer page showed the records but not why they were joined (the reason was shown only for pairs in Review).

**Now:** the customer page has **Why these records were joined**. It lists each link between two of the customer's records:
- what joined them: a rule (by name), the match score alone (with the score), or a person's decision (who, and their note);
- the score;
- the other rules that also held.

It opens with a summary such as "3 links by Same PAN and a compatible name; 1 link by the match score". Records joined in a chain (A to B,
B to C) show both links.

The run page already warns when a customer has unusually many records (a shared office phone or a default ID can chain people
together). From the customer page, **Split** separates records that are not the same customer; the next run keeps them apart.

Code: `ReviewService.links` (`review/service.py`), `GET .../runs/{r}/people/{id}/links`, `linksSection` (`ui/js/review.js`).

## 8. Review: does the engine learn from reviewer decisions?

**Already there:** every decision is **kept and applied in every later run**, not only to the pair seen. "Same" keeps those records
together even when their data changes. "Different" keeps them apart, and a person's "different" beats a rule's "merge". A split keeps
the records apart. A corrected value stays in the golden record. The run page lists how many decisions each run applied and whether any
could not be kept.

**Not done on purpose:** the decisions do **not** silently retrain the matching score. They are few, and they are the hard cases the
engine was unsure about. Training on them alone would skew the score for everyone, without anyone approving it.

**Added: suggestions from the decisions.** The Rules page has **What the reviewers' decisions say**. It counts the decisions by the rule or
score band that sent the pairs to people. When people (almost) always give the same answer, it suggests a change. Examples:
- "people said the same for 24 of 25 pairs this rule sent them: it could merge such pairs without asking";
- "of the pairs scored 80% to 90%, people said the same for 30 of 30: merging automatically from 80% would decide them".

A suggestion needs at least 20 decisions and 95 in 100 agreeing. A person then changes the rules, with approval as for every rule change.
That way the engine improves with the reviewers, under human control.

Code: `src/dedupe/review/lessons.py`, `GET /projects/{p}/review/lessons`, `lessonsSection` (`ui/js/rulespage.js`).
