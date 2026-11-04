# Platform revamp: critique and plan (2026-11-04)

Based on a full walk through the portal in a browser (fresh project, two uploaded files, rules, a run, results, review, every menu page,
phone width), plus the findings of the review agents (`~/agent-review`). Screenshots: `~/agent-review/tour/s/`.

## 1. The one root problem

The portal is organised around **what the system stores** (projects, connections, versions, runs, configs, approvals, audit events), not
around **the job the customer came to do**:

> "Bring my customer files, tell it how to match, get one clean record per customer, check the doubtful ones, take the result away."

Because every stored thing has its own page, tab or menu item, the customer has to know the system's model to move through it, the same
thing is reachable from two or three places, and technical detail (version numbers, run ids, stage timings) sits on the main path.
Everything below follows from this.

## 2. Critique, screen by screen

### Navigation (every screen)
- **10 menu items, plus a New menu, plus 4 project tabs, plus sub-tabs, plus 4 run tabs.** Four levels of navigation for a five-step job.
- **The same thing lives in several places:**
  - runs: the Runs menu and the project's Runs tab;
  - review: the Review inbox and each run's Review queue;
  - decisions: the Decisions menu and the project's Results > Decisions;
  - results: the project's Results tab and each run's Results tab;
  - Settings is a page of links to other menu items.
- **Administration and governance are shown with the same weight as daily work.** Users, Roles, Approvals and Audit log take half the menu,
  even for a one-person project.

### Home
- **Four "Get started" tiles stay forever,** even when everything is set up.
- **The counters don't say what to do.** "Connections 2" is not an action.
- **There is a red Delete button on the home page** next to the latest run. Destructive actions are one click from the first screen.
- **Missing: "what needs me now?"** (pairs to review, a run that failed, a change waiting for approval, a project with no data).

### Projects list
- **Fine, but it's a table of facts, not progress.** It doesn't show how far each project is (needs data / ready to run / needs review / done).
- **Delete sits inline on every row.**

### Project > Setup
- **A table of steps works, but it's text-only.** "5 rules say when records are the same, 5 rules ask a person..." is hard to scan.
- **No single "next step" button** once setup is complete. Start run sits top-right as "New run".

### Project > Data
- **The 38-source catalogue (Oracle, SQL Server, MySQL...) fills the top of the page** even after data is added. The data you actually
  have is pushed below the fold.
- **A large field-by-source matrix sits underneath:** useful for an expert, noise for everyone else.
- **Each connection page shows internals:** upload id `U36bda8c77b3b`, a versions table, "Proposed by", a health table with checks.
- **Known dead ends** (review agents):
  - an uploaded file that wasn't mapped can only be deleted, never mapped later;
  - the upload screen has no address, so Back or reload loses the batch;
  - one bad file stops a multi-file upload without naming it.

### Rules (matching, golden record)
- **Already simplified (section 74), but still exposed to versioning:** "In use: version 1", version history, and on two-person projects
  approval cards and reasons.
- **No warning when leaving with unsaved changes.**

### New run
- **Five numbered sections:**
  1. Data;
  2. matching rules version;
  3. golden-record version;
  4. technical settings;
  5. how to load.
  
  Plus a reason box. This is the power-user form shown to everyone.
- **On the first run nothing is ticked,** so "Start run" just shows "Choose at least one connection". The most common action costs two extra
  clicks and an error.
- **"Incremental" and "Full reload"** are engine words.

### Run page
- **Title is a machine id ("Run Ra4c535599caf").**
- **Overview mixes the result with engine internals:**
  - Households;
  - source data quality percentages;
  - "work reused from the previous run";
  - how golden records were chosen;
  - Stages with seconds and peak memory in MB.
- **What the customer wants first is one sentence:** "2,404 records became 2,025 customers; 437 merges; 4 need you". It isn't there.
- **Results tab:**
  - a very wide table of every field with masked values;
  - 3 download buttons, plus a database write;
  - field names are lower-case engine names ("dob", "marital status", "mobile alt").
- **The People tab is an empty box asking for a person ID.** You cannot browse or search people by name there: a dead end.

### Review inbox (the best screen)
- **Good:** a list on the left, a comparison on the right, keyboard keys.
- **But:**
  - the raw rule id is shown ("Rule: review_identifier_with_different_name");
  - "Score 100.0%" is shown on pairs a rule sent for review. Why ask about a 100% pair? It is confusing;
  - field names are lower-case engine names;
  - the filter bar has five controls before any work;
  - counts go stale after deciding.

### Approvals, Audit log, Users, Roles, Settings
- **Approvals only matter when second approval is on,** yet it is a permanent menu item.
- **Settings duplicates the menu** and says "under its Rules tab" (a tab that no longer exists).

### Whole portal
- **Phone width is broken:** every screen is wider than the phone and the top bar overlaps.
- **Messages appear in a corner banner that never hides,** and covers content.
- **Wording is inconsistent:**
  - connection / source / data;
  - version / in use / live;
  - Technical settings / Run settings / standard values;
  - plus raw validation messages.
- **`app.js` is one 3,200-line file:** every change risks breaking an unrelated screen. Not visible to customers, but it slows every step below.

## 3. Principles for the new design

1. **One job, one path.** The project page *is* the journey: Data, Rules, Run, Review, Results. Every step says its state in plain words and
   offers one main button.
2. **Show the next step, always.** Every screen has at most one black (primary) button, and it is the thing to do next.
3. **Each thing lives in one place.** No duplicate menus, tabs or pages; old addresses redirect.
4. **Plain words first, details on request.** Run ids, versions, upload ids, stage timings and engine rule names move into "Details" or
   "History".
5. **Nothing assumed, but nothing asked twice.** Defaults are shown and confirmed in one place ("Run with: 2 sources, rules in use, only
   changes [Start] [Change]"). They are not hidden, and not a five-section form.
6. **Governance only when it's on.** Approvals, reasons and version cards appear only in projects with second approval, or for
   administrators.
7. **Safe by layout.** Delete and other destructive actions live in a "More" menu, never as a red button in a list or on Home.
8. **Works on a phone** (at least reading results and reviewing).

## 4. The new structure

```
Menu (4 items instead of 10)
  Home            what needs me now, my projects with their progress
  Projects        list with a progress bar per project
  Review   (4)    the one place to decide pairs, across projects
  Admin           only for administrators: Users and roles, Audit log, Approvals (when any)

Project page = the journey
  [ Rules check ]                                            [ More v ]
  1 Data  >  2 Rules  >  3 Run  >  4 Review  >  5 Results
  ----------------------------------------------------------------------------
  Next step:  4 pairs need your decision            [ Review 4 pairs ]

  1 Data       2 files, 2,404 records                       Add data
  2 Rules      Same when: PAN, Aadhaar, name+birth+phone... Change
               Golden record: most records agree             Change
  3 Run        Last run 4 Nov 06:40: 2,404 records -> 2,025 customers
                                                             Run again
  4 Review     4 pairs waiting                               Review
  5 Results    2,025 customers                               Download v  Browse customers
```

### What moves where

| Today | New place |
|---|---|
| Runs menu, project Runs tab | Project > Run step, plus "History" (all runs) under More |
| Run Overview stages, memory, reuse, households, source quality | Run > Details (folded) |
| Run Results tab and project Results tab | One Results step: summary sentence, Download (one button with formats), Browse customers |
| Run People tab (ID box) | Browse customers: search by name, phone or ID, list, then the person page |
| Run Review queue tab | Removed: Review step opens the Review inbox filtered to the project |
| Decisions menu and project Results > Decisions | Review > "Decided" tab |
| Approvals menu | Home "Waiting for your approval" when any, plus Admin > Approvals |
| Settings page | Removed (keyboard keys move to a "?" help) |
| New run form | One confirm dialog: "Run with ... [Start] [Change options]"; options (versions, full reload, reason) behind Change |
| Data page catalogue at top | Data step lists your sources; "Add data" opens: big upload area first, "Connect a database or storage" second |
| Connection page internals (upload id, versions, health) | Source page shows columns and mapping; versions and health under Details |
| Field matrix on Data page | Data > "Compare sources" (folded) |

### Words (one glossary, used everywhere)

| Today | New |
|---|---|
| Connection | Data source (a file or a database) |
| Run Ra4c535599caf | Run 3 - 4 Nov, 06:40 |
| Incremental / Full reload | Only what changed (faster) / Everything again |
| Version 1 (in use) | Shown only in History and on approval screens |
| Technical settings / Run settings | Advanced settings |
| People found, Pairs merged automatically | Customers, Merged automatically |
| Rule: review_identifier_with_different_name | Same PAN but the names are different |
| dob, mobile alt, marital status | Date of birth, Second phone, Marital status |
| Golden record | Golden record (with one line: "one clean record per customer") |

## 5. Plan, in phases

Each phase is shippable on its own:
1. Do the work.
2. Run the tests.
3. Walk through it in a browser and show screenshots.
4. Update PROGRESS.
5. Commit.

Old addresses keep working (redirects).

| Phase | What | Size |
|---|---|---|
| **0. Groundwork** | Split `app.js` into modules per area (shell, home, projects, data, rules, run, review, admin) with no change in behaviour; one glossary file for words; a page template (title, one-line purpose, one primary button, More menu) | medium |
| **1. Navigation and Home** | 4-item menu; Home as "what needs me now" + project progress; remove Runs, Decisions, Settings pages (redirect); Delete moves to More menus everywhere | medium |
| **2. Project journey page** | Stepper and "Next step" bar; Setup, Runs, Results and Settings tabs become one page with five steps; project settings and members under More | large |
| **3. Data step** | Source list first, Add data with upload first; upload screen with its own address; "needs mapping" state for uploaded but unmapped files; bad file in a batch named, others continue; source page without internals | large |
| **4. Run and Results** | One-click run with a confirm summary; run title as "Run 3 - date"; Results step: summary sentence, one Download button, Browse customers (search by name, phone, ID); run internals under Details | large |
| **5. Review** | Plain reasons instead of rule ids; no score shown for rule-flagged pairs; field titles; counts update after each decision; Decided tab replaces Decisions menu | medium |
| **6. Governance tucked away** | Versions, approvals and reasons only when second approval is on; unsaved-changes warning on rule forms; decline reason reaches the proposer | medium |
| **7. Polish** | Phone layout; messages that hide by themselves; empty states with one action; glossary pass over every screen; validation messages in plain words | medium |

Order of value: phases 1, 2 and 4 change the feel the most. Phase 0 makes the rest faster and safer, so it goes first.

## 6. Decisions for the owner before starting

1. **The 4-item menu:** remove the Runs, Decisions and Settings pages (old links redirect)?
2. **One-click run with a confirm summary** instead of the five-section form? This keeps "nothing assumed" (everything is shown before
   starting) but stops asking the same questions on every run.
3. **Hide versions, approvals and reasons** when second approval is off?
4. **Rename "connection" to "data source"** and the other words in the glossary?
5. **Start with phase 0** (splitting the code, invisible to you), or go straight to phase 1 so you see a change sooner?
