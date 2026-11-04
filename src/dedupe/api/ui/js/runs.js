"use strict";
// Runs: a new run and one run's page. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- runs --------------------------------------------------------------------------------------------------------------
async function cancelRun(pid, rid) {
  if (!(await confirmDialog("Stop this run?", "The run stops now and keeps no results. Nothing else is changed; you can start it again.", "Stop the run", true))) return;
  stopTimer();
  await api("POST", R(pid, rid) + "/cancel");
  await refreshCache();
  await go(() => viewRun(pid, rid));
  say("The run was stopped.", "good");
}
async function deleteRun(pid, rid) {
  const why = await askReason("Delete run " + rid + "?", "Why delete it? Its results and files are erased for good; decisions made while reviewing it stay. The audit log keeps who deleted it and why", 10, "Delete run", true);
  if (why === null) return;
  await api("POST", R(pid, rid) + "/delete", { reason: why });
  await refreshCache();
  await go(() => viewProject(pid, "runs"));
  say("The run was deleted.", "good");
}
async function startRun(pid, connections, fromRun, reason, key, full, rules) {
  const r = await api("POST", P(pid) + "/runs", { connections, from_run: fromRun || null, reason: reason || "", full_rerun: !!full, rules: rules || null }, { "Idempotency-Key": key || uuid() });
  await go(() => viewRun(pid, r.id));
  say(r.replayed ? "This run was already started." : "Run started. This page updates by itself until it finishes.", "good");
}
// One click: run with every data source in use and the rules in use, only what changed. All of it is shown before it starts; "Change options"
// opens the full form (choose sources, rule versions, everything again, a reason).
async function quickRun(pid) {
  const conns = can("config.read") ? await api("GET", P(pid) + "/connections") : [];
  const st = await setupState(pid, conns);
  if (!can("config.read") || !st.ready) { await go(() => newRun(pid)); return; }
  const full = el("input", { type: "checkbox" });
  const key = uuid();
  const ver = (n) => "the ones in use (version " + st.cfg[n].live.version + ")";
  const body = el("div", {}, kv([["Data", st.data.map((c) => c.id).join(", ")], ["Matching rules", ver("decision")], ["Golden record", ver("survivorship")],
    ["Technical settings", st.cfg.settings.live ? ver("settings") : "standard values"]]),
  el("label", { class: "check" }, full, el("span", {}, "Process everything again", el("span", { class: "muted" },
    "Slower. Otherwise only what changed since the last run is processed again; customers are still formed from all records."))));
  const choice = await dialog("Start a run of " + projectName(pid) + "?", body, "Start run", async () => "start", false, ["Change options", "options"]);
  if (choice === "options") await go(() => newRun(pid));
  else if (choice === "start") await startRun(pid, st.data.map((c) => c.id), null, "", key, full.checked, null);
}
// Versions a run may use: the one in use, approved ones, earlier ones, and drafts while no second approval is required.
const VERSION_NOTE = { active: "in use", approved: "approved, not in use", proposed: "draft, not in use", superseded: "used before" };
// New run: one screen with every choice visible. Nothing is picked for you except the rule versions the project has in use.
async function newRun(pid) {
  await refreshCache();
  here(pid ? P(pid) + "/new-run" : "/new-run");
  if (!pid && cache.projects.length === 1) pid = cache.projects[0].id;
  const trail = pid ? crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), "New run") : crumbs(crumb("Projects", "/projects"), "New run");
  const projSel = el("select", {}, pid ? null : el("option", { value: "" }, "Choose a project"), cache.projects.map((p) => el("option", { value: p.id, selected: p.id === pid }, p.name)));
  projSel.addEventListener("change", () => { if (projSel.value) go(() => newRun(projSel.value)); });
  const top = page("New run", "Choose what this run reads, which rules it uses and whether it processes everything again. Each choice is recorded with the run.", [], trail);
  if (!cache.projects.length) { show(top, empty("Create a project first.", can("projects.create") ? btn("New project", () => go(newProject), "primary", "plus") : null)); return; }
  if (!pid) { show(top, el("div", { class: "narrow" }, field("Project", projSel))); return; }
  const p = cache.projects.find((x) => x.id === pid) || await api("GET", P(pid));
  const all = can("config.read") ? await api("GET", P(pid) + "/connections") : [];
  const st = await setupState(pid, all);
  const err = errBox();

  // 1 data: every connection, the ones not in use shown but not choosable
  const chosen = new Set();
  const boxes = all.map((c) => {
    const cb = el("input", { type: "checkbox", disabled: c.live_version ? null : true, checked: c.live_version ? true : null, "aria-label": "Read " + c.id });
    if (c.live_version) chosen.add(c.id);                    // every data source in use is read unless unticked
    cb.addEventListener("change", () => { if (cb.checked) chosen.add(c.id); else chosen.delete(c.id); });
    return [c, cb];
  });
  const usable = boxes.filter(([c]) => c.live_version);
  const pickAll = usable.length > 1 ? btn("Choose all in use", () => { for (const [c, cb] of usable) { cb.checked = true; chosen.add(c.id); } }, "small") : null;
  const dataPart = all.length ? table(["", "Data source", "Kind", "Version in use", ["Records last read"]], boxes.map(([c, cb]) => el("tr", {}, el("td", {}, cb), el("td", {}, el("strong", {}, c.id)),
    el("td", {}, typeTitle(c.type)), el("td", {}, c.live_version ? "Version " + c.live_version : tag(c.latest_status === "proposed" || c.latest_status === "approved" ? "Waiting for approval" : "Not in use", "warn")),
    el("td", { class: "num" }, c.last_check && c.last_check.rows != null ? num(c.last_check.rows) : ""))))
    : empty("This project has no data yet.", btn("Add data", () => go(() => viewProject(pid, "setup", "data")), "primary", "database"));

  // 2-4 rules: a version of each, with what it does in words
  const allowed = (v) => ["active", "approved"].includes(v.status) || (v.status === "superseded" && (v.reviewed || !p.second_approval)) ||
    (v.status === "proposed" && !p.second_approval);
  const contents = {};
  const words = { decision: decisionWords, survivorship: goldenWords, settings: () => "" };
  const pickers = {};
  const rulePart = (n, part, required) => {
    const vs = st.cfg[n].versions.filter(allowed).reverse();
    if (!vs.length) {
      const what = part === "matching" ? "matching rules" : "golden-record rules";
      return required ? el("div", { class: "notice warn", role: "note" }, "Not chosen yet: this project has no " + what + " in use, so a run cannot start.",
        el("div", { class: "row" }, btn("Choose the " + what, () => go(() => viewProject(pid, "setup", part)), "primary"))) : el("p", {}, "Standard values.");
    }
    const sel = el("select", { "aria-label": SETUP_PARTS[part] }, vs.map((v) => el("option", { value: v.version, selected: v.status === "active" ? true : null },
      "Version " + v.version + " (" + VERSION_NOTE[v.status] + ")" + (v.reason ? ": " + v.reason.slice(0, 70) : ""))));
    if (!st.cfg[n].live && !required) sel.prepend(el("option", { value: "", selected: true }, "Standard values"));
    const about = el("p", { class: "muted" });
    const note = el("div", { class: "notice warn", role: "note", hidden: true }, "This version is not the one in use. The run's results still become the project's latest results.");
    const show1 = () => run(async () => {
      const v = sel.value;
      note.hidden = !v || (st.cfg[n].live && String(st.cfg[n].live.version) === v);
      if (!v) { about.textContent = ""; return; }
      contents[n + v] = contents[n + v] || (await api("GET", P(pid) + "/config/" + n + "/versions/" + v)).content;
      about.textContent = words[n](contents[n + v]);
    }, true);
    sel.addEventListener("change", show1);
    show1();
    pickers[n] = sel;
    return el("div", {}, el("div", { class: "narrow" }, sel), about, note);
  };
  const load = choiceCards("load", LOAD_MODES, "incremental");
  const reason = el("textarea", { class: "plain", maxlength: "300" });
  const key = uuid();                              // one key for this form: a second press replays the first start instead of starting twice
  const start = () => {
    fail(err, "");
    if (!chosen.size) { fail(err, "Tick at least one data source to read."); return; }
    if (can("config.read") && (!pickers.decision || !pickers.survivorship)) { fail(err, "Choose the matching rules and the golden-record rules first (the Rules step of the project)."); return; }
    const rules = Object.fromEntries(Object.entries(pickers).filter(([, s]) => s.value).map(([n, s]) => [n, Number(s.value)]));
    return startRun(pid, [...chosen], null, reason.value, key, load.value() === "full", can("config.read") ? rules : null);
  };
  const rulesParts = () => [section(2, "Matching rules", rulePart("decision", "matching", true)), section(3, "Golden record", rulePart("survivorship", "golden", true)),
    section(4, "Technical settings", rulePart("settings", "technical", false))];
  const section = (n, title, body, extra) => el("section", { class: "run-part" }, el("div", { class: "title-row" }, el("h2", { class: "section" }, n + ". " + title), extra || null), body);
  show(top, cache.projects.length > 1 ? el("div", { class: "narrow" }, field("Project", projSel)) : null,
    section(1, "Data", dataPart, pickAll),
    can("config.read") ? (p.second_approval ? rulesParts() : el("details", { class: "advanced", open: !st.ready ? true : null },
      el("summary", {}, "Rules: the ones in use (choose an earlier version)"), rulesParts())) : el("p", { class: "hint" }, "The run uses the rule versions the project has in use."),
    section(can("config.read") ? 5 : 2, "What to process", load.node),
    el("div", { class: "narrow" }, field("Reason (optional, recorded with the run)", reason)), err,
    el("div", { class: "row" }, btn("Cancel", () => go(() => viewProject(pid))), can("runs.start") ? btn("Start run", start, "primary", "play") : null));
}
// What a run reads and uses, in one line under its title: data versions, rule versions, load.
function runUsed(r) {
  const cfg = r.configs || {};
  const chosen = (r.options || {}).rules || {};
  const done = r.status === "succeeded";
  const ver = (n, none) => (cfg[n] ? (cfg[n].source === "store" ? "version " + cfg[n].version : none) : chosen[n] ? "version " + chosen[n] : done ? none : "the version in use when it starts");
  const part = (k, v) => el("span", {}, el("strong", {}, k + ": "), v);
  const parts = [part("Data", r.connections.length ? r.connections.map((c) => c.id + " version " + c.version).join(", ") : "imported"),
    part("Matching rules", ver("decision", "standard (built in)")), part("Golden record", ver("survivorship", "standard (built in)")),
    part("Technical settings", ver("settings", "standard values")), part("Load", ((r.options || {}).full ? LOAD_WORDS.full : LOAD_WORDS.incremental).toLowerCase())];
  return el("p", { class: "run-used" }, parts.flatMap((x, i) => (i ? [" · ", x] : [x])));
}
// One run. Finished: Overview (what came out, in one sentence; the engine's details folded away), Results (the files) and Customers (find a
// customer by name, id, phone or email). The review queue is the Review screen, opened at this run.
async function viewRun(pid, rid, tab) {
  stopTimer();                                                 // never two polls: a leftover one would put this run back on screen after the person has left
  if (tab === "review") { await viewInbox(pid, "open", rid); return; }          // older links
  if (tab === "people") tab = "customers";
  const [r, runs] = await Promise.all([api("GET", R(pid, rid)), can("runs.read") ? api("GET", P(pid) + "/runs?limit=1000") : [], refreshCache()]);
  const going = r.status === "running" || r.status === "queued";
  const okRun = r.status === "succeeded";
  const tabList = okRun ? [["overview", "Overview"], ...(can("results.export") ? [["results", "Results"]] : []), ...(can("person.read") ? [["customers", "Customers"]] : [])] : [];
  if (!tabList.some(([k]) => k === tab)) tab = "overview";
  here(R(pid, rid) + (tab === "overview" ? "" : "/" + tab));
  const title = runTitle(r, runs);
  const again = can("runs.start") && !going && r.connections.length;
  const actions = [];
  if (okRun && can("results.export") && tab !== "results") actions.push(btn("Get the results", () => go(() => viewRun(pid, rid, "results")), "primary", "download"));
  if (going) actions.push(btn("Refresh", () => go(() => viewRun(pid, rid, tab)), "", "refresh"));
  if (going && can("runs.start")) actions.push(btn("Stop run", () => cancelRun(pid, rid), "danger"));
  if (again) actions.push(btn("Run again", () => run(() => quickRun(pid)), okRun ? "" : "primary", "play"));
  if (!going) actions.push(moreMenu([can("data.delete") ? ["Delete run", () => deleteRun(pid, rid), true] : null], title));
  const top = page(title, "Started by " + r.requested_by + (r.finished_at ? ", finished " + when(r.finished_at) + "." : "."), actions,
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("All runs", P(pid) + "/runs"), title), runState(r));
  const used = runUsed(r);
  if (going) {
    timer = setInterval(() => run(async () => {
      if (!readAddress().startsWith(R(pid, rid))) { stopTimer(); return; }      // the person went elsewhere
      const now = await api("GET", R(pid, rid));
      if (now.status !== "running" && now.status !== "queued") { stopTimer(); await viewRun(pid, rid); say("The run finished: " + (STATUS_LABEL[now.status] || now.status) + ".", now.status === "succeeded" ? "good" : "bad"); }
    }, true), 4000);
    show(top, used, empty(r.status === "queued" ? "This run is waiting its turn" + (r.queue_position > 1 ? " (" + (r.queue_position - 1) + " ahead of it)" : " and starts next") + ". It starts by itself; you can stop it above."
      : "This run is in progress. Its results appear here when it finishes (this page checks every few seconds); earlier runs stay readable meanwhile."));
    return;
  }
  const restart = again ? " Start it again with Run again above." : " Someone who can start runs can start it again.";
  if (r.status === "cancelled") { show(top, used, empty("This run was stopped before it finished, so it has no results." + restart)); return; }
  if (!okRun) {
    show(top, used, empty(r.status === "interrupted" ? "This run was interrupted when the server restarted, so it has no results." + restart
      : "This run did not finish successfully (exit code " + r.exit_code + "), so it has no results. The end of its log is below." + restart), await runLogPanel(pid, rid));
    return;
  }
  const stale = r.latest_run_id && r.latest_run_id !== rid ? el("div", { class: "notice warn", role: "note" },
    el("strong", {}, "A newer run has finished. "), "This run's customers and results are out of date. Decisions made from it still count, but review in the newest run.",
    el("div", { class: "row" }, btn("Open the newest run", () => go(() => viewRun(pid, r.latest_run_id, tab)), "small primary"))) : null;
  let body;
  if (tab === "overview") body = el("div", {}, runOverview(r, pid), el("details", { class: "advanced" }, el("summary", {}, "Details: what it read, data quality, timings"),
    el("p", { class: "muted" }, "Run id " + rid + "."), used, runDetails(r)), runLogDetails(pid, rid));
  else if (tab === "results") body = await runResults(pid, rid);
  else body = await customersPanel(pid, rid);
  show(top, stale, tabs(tabList, tab, (t) => go(() => viewRun(pid, rid, t))), tabPanel(tab, body));
}
// The results of one finished run, right on the run page: each file with one Download menu (formats, or into a database).
async function runResults(pid, rid) {
  if (!can("results.export")) {
    return empty("Your role cannot take results out of the portal. You can still look customers up under Customers. Ask an administrator for the results.export permission if you need files.");
  }
  const p = await api("GET", P(pid));
  const refresh = () => go(() => viewRun(pid, rid, "results"));
  return el("div", {}, el("p", { class: "hint" }, "Results contain personal data. Every download or database write asks for a reason and is recorded in the project's activity."),
    ...(await datasetCards(p, rid)), el("details", { class: "advanced" }, el("summary", {}, "Database destinations"), await destinationsPanel(p, refresh)));
}
// Customers: find one by name, person id, a whole mobile number or email; open them to see their records, split a wrong merge or correct a value.
const CUSTOMER_COLUMNS = ["person_id", "full_name", "dob", "mobile", "email", "city", "record_count", "sources"];
async function customersPanel(pid, rid, st) {
  st = st || { offset: 0, q: "" };
  const size = 25;
  const r = await api("GET", R(pid, rid) + "/datasets/golden_records/rows?limit=" + size + "&offset=" + st.offset + "&q=" + encodeURIComponent(st.q));
  const wrap = el("div");
  const again = (patch) => run(async () => wrap.replaceWith(await customersPanel(pid, rid, { ...st, ...patch })));
  const q = el("input", { type: "search", value: st.q, maxlength: "60", placeholder: "Name, person ID, whole mobile number or email", class: "wide-search" });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") again({ q: q.value.trim(), offset: 0 }); });
  const cols = CUSTOMER_COLUMNS.map((c) => [c, r.columns.indexOf(c)]).filter(([, i]) => i >= 0);
  const TITLES = { person_id: "Person ID", record_count: "Records", sources: "Sources" };
  const cell = (c, v) => (v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v));
  const last = Math.min(st.offset + size, r.total);
  add(wrap, el("div", { class: "row", role: "search" }, q, btn("Search", () => again({ q: q.value.trim(), offset: 0 }), "", "search"),
    st.q ? btn("Clear", () => again({ q: "", offset: 0 }), "ghost") : null),
  r.masked.length ? el("p", { class: "hint" }, "Phone numbers, emails and identifiers are masked here; open a customer to see more. A search by mobile or email needs the whole value.") : null,
  r.rows.length ? table(cols.map(([c]) => (c === "record_count" ? [TITLES[c]] : TITLES[c] || fieldTitle(c))), r.rows.map((row) => {
    const id = row[r.columns.indexOf("person_id")];
    return linkRow(id, () => viewPerson(pid, rid, id), cols.slice(1).map(([c, i]) => el("td", { class: c === "record_count" ? "num" : null }, cell(c, row[i]))));
  })) : empty(st.q ? "No customer matches " + st.q + "." : "No customers."),
  r.total ? el("div", { class: "pager" }, el("span", { class: "muted" }, num(st.offset + 1) + " to " + num(last) + " of " + plural(r.total, "customer", "customers")),
    st.offset > 0 ? btn("Previous", () => again({ offset: Math.max(0, st.offset - size) }), "small", "prev") : null,
    last < r.total ? btn("Next", () => again({ offset: st.offset + size }), "small", "next") : null) : null);
  return wrap;
}
// The log of a finished run, folded away at the end of its overview and read only when opened.
function runLogDetails(pid, rid) {
  const box = el("div");
  const d = el("details", { class: "advanced" }, el("summary", {}, "Run log"), box);
  d.addEventListener("toggle", () => { if (d.open && !box.childElementCount) run(async () => fill(box, await runLogPanel(pid, rid)), true); });
  return d;
}
async function runLogPanel(pid, rid) {
  const log = await api("GET", R(pid, rid) + "/log?lines=200");
  return el("div", {}, el("h2", { class: "section" }, "Run log"),
    log.available ? el("p", { class: "hint" }, "The end of the log" + (log.truncated ? " (older lines are cut)" : "") + ". Anything that looks like personal data is removed.") : null,
    log.available ? el("pre", { class: "log", tabindex: "0" }, log.lines.join("\n")) : empty("No log was kept for this run."));
}
// How the match scores of a run were learned. ID-backed scores need no remark; the others say plainly how far to trust them.
function scoringNotice(s) {
  const sc = s && s.scoring;
  if (!sc || sc.mode === "id_anchored") return null;
  if (sc.mode === "unsupervised_em") {
    return el("div", { class: "notice", role: "note" }, el("strong", {}, "Scores learned from the data itself. "),
      "This data has too few identifiers (PAN, Aadhaar, document numbers) to learn from, so the scoring found its own pattern. " +
      "Scores are usually sensible but less certain than ID-backed ones: look over a sample of automatic merges before relying on them.");
  }
  return el("div", { class: "notice bad", role: "note" }, el("strong", {}, "Scores are not meaningful for this run. "),
    "There are too few identifiers and too little data to learn how much each field matters, so every pair sits near 50% and goes to review. " +
    "Add records or identifiers, or decide these pairs by hand.");
}
// What the cleaning noticed in awkward data (standardise/oddities.py), in words, with what it did about it.
const ODDITY = {
  multi_phone: ["Two or more phone numbers in one cell", "The first became the main number, the next the alternate"],
  multi_email: ["Two or more emails in one cell", "The first real one was used"],
  phone_digits_lost: ["Phone stored as a rounded number (like 9.87654E+09)", "Not used: the missing digits cannot be recovered. Store phones as text in the source"],
  placeholder_name: ["Placeholder name (Unknown, Test, NA...)", "Treated as no name; never matched on"],
  placeholder_email: ["Placeholder email (test@, none@...)", "Treated as no email; never matched on"],
  joint_name: ["Two people in one name (Rahul & Anita Sharma)", "The first person was kept"],
  relation_in_name: ["Relation inside the name (Rahul S/O Ramesh)", "Name and father's name separated"],
  organisation: ["Looks like a company, not a person", "Kept in the results as its own record, never matched with a person"],
  header_row: ["Header line repeated inside the data", "Kept out of matching"],
};
// What came out, in one sentence and four numbers, then what a person should know (decisions applied); the engine's details are in runDetails.
function runOverview(r, pid) {
  const s = r.summary;
  const j = s.judgments;
  const sentence = num(s.records) + " records became " + plural(s.people, "customer", "customers") + ": " + plural(s.auto_merged_pairs, "pair was", "pairs were") +
    " merged automatically, and " + plural(s.review_items, "pair waits", "pairs wait") + " for a person.";
  const parts = [el("p", { class: "lead" }, sentence), scoringNotice(s), el("div", { class: "stats" }, stat("Records read", num(s.records)), stat("Customers", num(s.people)),
    stat("Merged automatically", num(s.auto_merged_pairs)), stat("Sent to a person", num(s.review_items), can("queue.read") ? link("/review/" + pid) : null))];
  if (j && j.judgments_applied_to_run) {
    parts.push(el("h2", { class: "section" }, "Decisions by people this run applied"),
      kv([["Decisions applied", num(j.judgments_applied_to_run)], ...Object.entries(j.by_kind || {}).map(([k, n]) => ["  " + (KIND_LABEL[k] || label(k)), num(n)]),
        ["Kept in the result", num(j.held)], ["Not kept", j.not_held ? el("span", { class: "bad" }, num(j.not_held) + " (a later decision or an identifier rule overruled them)") : "0"]]));
  }
  return el("div", {}, parts);
}
function runDetails(r) {
  const s = r.summary;
  const parts = [kv([["Households", num(s.households)], ["Time taken", s.seconds == null ? "" : s.seconds.toFixed(1) + " seconds"]])];
  const inc = s.incremental;
  if (inc) {
    const rc = inc.records || {};
    parts.push(el("h2", { class: "section" }, "Work reused from the previous run"), inc.used
      ? kv([["Previous run", inc.previous_run], ["Records unchanged", num(rc.unchanged)], ["New", num(rc.new)], ["Changed", num(rc.changed)], ["Removed", num(rc.removed)],
        ["Pairs whose scores were reused", num(inc.pairs_reused)], ["Pairs scored now", num(inc.pairs_scored)],
        ["Scoring model", inc.model === "reused" ? "Reused from the previous run" : "Learned again" + (inc.model_reason ? ": " + inc.model_reason : "")]])
      : el("p", { class: "hint" }, "Nothing reused: every record was processed" + (inc.reason ? " (" + inc.reason + ")" : "") + "."));
  }
  const q = s.source_quality;
  if (q && Object.keys(q).length) {
    const cols = [["name", "Name"], ["birth_date", "Birth date"], ["birth_year_or_age", "Birth year or age"], ["phone", "Phone"], ["email", "Email"],
      ["address_pin", "PIN code"], ["identifier", "An ID"], ["record_date", "Record date"]];
    const pct = (n, d) => (d ? Math.round((100 * n) / d) + "%" : "");
    parts.push(el("h2", { class: "section" }, "Source data quality"),
      el("p", { class: "hint" }, "Share of each source's records that carry a usable value after cleaning. Sources with little to match on find fewer duplicates; " +
        "rejected values failed a check (a phone with too few digits, a PAN of the wrong shape) and were not used."),
      table(["Source", ["Records"], ...cols.map(([, t]) => [t]), "Rejected values"], Object.entries(q).map(([src, x]) => el("tr", {}, el("td", { class: "mono" }, src),
        el("td", { class: "num" }, num(x.records)), cols.map(([k]) => el("td", { class: "num" }, pct(x.filled[k] || 0, x.records))),
        el("td", {}, Object.entries(x.invalid || {}).map(([f, n]) => fieldTitle(f) + " " + num(n)).join(", ") || "None")))));
    const oddRows = Object.entries(q).flatMap(([src, x]) => Object.entries(x.oddities || {}).map(([k, n]) => el("tr", {}, el("td", { class: "mono" }, src),
      el("td", {}, (ODDITY[k] || [label(k), ""])[0]), el("td", { class: "num" }, num(n)), el("td", {}, (ODDITY[k] || ["", ""])[1]))));
    for (const [src, n] of Object.entries(s.duplicate_record_ids || {})) {
      oddRows.push(el("tr", {}, el("td", { class: "mono" }, src), el("td", {}, "The same record id used more than once"), el("td", { class: "num" }, num(n)),
        el("td", {}, "Later copies were renamed <id>#2, #3... so none is lost. Fix the id column in the source")));
    }
    if (oddRows.length) {
      parts.push(el("h2", { class: "section" }, "Unusual data found and how it was handled"),
        table(["Source", "What", ["Records"], "What was done"], oddRows));
    }
  }
  const g = s.golden;
  if (g && g.trust_order) {
    parts.push(el("h2", { class: "section" }, "How the golden records were chosen"),
      kv([["Source trust order", g.trust_order.join(" > ")],
        g.auto_ranked_sources && g.auto_ranked_sources.length ? ["Ranked automatically", g.auto_ranked_sources.join(", ") + " (by how complete their records are; set the order in Rules, Golden record)"] : null,
        g.sources_without_dates && g.sources_without_dates.length ? ["No record date", el("span", { class: "bad" }, g.sources_without_dates.join(", ") + ": 'newest record' could not use them")] : null,
        ["Fields where sources disagreed", num(g.values_with_source_conflict)],
        g.shared_values_passed_over ? ["Shared phones or emails passed over", num(g.shared_values_passed_over)] : null]));
  }
  const rowsOf = (id) => (s.records_by_source || {})[id];
  const srcRows = r.connections.length ? r.connections.map((c) => el("tr", {}, el("td", {}, c.id), el("td", {}, "Version " + c.version), el("td", { class: "num" }, num(rowsOf(c.id)))))
    : (s.sources || []).map((x) => el("tr", {}, el("td", {}, x), el("td", { class: "muted" }, "imported run"), el("td", { class: "num" }, num(rowsOf(x)))));
  parts.push(el("h2", { class: "section" }, "Sources read"), table(["Data source", "Version", ["Records read"]], srcRows));
  if (r.stages) {
    parts.push(el("h2", { class: "section" }, "Stages"), table(["Stage", ["Seconds"], ["Peak memory (MB)"]], r.stages.map((st) => el("tr", {}, el("td", {}, label(st.stage)),
      el("td", { class: "num" }, st.seconds), el("td", { class: "num" }, num(st.peak_memory_mb))))));
  }
  return el("div", {}, parts);
}
