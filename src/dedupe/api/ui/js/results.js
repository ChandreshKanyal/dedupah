"use strict";
// Results of a run, and schedules. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- results: take a run's output out as a file or into a database ------------------------------------------------------------
async function resultsPanel(p, runs) {
  const done = runs.filter((r) => r.status === "succeeded");
  const refresh = () => go(() => viewProject(p.id, "results"));
  if (!done.length) {
    return el("div", {}, empty("Results appear here after a run has finished. Start a run first.", can("runs.start") ? btn("Start run", () => quickRun(p.id), "primary", "play") : null),
      await destinationsPanel(p, refresh));
  }
  const pick = el("select", {}, done.map((r) => el("option", { value: r.id }, runTitle(r, runs) + " (" + plural(r.summary.people, "customer", "customers") + ")")));
  const box = el("div");
  const load = () => run(async () => box.replaceChildren(...(await datasetCards(p, pick.value))));
  pick.addEventListener("change", load);
  const wrap = el("div", {}, el("p", { class: "hint" }, "Results contain personal data. Every download or database write asks for a reason and is recorded in the project's activity."),
    done.length > 1 ? el("div", { class: "row" }, field("Results of (newest first)", pick)) : null, box,
    el("details", { class: "advanced" }, el("summary", {}, "Database destinations"), await destinationsPanel(p, refresh)));
  await load();
  return wrap;
}
// The files of a run: the customers (one clean record each) first, the other two folded; each with one Download menu (a format, or a database).
async function datasetCards(p, rid) {
  const info = await api("GET", R(p.id, rid) + "/datasets");
  const menu = (d) => moreMenu([...["xlsx", "csv", "parquet"].map((f) => [info.formats[f], () => downloadResults(p.id, rid, d.name, f)]),
    ["Write to a database", () => databaseDialog(p, rid, d)]], d.title, "Download", "download");
  const card = (d, title, preview) => {
    const box = el("div");
    return el("div", { class: "panel" }, el("div", { class: "card-head body" }, el("div", {}, el("h3", {}, title + " (" + num(d.rows) + " rows)"), el("p", { class: "muted" }, d.description)),
      el("div", { class: "row tight" }, preview ? btn("Show a preview", () => run(async () => box.replaceChildren(await previewTable(p.id, rid, d))), "small", "list") : null, menu(d))), box);
  };
  const golden = info.datasets.find((d) => d.name === "golden_records");
  const others = info.datasets.filter((d) => d !== golden);
  return [golden ? card(golden, "Customers, one clean record each", false) : null,
    others.length ? el("details", { class: "advanced" }, el("summary", {}, "Other files: which record belongs to which customer, households"), others.map((d) => card(d, d.title, true))) : null]
    .filter(Boolean);
}
// A page of a result table on screen. Identifiers, contact details and extra columns stay masked until someone allowed to see them asks (logged).
async function previewTable(pid, rid, d, st) {
  st = st || { offset: 0, q: "", reason: null };
  const size = 25;
  const url = R(pid, rid) + "/datasets/" + d.name + "/rows?limit=" + size + "&offset=" + st.offset + "&q=" + encodeURIComponent(st.q) +
    (st.reason ? "&" + unmaskQuery(st.reason) : "");
  const r = await api("GET", url);
  const wrap = el("div", { class: "preview" });
  const again = (patch) => run(async () => wrap.replaceWith(await previewTable(pid, rid, d, { ...st, ...patch })));
  const q = el("input", { type: "search", value: st.q, maxlength: "60", placeholder: d.name === "golden_records" ? "Name or person id" : "Person or record id" });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") again({ q: q.value.trim(), offset: 0 }); });
  const reveal = can("pii.unmask") && r.masked.length ? btn("Show identifiers", async () => {
    const why = await revealIdentifiers("Show identifiers");
    if (why !== null) again({ reason: why });
  }, "small", "shield") : null;
  const last = Math.min(st.offset + size, r.total);
  add(wrap, el("div", { class: "row" }, field("Find", q), btn("Search", () => again({ q: q.value.trim(), offset: 0 }), "small", "search"), reveal),
    r.masked.length ? el("p", { class: "hint" }, "Masked: " + r.masked.map(label).join(", ") + ". " + (can("pii.unmask") ? "Press Show identifiers to see them (every view is logged)." : "Your role cannot unmask them."))
      : r.unmasked ? el("p", { class: "hint" }, "Showing actual values. This view was logged.") : null,
    r.rows.length ? wide(r.columns.map(label), r.rows.map((row) => el("tr", {}, row.map((v) => el("td", {}, v == null ? "" : String(v)))))) : empty(st.q ? "Nothing matches." : "No rows."),
    r.total ? el("div", { class: "pager" }, el("span", { class: "muted" }, "Rows " + num(st.offset + 1) + " to " + num(last) + " of " + num(r.total)),
      st.offset > 0 ? btn("Previous", () => again({ offset: Math.max(0, st.offset - size) }), "small", "prev") : null,
      last < r.total ? btn("Next", () => again({ offset: st.offset + size }), "small", "next") : null) : null);
  return wrap;
}
async function downloadResults(pid, rid, dataset, format) {
  const why = await askReason("Download these results", "Why do you need this file? It is recorded", 10, "Download");
  if (why === null) return;
  const r = await fetch(R(pid, rid) + "/export", { method: "POST", credentials: "omit", cache: "no-store",
    headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" }, body: JSON.stringify({ dataset, format, reason: why, safe_for_spreadsheets: true }) });
  if (!r.ok) {
    let msg = "The download failed.";
    try { msg = ((await r.json()).error || {}).message || msg; } catch (_) { /* no body */ }
    throw new Error(msg);
  }
  const name = ((r.headers.get("content-disposition") || "").match(/filename="?([^";]+)"?/) || [])[1] || (dataset + "." + format);
  const url = URL.createObjectURL(await r.blob());
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  say("Downloaded " + name + ".", "good");
}
async function databaseDialog(p, rid, dataset) {
  const dests = (await api("GET", P(p.id) + "/destinations")).filter((d) => d.live_version);
  if (!dests.length) { say("This project has no database destination in use yet. Add one under Database destinations on this page.", "bad"); return; }
  const dest = el("select", {}, dests.map((d) => el("option", { value: d.id }, d.id + (d.schema_name ? " (schema " + d.schema_name + ")" : ""))));
  const tableName = el("input", { type: "text", maxlength: "63", placeholder: "for example dedupe_" + dataset.name });
  const mode = el("select", {}, [["create", "Create a new table"], ["append", "Add rows to an existing table"], ["replace", "Replace the rows of an existing table"]]
    .map(([v, t]) => el("option", { value: v }, t)));
  const schema = el("input", { type: "text", maxlength: "63", placeholder: "leave empty for the destination's default" });
  const confirm = el("input", { type: "checkbox" });
  const confirmRow = el("label", { class: "check", hidden: true }, confirm, el("span", {}, "I understand this deletes the rows the table has now"));
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  mode.addEventListener("change", () => { confirmRow.hidden = mode.value !== "replace"; });
  const result = await dialog("Write " + dataset.title.toLowerCase() + " to a database", el("div", {}, el("p", { class: "hint" }, num(dataset.rows) + " rows. All of it is written, or none: a refused row leaves the table as it was."),
    field("Destination", dest), field("Table name", tableName), field("What to do", mode), confirmRow, field("Schema (optional)", schema), field("Reason (at least 10 characters; it is recorded)", why), err), "Write", async () => {
    try {
      return await api("POST", R(p.id, rid) + "/export/database", { destination: dest.value, dataset: dataset.name, table: tableName.value.trim(), mode: mode.value,
        schema_name: schema.value.trim() || null, confirm_replace: confirm.checked, reason: why.value.trim() });
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (result) say("Wrote " + num(result.rows) + " rows to " + (result.schema ? result.schema + "." : "") + result.table + ".", "good");
}
async function destinationsPanel(p, refresh) {
  const list = await api("GET", P(p.id) + "/destinations");
  const second = p.second_approval;
  const details = await Promise.all(list.map((d) => (d.latest_status === "proposed" || d.latest_status === "approved" ? api("GET", P(p.id) + "/destinations/" + d.id) : null)));
  const act = async (d, v, what, title, minLength, prompt, danger) => {
    const reason = await askReason(title, prompt, minLength, title.split(" ")[0], danger);
    if (reason === null) return;
    await api("POST", P(p.id) + "/destinations/" + d.id + "/versions/" + v.version + "/" + what, { reason });
    await refresh();
    say(title + ": done.", "good");
  };
  const rows = list.map((d, i) => {
    const v = details[i] ? details[i].versions[details[i].versions.length - 1] : null;
    const mine = v && v.proposed_by === me.sub;
    const buttons = v ? [
      second && can("config.approve") && v.status === "proposed" && !mine ? btn("Approve", () => act(d, v, "approve", "Approve version " + v.version, 0, "Note"), "small") : null,
      can("config.approve") ? btn("Decline", () => act(d, v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger") : null,
      can("config.activate") && (!second || v.approved_by.length) ? btn("Put in use", () => act(d, v, "activate", "Put version " + v.version + " in use", 0, "Note"), "small primary") : null,
      second && mine && v.status === "proposed" ? tag("Yours: someone else approves it", "warn") : null,
    ].filter(Boolean) : [];
    return el("tr", {}, el("td", {}, d.id), el("td", { class: "mono" }, d.url_env), el("td", {}, d.schema_name || ""), el("td", {}, d.live_version ? "Version " + d.live_version : el("span", { class: "muted" }, "Not in use")),
      el("td", {}, d.latest_status === "active" ? "" : statusTag(d.latest_status)), el("td", { class: "actions-cell" }, buttons,
        can("sources.manage") ? btn("Test", async () => {
          const t = await api("POST", P(p.id) + "/destinations/" + d.id + "/test");
          say(t.ok ? "The database " + d.id + " can be reached." : "The database " + d.id + " could not be reached: " + t.error, t.ok ? "good" : "bad");
        }, "small") : null));
  });
  return el("div", {}, el("div", { class: "title-row" }, el("h2", { class: "section" }, "Database destinations"),
    can("sources.manage") ? btn("Add a destination", () => destinationDialog(p, refresh), "", "plus") : null),
  el("p", { class: "hint" }, "A destination is the name of an environment variable (starting with DEDUPE_DST_) that holds a database address. The address and password are set on the server by whoever runs it, never typed here."),
  rows.length ? table(["Destination", "Environment variable", "Schema", "In use", "Waiting", ""], rows) : empty("No destinations yet. Add one to write results into a database."));
}
async function destinationDialog(p, refresh) {
  const id = el("input", { type: "text", maxlength: "40", placeholder: "for example warehouse" });
  const env = el("input", { type: "text", maxlength: "70", placeholder: "DEDUPE_DST_WAREHOUSE" });
  const schema = el("input", { type: "text", maxlength: "63" });
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  const res = await dialog("Add a database destination", el("div", {}, field("Destination id (letters, digits, - and _)", id), field("Environment variable that holds the database address", env),
    field("Default schema (optional)", schema), field("Reason (at least 10 characters)", why), err), "Save destination", async () => {
    try {
      return await api("POST", P(p.id) + "/destinations", { destination: { destination_id: id.value.trim(), url_env: env.value.trim().toUpperCase(), schema_name: schema.value.trim() || null }, reason: why.value.trim() });
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (!res) return;
  const now = !res.second_approval && can("config.activate");           // one-person project: saved means in use
  if (now) await api("POST", P(p.id) + "/destinations/" + res.id + "/versions/" + res.version + "/activate", { reason: why.value.trim() });
  await refresh();
  say("Saved as version " + res.version + (now ? " and in use." : res.second_approval ? ". Another person approves it, then it can be put in use." : ". Someone who can put it in use does that next."), "good");
}

// Radio buttons drawn as cards: [value, title, icon, explanation]. Returns { node, value() }.
function choiceCards(name, options, current) {
  const group = name + "-" + (++uid);
  const cards = options.map(([v, title, ic, text]) => {
    const r = el("input", { type: "radio", name: group, value: v, checked: v === current });
    return el("label", { class: "choice-card" }, r, el("span", { class: "choice-mark", "aria-hidden": "true" }, icon(ic)),
      el("span", {}, el("strong", {}, title), el("span", { class: "muted" }, text)));
  });
  return { node: el("div", { class: "choice-cards", role: "radiogroup" }, cards), value: () => (cards.map((c) => c.querySelector("input")).find((r) => r.checked) || {}).value };
}

// ---- schedules: run the project's connections by themselves -----------------------------------------------------------------
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const ZONES = ["UTC", "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago", "America/Los_Angeles", "Australia/Sydney"];
const LOAD_MODES = [["incremental", LOAD_WORDS.incremental, "refresh", "Reads every record, but reuses the latest successful run's work for records that did not change. Much faster; people are still formed from all records."],
  ["full", LOAD_WORDS.full, "layers", "Reads and processes every record from scratch, and learns the scoring again. Slower; use it after big changes to the data or the rules."]];
const loadTag = (mode) => (mode === "full" ? tag(LOAD_WORDS.full, "info") : tag(LOAD_WORDS.incremental, "success"));
function scheduleWhen(s) {
  const t = s.at_time;
  if (s.frequency === "hourly") return "Every hour at :" + t.slice(3) + " (" + s.timezone + ")";
  if (s.frequency === "weekly") return "Every " + WEEKDAYS[s.weekday] + " at " + t + " (" + s.timezone + ")";
  return "Every day at " + t + " (" + s.timezone + ")";
}
async function schedulesPanel(p, connections) {
  const list = await api("GET", P(p.id) + "/schedules");
  const again = () => go(() => viewProject(p.id, "schedules"));
  const change = async (s, patch, done) => { await api("PATCH", P(p.id) + "/schedules/" + s.id, patch); await again(); say(done, "good"); };
  const remove = async (s) => {
    if (!(await confirmDialog("Delete schedule " + s.name + "?", "Runs it already started are kept. Nothing will start from it any more.", "Delete schedule", true))) return;
    await api("DELETE", P(p.id) + "/schedules/" + s.id);
    await again();
    say("Schedule deleted.", "good");
  };
  const manage = can("runs.start");
  const rows = list.map((s) => el("tr", {}, el("td", {}, el("strong", {}, s.name)), el("td", {}, s.connections.join(", ")), el("td", {}, scheduleWhen(s)), el("td", {}, loadTag(s.mode)),
    el("td", {}, s.enabled ? when(s.next_run_at) : el("span", { class: "muted" }, "Paused")),
    el("td", {}, s.last_run_id ? el("button", { type: "button", class: "link", onclick: () => go(() => viewRun(p.id, s.last_run_id)) }, STATUS_LABEL[s.last_status] || label(s.last_status || "")) : (s.last_status ? label(s.last_status) : "")),
    el("td", {}, s.enabled ? tag("On", "success") : el("span", {}, tag("Paused"), s.paused_reason ? el("div", { class: "muted" }, s.paused_reason) : null)),
    el("td", { class: "actions-cell" }, manage ? [
      btn("Edit", () => scheduleDialog(p, connections, s), "small"),
      s.enabled ? btn("Pause", () => change(s, { enabled: false }, "Schedule paused."), "small") : btn("Resume", () => change(s, { enabled: true }, "Schedule resumed."), "small"),
      btn("Delete", () => remove(s), "small danger")] : null)));
  const create = manage ? btn("New schedule", () => scheduleDialog(p, connections), "primary", "plus") : null;
  return el("div", {}, el("div", { class: "title-row" }, el("h2", { class: "section" }, "Scheduled runs"), create),
    el("p", { class: "hint" }, "A schedule starts an ordinary run of the chosen data sources at the times you set, using the versions in use at that moment, and processes only what changed or everything again. It never starts a new run while its last one is still going, runs once (not once per missed slot) after the server was off, and pauses itself after three failed runs in a row."),
    rows.length ? table(["Name", "Data", "When", "Processes", "Next run", "Last run", "State", ""], rows) : empty("No schedules yet." + (manage ? "" : " Someone who can start runs creates them."), create));
}
async function scheduleDialog(p, connections, existing) {
  const live = connections.filter((c) => c.live_version);
  if (!live.length) { say("This project has no data source in use yet.", "bad"); return; }
  const s = existing || {};
  const name = el("input", { type: "text", maxlength: "100", placeholder: "for example Nightly sweep", value: s.name || "" });
  const picks = checkList(live.map((c) => ({ name: c.id, description: typeTitle(c.type) })), new Set(s.connections || live.map((c) => c.id)), () => true, false);
  const freq = el("select", {}, [["daily", "Every day"], ["weekly", "Every week"], ["hourly", "Every hour"]].map(([v, t]) => el("option", { value: v, selected: v === s.frequency }, t)));
  const at = el("input", { type: "text", maxlength: "5", value: s.at_time || "02:00", placeholder: "02:00" });
  const day = el("select", {}, WEEKDAYS.map((d, i) => el("option", { value: String(i), selected: i === s.weekday }, d)));
  const dayField = field("Day of the week", day);
  const atField = field("", at);
  const setLabels = () => {
    dayField.hidden = freq.value !== "weekly";
    atField.querySelector("label").textContent = freq.value === "hourly" ? "Minutes past the hour, written as 00:MM (the hour is ignored)" : "Time, 24-hour clock (for example 02:30)";
  };
  const zones = el("datalist", { id: "zones" }, ZONES.map((z) => el("option", { value: z })));
  const browserZone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (_) { return "UTC"; } })();
  const zone = el("input", { type: "text", list: "zones", value: s.timezone || browserZone, maxlength: "40" });
  const mode = choiceCards("load", LOAD_MODES, s.mode || "incremental");
  const err = errBox();
  freq.addEventListener("change", setLabels);
  setLabels();
  const body = { name: () => name.value.trim(), connections: () => picks.values(), frequency: () => freq.value, at_time: () => at.value.trim(),
    weekday: () => (freq.value === "weekly" ? Number(day.value) : null), timezone: () => zone.value.trim(), mode: () => mode.value() };
  const saved = await dialog(existing ? "Edit schedule " + s.name : "New schedule", el("div", {}, field("Name", name), el("h3", { class: "section" }, "Data sources to read"), picks.node,
    el("h3", { class: "section" }, "How to load"), mode.node,
    field("How often", freq), dayField, atField, field("Time zone (a name such as Asia/Kolkata)", zone), zones, err), "Save schedule", async () => {
    try {
      const values = Object.fromEntries(Object.entries(body).map(([k, f]) => [k, f()]));
      return existing ? await api("PATCH", P(p.id) + "/schedules/" + s.id, values) : await api("POST", P(p.id) + "/schedules", values);
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (saved) { await go(() => viewProject(p.id, "schedules")); say("Schedule saved. Next run: " + (saved.next_run_at ? when(saved.next_run_at) : "none while paused") + ".", "good"); }
}
