"use strict";
// Projects: the list, a new project, and one project's tabs. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- runs tables ---------------------------------------------------------------------------------------------------------
const runState = (r) => tag(r.status === "queued" && r.queue_position ? "Queued, " + (r.queue_position - 1 ? (r.queue_position - 1) + " ahead" : "next") : (STATUS_LABEL[r.status] || label(r.status)), STATUS_KIND[r.status]);
function runsTable(runs, withProject, pid) {
  if (!runs.length) return empty("No runs yet." + (can("runs.start") ? "" : " Someone who can start runs starts the first one."),
    can("runs.start") ? btn("Start a run", () => (pid ? quickRun(pid) : go(() => newRun())), "primary", "play") : null);
  const del = can("data.delete");
  const cols = ["Run", ...(withProject ? ["Project"] : []), "Status", "Data", ["Records"], ["Customers"], ["Sent to a person"], "Started", "By", ...(del ? [""] : [])];
  return table(cols, runs.map((r) => linkRow(withProject ? r.id : runTitle(r, runs), () => viewRun(r.project_id, r.id),
    withProject ? el("td", {}, projectName(r.project_id)) : null, el("td", {}, runState(r)),
    el("td", {}, r.connections.map((c) => c.id).join(", ") || el("span", { class: "muted" }, "imported")),
    el("td", { class: "num" }, num(r.summary.records)), el("td", { class: "num" }, num(r.summary.people)), el("td", { class: "num" }, num(r.summary.review_items)),
    el("td", {}, when(r.started_at || r.created_at)), el("td", {}, r.requested_by),
    del ? el("td", { class: "actions-cell" }, r.status === "running" || r.status === "queued" ? null
      : moreMenu([["Delete run", () => deleteRun(r.project_id, r.id), true]], "run " + r.id)) : null)));
}

// ---- projects ----------------------------------------------------------------------------------------------------------
async function viewProjects() {
  here("/projects");
  await refreshCache();
  const del = can("data.delete");
  const rows = cache.projects.map((p) => linkRow(p.name, () => viewProject(p.id),
    el("td", {}, progressTag(p.progress)), el("td", {}, p.description || el("span", { class: "muted mono" }, p.id)),
    el("td", {}, p.latest_run ? when(p.latest_run.started_at || p.latest_run.created_at) : el("span", { class: "muted" }, "no runs yet")),
    el("td", {}, p.you_own ? tag("You", "info") : p.created_by),
    del ? el("td", { class: "actions-cell" }, moreMenu([["Delete project", () => deleteProject(p), true]], p.name)) : null));
  const create = can("projects.create") ? btn("New project", () => go(newProject), "primary", "plus") : null;
  show(page("Projects", "A project is one piece of dedupe work with its own data, rules, runs and decisions. You see the projects you created or were added to.", [create]),
    rows.length ? table(["Project", "Where it is", "Description", "Last run", "Owner", ...(del ? [""] : [])], rows)
      : empty(create ? "No projects yet. Create the first one." : "You are not in any project yet. Ask an administrator to create one or to add you to one.", create));
}
// A new project is set up in five steps and nothing is chosen for the user: name it here, then add its data, choose the matching rules and the
// golden-record rules (Setup tab), then check and start the first run.
async function newProject() {
  here("/new-project");
  const name = el("input", { type: "text", maxlength: "120" });
  const id = el("input", { type: "text", maxlength: "40" });
  const desc = el("textarea", { class: "plain", maxlength: "500" });
  const second = el("input", { type: "checkbox" });
  let touched = false;
  name.addEventListener("input", () => { if (!touched) id.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40); });
  id.addEventListener("input", () => { touched = true; });
  const err = errBox();
  const create = async () => {
    fail(err, "");
    let created;
    try { created = await api("POST", "/projects", { id: id.value.trim(), name: name.value.trim(), description: desc.value.trim(), second_approval: second.checked }); }
    catch (e) { fail(err, e.message); return; }
    await refreshCache();
    await go(() => viewProject(created.id, "setup", "data"));
    say("Project created. Next: add its data.", "good");
  };
  show(page("New project", "Name the project, add its data, choose how records are matched and how golden records are built, then check and run. Nothing is chosen for you.",
    [btn("Cancel", () => go(viewProjects))], crumbs(crumb("Projects", "/projects"), "New project")),
  setupSteps(1), el("div", { class: "narrow" }, field("Name", name), field("Id (used in addresses: lower-case letters, digits and -)", id), field("Description (optional)", desc),
    el("label", { class: "check" }, second, el("span", {}, "Require a second person to approve changes", el("span", { class: "muted" }, "Leave this off while you work alone: then what you save is in use at once. Turn it on in the project's settings once others join."))),
    err, el("div", { class: "row" }, btn("Create and continue", create, "primary", "next"))));
  name.focus();
}
async function pickProject(title, text, then) {
  await refreshCache();
  if (!cache.projects.length) { await go(viewProjects); say("Create a project first.", "bad"); return; }
  if (cache.projects.length === 1) { then(cache.projects[0].id); return; }
  const sel = el("select", {}, cache.projects.map((p) => el("option", { value: p.id }, p.name)));
  const chosen = await dialog(title, el("div", {}, el("p", { class: "hint" }, text), field("Project", sel)), "Continue", async () => sel.value);
  if (chosen) then(chosen);
}

// ---- one project: Setup, Runs, Results, Settings ---------------------------------------------------------------------------
// Older addresses and links still open the right place.
const OLD_TAB = { overview: ["setup"], connections: ["setup", "data"], rules: ["setup", "matching"], schedules: ["runs"], decisions: ["results", "decisions"],
  access: ["settings", "members"], activity: ["settings", "activity"] };
const SETUP_PARTS = { data: "Data", matching: "Matching rules", golden: "Golden record", technical: "Fields and search" };
const SETUP_CONFIG = { matching: "decision", golden: "survivorship", technical: "settings" };
const SETUP_STEP = { data: 2, matching: 3, golden: 4 };
function setupSteps(on, pid) {
  const to = [null, ["settings", "general"], ["setup", "data"], ["setup", "matching"], ["setup", "golden"], ["setup"]];
  return steps(["Project", "Data", "Matching rules", "Golden record", "Check and run"], on, pid ? (n) => go(() => viewProject(pid, ...to[n])) : null);
}
// Which parts of a project are chosen: data in use, and a version in use of the matching and golden-record rules (technical settings are optional).
async function setupState(pid, connections) {
  const names = ["decision", "survivorship", "settings"];
  const lists = can("config.read") ? await Promise.all(names.map((n) => api("GET", P(pid) + "/config/" + n))) : names.map(() => []);
  const cfg = Object.fromEntries(names.map((n, i) => [n, { versions: lists[i], live: lists[i].find((v) => v.status === "active"),
    waiting: lists[i].filter((v) => v.status === "proposed" || v.status === "approved") }]));
  const data = connections.filter((c) => c.live_version);
  return { cfg, data, ready: data.length > 0 && !!cfg.decision.live && !!cfg.survivorship.live };
}
const percent = (x) => Math.round(x * 100) + "%";
function goldenWords(c) {
  const count = {};
  for (const v of Object.values(c.fields || {})) count[v] = (count[v] || 0) + 1;
  const parts = Object.entries(count).sort((x, y) => y[1] - x[1]).map(([k, n]) => strategyName(k) + " for " + plural(n, "column", "columns"));
  return (parts.length ? parts.join(", ") + "; " + strategyName(c.default_strategy || "most_recent").toLowerCase() + " for the rest. " : strategyName(c.default_strategy || "most_recent") + " for every column. ") +
    ((c.source_trust || []).length ? "Trust order: " + c.source_trust.join(", ") + "." : "Sources ranked automatically by how complete they are.");
}
// One project. Its main page is the journey: Data > Rules > Run > Review > Results, with the one next step on top and each step's state and
// buttons below. Everything else is a page under it, reached from a step or from More: a part of the setup, all runs, results, decisions made,
// settings, members, activity.
const PROJECT_PAGES = { runs: "All runs", results: "Results", settings: "Project settings" };
async function viewProject(pid, tab, sub) {
  if (OLD_TAB[tab]) [tab, sub] = [OLD_TAB[tab][0], OLD_TAB[tab][1] || sub];
  const [p, runs, connections] = await Promise.all([api("GET", P(pid)), can("runs.read") ? api("GET", P(pid) + "/runs") : [],
    can("config.read") ? api("GET", P(pid) + "/connections") : []]);
  await refreshCache();
  const manage = can("project.access") || p.you_own;
  const subs = {
    setup: can("config.read") ? Object.keys(SETUP_PARTS) : [],
    results: [...(can("results.export") ? ["files"] : []), ...(can("queue.read") ? ["decisions"] : [])],
    settings: [...(manage ? ["general", "members"] : []), ...(can("runs.read") ? ["activity"] : [])],
  };
  if (tab === "runs" && !can("runs.read")) tab = "setup";
  if ((tab === "results" || tab === "settings") && !subs[tab].length) tab = "setup";
  if (!["setup", "runs", "results", "settings"].includes(tab)) tab = "setup";
  if (tab === "setup" ? sub && !subs.setup.includes(sub) : tab !== "runs" && !subs[tab].includes(sub)) sub = tab === "setup" ? null : subs[tab][0];
  if (tab === "runs") sub = null;
  here(P(pid) + (tab === "setup" && !sub ? "" : "/" + tab + (sub ? "/" + sub : "")));
  const guided = !runs.length;                     // until the first run, the setup pages walk through the steps in order
  const second = p.second_approval ? tag("Second approval on", "info") : null;
  const more = moreMenu([
    can("runs.read") && tab !== "runs" ? ["All runs and schedules", () => go(() => viewProject(pid, "runs"))] : null,
    can("queue.read") ? ["Decisions made", link("/review/" + pid + "/decided")] : null,
    subs.setup.includes("technical") ? ["Fields and search", () => go(() => viewProject(pid, "setup", "technical"))] : null,
    subs.settings.includes("general") ? ["Project settings", () => go(() => viewProject(pid, "settings", "general"))] : null,
    subs.settings.includes("members") ? ["Members", () => go(() => viewProject(pid, "settings", "members"))] : null,
    subs.settings.includes("activity") ? ["Activity", () => go(() => viewProject(pid, "settings", "activity"))] : null,
    can("data.delete") ? ["Delete project", () => deleteProject(p), true] : null], p.name);
  if (tab === "setup" && !sub) {
    show(page(p.name, p.description || null, [more], crumbs(crumb("Projects", "/projects"), p.name), second), await projectJourney(p, runs, connections));
    return;
  }
  let body;
  let title = PROJECT_PAGES[tab];
  const actions = [];
  if (tab === "setup") { body = await setupPart(p, sub, connections, guided); title = SETUP_PARTS[sub]; }
  else if (tab === "runs") {
    body = el("div", {}, runsTable(runs, false, pid), await schedulesPanel(p, connections));
    if (can("runs.start")) actions.push(btn("Start run", () => quickRun(pid), "primary", "play"));
  } else if (tab === "results") {
    title = sub === "decisions" ? "Decisions made" : "Results";
    body = sub === "files" ? await resultsPanel(p, runs) : await decisionsPanel([p], { project: true });
  } else body = el("div", {}, subTabs(subs.settings, { general: "General", members: "Members", activity: "Activity" }, sub, (s) => viewProject(pid, "settings", s)),
    sub === "general" ? settingsPanel(p) : sub === "members" ? await accessPanel(pid) : await activityPanel(pid));
  show(page(title, "Project " + p.name, actions, crumbs(crumb("Projects", "/projects"), crumb(p.name, P(pid)), title), second), body);
}
function subTabs(keys, names, current, pick) {
  if (keys.length < 2) return null;
  return el("div", { class: "subtabs page-sub", role: "tablist" }, keys.map((k) => el("button", { type: "button", role: "tab", "aria-selected": k === current ? "true" : "false",
    onclick: () => go(() => pick(k)) }, names[k])));
}
// "Same when: PAN, Aadhaar, Name + Date of birth + Phone and 2 more": the columns of the rules that merge, in a few words.
function sameWhenShort(dec) {
  const rules = (dec.rules || []).filter((r) => r.type === "auto_merge" && r.enabled !== false);
  const words = rules.map((r) => {
    const ps = matchParts(r.when || {});
    return ps ? ps.map((x) => MATCH_COLUMNS[x.col][0]).join(" + ") : label(r.id);
  });
  if (!words.length) return "No rule merges records by itself; " + leftoverWords(dec).toLowerCase();
  return "The same when: " + words.slice(0, 4).join(", ") + (words.length > 4 ? " and " + (words.length - 4) + " more" : "") + ".";
}
const JOURNEY = ["Data", "Rules", "Run", "Review", "Results"];
// The journey page: where the project is (steps), the one thing to do next, and one row per step with what it holds now.
async function projectJourney(p, runs, connections) {
  const pid = p.id;
  const st = can("config.read") ? await setupState(pid, connections) : null;
  const content = async (n) => (st && st.cfg[n].live ? (await api("GET", P(pid) + "/config/" + n + "/versions/" + st.cfg[n].live.version)).content : null);
  const [dec, gold] = await Promise.all([content("decision"), content("survivorship")]);
  const latest = runs[0] || null;
  const done = runs.find((r) => r.status === "succeeded") || null;
  const box = done && can("queue.read") ? ((await api("GET", "/review/inbox")).find((x) => x.project_id === pid) || null) : null;
  const pr = (cache.projects.find((x) => x.id === pid) || {}).progress || { step: "data" };
  const openRun = (r) => () => go(() => viewRun(pid, r.id));
  const missingRules = !dec ? "matching" : !gold ? "golden" : null;
  // the next step: one sentence and the main button of the page
  const NEXT = {
    data: ["Add the data to find duplicates in: files or databases.", can("sources.manage") || can("config.read") ? ["Add data", () => go(() => viewProject(pid, "setup", "data")), "database"] : null],
    rules: ["Choose " + (missingRules === "golden" ? "how the one clean record per customer is built." : "when two records are the same customer."),
      can("config.read") ? ["Choose rules", () => go(() => viewProject(pid, "setup", missingRules || "matching")), "sliders"] : null],
    run: can("runs.start") ? ["Data and rules are set. Start the first run to find the duplicates.", ["Start run", () => quickRun(pid), "play"]]
      : ["Data and rules are set. Someone who can start runs starts the first one.", null],                       // U2-25
    running: ["A run is going. This page shows the result when it is done.", latest ? ["Open the run", openRun(latest), "clock"] : null],
    failed: ["The last run did not finish. Open it to see why, then run again.", latest ? ["Open the run", openRun(latest), "alert"] : null],
    review: [plural(pr.open_review, "pair needs", "pairs need") + " your decision: same customer or not.", can("queue.read") ? ["Review " + plural(pr.open_review, "pair", "pairs"), link("/review/" + pid), "inbox"] : null],
    done: ["Up to date. Run again when the data or the rules change.", done && can("results.export") ? ["Get the results", () => go(() => viewProject(pid, "results", "files")), "download"] : null],
  }[pr.step] || ["", null];
  const nextBar = el("div", { class: "next-step" }, el("div", {}, el("span", { class: "muted" }, "Next step"), el("strong", {}, NEXT[0])),
    NEXT[1] ? btn(NEXT[1][0], NEXT[1][1], "primary", NEXT[1][2]) : null);
  const at = { data: 0, rules: 1, run: 2, running: 2, failed: 2, review: 3, done: 4 }[pr.step] || 0;
  const bar = el("ol", { class: "journey", "aria-label": "Steps of this project" }, JOURNEY.map((name, i) =>
    el("li", { class: i < at || pr.step === "done" ? "done" : i === at ? "on" : null, "aria-current": i === at ? "step" : null },
      el("span", { class: "n" }, String(i + 1)), el("span", {}, name))));
  // one row per step: what it holds now, and its buttons (never the main one: that is the next step's)
  const row = (n, name, lines, buttons) => el("div", { class: "journey-row" }, el("div", { class: "jname" }, el("span", { class: "n" }, String(n)), name),
    el("div", { class: "jbody" }, lines.filter(Boolean).map((t) => (t instanceof Node && t.tagName === "DIV" ? t : el("div", {}, t)))), el("div", { class: "row" }, buttons.filter(Boolean)));
  const notInUse = connections.filter((c) => !c.live_version).length;
  const records = done && done.summary.records ? num(done.summary.records) + " records in the last run." : null;
  const rows = [
    row(1, "Data", st ? [st.data.length ? plural(st.data.length, "source", "sources") + " in use: " + st.data.map((c) => c.id).join(", ") + "." : tag("No data yet", "warn"),
      notInUse ? el("div", { class: "muted" }, plural(notInUse, "source is", "sources are") + " added but not in use yet.") : null, records]
      : [el("span", { class: "muted" }, "Your role cannot see the data of this project.")],
    [st ? btn(st.data.length ? "Open" : "Add data", () => go(() => viewProject(pid, "setup", "data")), "", st.data.length ? null : "database") : null]),
    row(2, "Rules", st ? [dec ? sameWhenShort(dec) : tag("Matching rules not chosen", "warn"), gold ? el("div", { class: "muted" }, "Golden record: " + goldenWords(gold)) : tag("Golden-record rules not chosen", "warn"),
      st.cfg.decision.waiting.length + st.cfg.survivorship.waiting.length ? el("div", { class: "muted" }, "Changes wait for approval.") : null] : [],
    [st ? btn("Matching rules", () => go(() => viewProject(pid, "setup", "matching"))) : null, st ? btn("Golden record", () => go(() => viewProject(pid, "setup", "golden"))) : null,
      st ? btn("Fields and search", () => go(() => viewProject(pid, "setup", "technical"))) : null]),
    row(3, "Run", [latest ? (latest.status === "succeeded" ? runTitle(latest, runs) + ": " + num(latest.summary.records) + " records became " + num(latest.summary.people) + " customers."
      : el("div", {}, runTitle(latest, runs) + ": ", runState(latest))) : "No run yet.",
    runs.length > 1 ? el("div", { class: "muted" }, plural(runs.length, "run", "runs") + " so far.") : null],
    [latest ? btn("Open", openRun(latest)) : null, can("runs.start") && st && st.ready && pr.step !== "running" ? btn(latest ? "Run again" : "Start run", () => quickRun(pid), "", "play") : null]),
    row(4, "Review", [done ? (box && box.open ? plural(box.open, "pair waits", "pairs wait") + " for a person." : "Nothing waits for a person.") : "Pairs a person must check appear after the first run.",
      box && box.waiting_for_run ? el("div", { class: "muted" }, plural(box.waiting_for_run, "decision is", "decisions are") + " made and change the customers at the next run.") : null],
    [done && can("queue.read") ? btn("Review", link("/review/" + pid)) : null, done && can("queue.read") ? btn("Decisions made", link("/review/" + pid + "/decided")) : null]),
    row(5, "Results", [done ? num(done.summary.people) + " customers, one clean record each, from " + runTitle(done, runs) + "." : "The customers and their clean records appear after the first run."],
      [done && can("person.read") ? btn("Find a customer", () => go(() => viewRun(pid, done.id, "customers")), "", "search") : null,
      done && can("results.export") ? btn("Get the results", () => go(() => viewProject(pid, "results", "files")), "", "download") : null]),
  ];
  return el("div", {}, bar, nextBar, el("div", { class: "panel journey-steps" }, rows));
}
// One part of the setup on its own page. Until the first run it carries the steps and a Next button, so a new project is set up in order.
async function setupPart(p, sub, connections, guided) {
  const order = ["data", "matching", "golden"];
  const i = order.indexOf(sub);
  const body = sub === "data" ? await projectConnections(p, connections) : await rulesPanel(p, SETUP_CONFIG[sub]);
  const nav = guided && i >= 0 ? el("div", { class: "row guide-nav" },
    btn("Back", () => go(() => (i ? viewProject(p.id, "setup", order[i - 1]) : viewProject(p.id, "settings", "general"))), "", "prev"),
    btn(i < 2 ? "Next: " + SETUP_PARTS[order[i + 1]] : "Next: check and run", () => go(() => viewProject(p.id, "setup", order[i + 1])), "primary", "next")) : null;
  return el("div", {}, guided && i >= 0 ? setupSteps(SETUP_STEP[sub], p.id) : null, body, nav);
}
async function activityPanel(pid) {
  const ACTIONS = { propose: "Saved a data source", activate: "Put a version in use", approve: "Approved a version", decline: "Declined a version", copy: "Copied a data source",
    source_inspect: "Looked inside a source", source_preview: "Previewed a mapping", upload: "Uploaded a file", create_project: "Created the project", grant_access: "Added a member",
    revoke_access: "Removed a member", set_second_approval: "Changed the approval setting", rename_project: "Renamed the project", start: "Started a run", cancel: "Cancelled a run",
    delete_run: "Deleted a run", delete_upload: "Deleted an uploaded file", approve_match: "Decided: same person", reject_match: "Decided: different people",
    mark_unsure: "Marked a pair not sure", undo: "Undid a decision", split_person: "Split a person", override_field: "Corrected a field", approve_pending: "Gave a second approval",
    decline_pending: "Declined a decision" };
  const RULES_OF = { decision: "the matching rules", survivorship: "the golden-record rules", settings: "the fields and search settings" };
  const RULE_ACTIONS = { propose: "Saved a version of ", activate: "Put a version in use: ", approve: "Approved a version of ", decline: "Declined a version of ", rollback: "Went back to an earlier version of " };
  const configOf = (e) => (/(?:^|\/)(decision|survivorship|settings):/.exec(e.object_ref || "") || [])[1];
  const what = (e) => {
    const rules = configOf(e);
    if (rules && e.detail && RULE_ACTIONS[e.detail.action]) return RULE_ACTIONS[e.detail.action] + RULES_OF[rules];
    return (e.detail && ACTIONS[e.detail.action]) || (label(e.event) + (e.detail && e.detail.action ? ": " + label(e.detail.action) : ""));
  };
  const target = (e) => {
    const ref = (e.object_ref || "").replace(pid + "/", "").replace(/^connection\//, "");
    const rules = configOf(e);
    const FILES = { golden_records: "customers file", person_records: "record map", households: "households file" };
    return rules ? ref.replace(/^[^:]+:/, "version ") : ref.replace(/\/(golden_records|person_records|households)$/, (m, f) => ": " + FILES[f]);
  };
  const rowOf = (e) => el("tr", {}, el("td", {}, when(e.created_at)), el("td", {}, e.actor), el("td", {}, what(e)), el("td", { class: "mono" }, target(e)),
    el("td", { class: "num" }, e.status == null ? "" : e.status));
  const first = await api("GET", P(pid) + "/activity?limit=50");
  if (!first.length) return empty("Nothing has happened in this project yet.");
  const t = table(["Time", "Who", "What", "On", ["Status"]], first.map(rowOf));
  const body = t.querySelector("tbody");
  let last = first[first.length - 1].id;
  const more = first.length === 50 ? btn("Show older events", async () => {
    const page2 = await api("GET", P(pid) + "/activity?limit=50&before_id=" + last);
    body.append(...page2.map(rowOf));
    if (page2.length) last = page2[page2.length - 1].id;
    if (page2.length < 50) more.remove();
  }, "small") : null;
  return el("div", {}, el("p", { class: "hint" }, "Who did what in this project, newest first. Personal data values are never recorded here."), t, more ? el("div", { class: "row" }, more) : null);
}
function settingsPanel(p) {
  const name = el("input", { type: "text", maxlength: "120", value: p.name });
  const desc = el("textarea", { class: "plain", maxlength: "500" }, p.description || "");
  const second = el("input", { type: "checkbox", checked: p.second_approval });
  const err = errBox();
  const save = async () => {
    fail(err, "");
    try {
      await api("PATCH", P(p.id) + "/settings", { name: name.value.trim(), description: desc.value.trim(), second_approval: second.checked });
    } catch (e) { fail(err, e.message); return; }
    await refreshCache();
    await go(() => viewProject(p.id, "settings"));
    say("Settings saved.", "good");
  };
  const remove = () => deleteProject(p);
  return el("div", {},
    el("div", { class: "cols" }, el("div", {}, field("Name", name), field("Description", desc)),
      kv([["Project id", el("span", { class: "mono" }, p.id)], ["Created by", p.created_by], ["Created", when(p.created_at)]])),
    el("h3", { class: "section" }, "Approvals"),
    el("label", { class: "check" }, second, el("span", {}, "Require a second person to approve changes", el("span", { class: "muted" },
      "When on, a change to a data source or the rules is approved by someone other than whoever proposed it, and sensitive decisions need a second person too. It needs at least two people in the project. When off, one person can do everything."))),
    err, el("div", { class: "row" }, btn("Save settings", save, "primary")),
    can("data.delete") ? el("div", { class: "panel danger-zone" }, el("h3", {}, "Delete this project"), el("div", { class: "body" },
      el("p", { class: "muted" }, "Erases everything in the project. Only its owner or an administrator can do it once the project has members."),
      btn("Delete project", remove, "danger", "trash"))) : null);
}
async function deleteProject(p) {
  const typed = el("input", { type: "text", maxlength: "40", autocomplete: "off" });
  const why = el("textarea", { class: "plain", maxlength: "300" });
  const e2 = errBox();
  const ok = await dialog("Delete project " + p.name + "?", el("div", {},
    el("p", {}, "This erases the project for good: its runs and results, uploaded files, person IDs, review decisions, rules, data sources and schedules. The audit log keeps who deleted it, when and why. This cannot be undone."),
    field("Type the project id (" + p.id + ") to confirm", typed), field("Why is it deleted? (at least 10 characters)", why), e2), "Delete project", async () => {
    try { return await api("POST", P(p.id) + "/delete", { confirm: typed.value.trim(), reason: why.value.trim() }); } catch (e) { fail(e2, e.message); return undefined; }
  }, true);
  if (ok) { await refreshCache(); await go(viewProjects); say("Project " + p.name + " was deleted.", "good"); }
}
async function accessPanel(pid) {
  const [members, roles] = await Promise.all([api("GET", P(pid) + "/access"), api("GET", "/roles")]);
  const who = el("input", { type: "text", maxlength: "200" });
  const role = el("select", {}, roles.roles.map((x) => el("option", { value: x.name, selected: x.name === "analyst" }, label(x.name))));
  const err = errBox();
  const add = async () => {
    try { await api("POST", P(pid) + "/access", { subject: who.value.trim(), role: role.value }); }
    catch (e) { fail(err, e.message, who); return; }
    await go(() => viewProject(pid, "access"));
    say(who.value.trim() + " was added as " + label(role.value) + ".", "good");
  };
  const drop = async (g) => {
    if (!(await confirmDialog("Remove " + g.subject + "?", g.subject + " loses the " + label(g.role) + " role in this project. Their earlier work stays.", "Remove", true))) return;
    await api("DELETE", P(pid) + "/access/" + encodeURIComponent(g.subject) + "/" + encodeURIComponent(g.role));
    await go(() => viewProject(pid, "access"));
    say(g.subject + " was removed from the project.", "good");
  };
  return el("div", {},
    el("p", { class: "hint" }, members.length ? "Only the people below can open this project, plus administrators and auditors. What each person may do inside it comes from their role."
      : "This project has no members listed, so anyone whose role allows an action can use it. Add a person to make it members-only."),
    members.length ? table(["Person", "Role", "Added by", "Added", ""], members.map((g) => el("tr", {}, el("td", {}, g.subject), el("td", {}, g.role === "owner" ? tag("Owner", "info") : label(g.role)),
      el("td", {}, g.granted_by), el("td", {}, when(g.granted_at)), el("td", { class: "actions-cell" }, g.role === "owner" ? "" : btn("Remove", () => drop(g), "small danger"))))) : null,
    el("h3", { class: "section" }, "Add a person"), el("div", { class: "row" }, field("Username", who), field("Role", role), btn("Add person", add, "primary")), err);
}
