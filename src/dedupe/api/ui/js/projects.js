"use strict";
// Projects: the list, a new project, and one project's tabs. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- runs tables ---------------------------------------------------------------------------------------------------------
const runState = (r) => tag(r.status === "queued" && r.queue_position ? "Queued, " + (r.queue_position - 1 ? (r.queue_position - 1) + " ahead" : "next") : (STATUS_LABEL[r.status] || label(r.status)), STATUS_KIND[r.status]);
function runsTable(runs, withProject, pid) {
  if (!runs.length) return empty("No runs yet." + (can("runs.start") ? "" : " Someone who can start runs starts the first one."),
    can("runs.start") ? btn("Start a run", () => go(() => newRun(pid)), "primary", "play") : null);
  const del = can("data.delete");
  const cols = ["Run", ...(withProject ? ["Project"] : []), "Status", "Connections", ["Records"], ["People"], ["In review queue"], "Started", "By", ...(del ? [""] : [])];
  return table(cols, runs.map((r) => linkRow(r.id, () => viewRun(r.project_id, r.id),
    withProject ? el("td", {}, projectName(r.project_id)) : null, el("td", {}, runState(r)),
    el("td", {}, r.connections.map((c) => c.id).join(", ") || el("span", { class: "muted" }, "imported")),
    el("td", { class: "num" }, num(r.summary.records)), el("td", { class: "num" }, num(r.summary.people)), el("td", { class: "num" }, num(r.summary.review_items)),
    el("td", {}, when(r.started_at || r.created_at)), el("td", {}, r.requested_by),
    del ? el("td", { class: "actions-cell" }, r.status === "running" || r.status === "queued" ? null
      : btn("Delete", () => deleteRun(r.project_id, r.id), "small danger", "trash", { "aria-label": "Delete run " + r.id })) : null)));
}

// ---- projects ----------------------------------------------------------------------------------------------------------
async function viewProjects() {
  here("/projects");
  await refreshCache();
  const del = can("data.delete");
  const rows = cache.projects.map((p) => linkRow(p.name, () => viewProject(p.id),
    el("td", {}, p.description || el("span", { class: "muted mono" }, p.id)), el("td", { class: "num" }, num(p.runs)),
    el("td", {}, p.latest_run ? runState(p.latest_run) : el("span", { class: "muted" }, "no runs")),
    el("td", {}, p.latest_run ? when(p.latest_run.started_at || p.latest_run.created_at) : ""), el("td", {}, p.you_own ? tag("You", "info") : p.created_by),
    del ? el("td", { class: "actions-cell" }, btn("Delete", () => deleteProject(p), "small danger", "trash", { "aria-label": "Delete project " + p.name })) : null));
  const create = can("projects.create") ? btn("New project", () => go(newProject), "primary", "plus") : null;
  show(page("Projects", "A project is one piece of dedupe work with its own data connections, runs, decisions and rules. You see the projects you created or were added to.", [create]),
    rows.length ? table(["Project", "Description", ["Runs"], "Latest run", "Last activity", "Owner", ...(del ? [""] : [])], rows)
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
const SETUP_PARTS = { data: "Data", matching: "Matching rules", golden: "Golden record", technical: "Technical settings" };
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
  const tabList = [["setup", "Setup"], ...(can("runs.read") ? [["runs", "Runs"]] : []), ...(subs.results.length ? [["results", "Results"]] : []),
    ...(subs.settings.length ? [["settings", "Settings"]] : [])];
  if (!tabList.some(([k]) => k === tab)) tab = "setup";
  if (tab === "setup" ? !subs.setup.includes(sub) : tab !== "runs" && !subs[tab].includes(sub)) sub = tab === "setup" || tab === "runs" ? null : subs[tab][0];
  if (tab === "runs") sub = null;
  here(P(pid) + (tab === "setup" && !sub ? "" : "/" + tab + (sub ? "/" + sub : "")));
  const guided = !runs.length;                     // until the first run, Setup walks through the steps in order
  let body;
  if (tab === "setup") body = sub ? await setupPart(p, sub, connections, guided) : await projectSetup(p, runs, connections, guided);
  else if (tab === "runs") body = el("div", {}, runsTable(runs, false, pid), await schedulesPanel(p, connections));
  else if (tab === "results") body = el("div", {}, subTabs(subs.results, { files: "Files", decisions: "Decisions" }, sub, (s) => viewProject(pid, "results", s)),
    sub === "files" ? await resultsPanel(p, runs) : await decisionsPanel([p], { project: true }));
  else body = el("div", {}, subTabs(subs.settings, { general: "General", members: "Members", activity: "Activity" }, sub, (s) => viewProject(pid, "settings", s)),
    sub === "general" ? settingsPanel(p) : sub === "members" ? await accessPanel(pid) : await activityPanel(pid));
  const actions = can("runs.start") ? [btn("New run", () => go(() => newRun(pid)), "primary", "play")] : [];
  const trail = tab === "setup" && sub ? crumbs(crumb("Projects", "/projects"), crumb(p.name, P(pid)), SETUP_PARTS[sub]) : crumbs(crumb("Projects", "/projects"), p.name);
  show(page(sub && tab === "setup" ? SETUP_PARTS[sub] : p.name, sub && tab === "setup" ? "Project " + p.name : p.description || "Project " + p.id, actions, trail,
    p.second_approval ? tag("Second approval on", "info") : null),
  tabs(tabList, tab, (t) => go(() => viewProject(pid, t))), tabPanel(tab, body));
}
function subTabs(keys, names, current, pick) {
  if (keys.length < 2) return null;
  return el("div", { class: "subtabs page-sub", role: "tablist" }, keys.map((k) => el("button", { type: "button", role: "tab", "aria-selected": k === current ? "true" : "false",
    onclick: () => go(() => pick(k)) }, names[k])));
}
// The Setup tab: the three things every run needs, each with what is chosen now, and the optional technical settings.
async function projectSetup(p, runs, connections, guided) {
  const pid = p.id;
  if (!can("config.read")) return empty("Your role cannot see this project's setup. Its runs and results are in the other tabs.");
  const st = await setupState(pid, connections);
  const { cfg } = st;
  const content = async (n) => (cfg[n].live ? (await api("GET", P(pid) + "/config/" + n + "/versions/" + cfg[n].live.version)).content : null);
  const [dec, gold] = await Promise.all([content("decision"), content("survivorship")]);
  const waiting = (n) => (n ? el("div", { class: "muted" }, plural(n, "version waits", "versions wait") + " for approval") : null);
  const state = (on, text, n) => el("td", {}, on ? "In use" : tag(text, "warn"), waiting(n));
  const notInUse = connections.filter((c) => !c.live_version).length;
  const rows = [
    linkRow("1. Data", () => viewProject(pid, "setup", "data"), el("td", {}, st.data.length ? st.data.map((c) => c.id + " (version " + c.live_version + ")").join(", ") : "No data in use yet."),
      el("td", {}, st.data.length ? "In use" : tag("Not added yet", "warn"), notInUse ? el("div", { class: "muted" }, plural(notInUse, "connection is", "connections are") + " not in use yet") : null)),
    linkRow("2. Matching rules", () => viewProject(pid, "setup", "matching"), el("td", {}, dec ? decisionWords(dec) : "Not chosen yet."),
      state(!!dec, "Not chosen yet", cfg.decision.waiting.length)),
    linkRow("3. Golden record", () => viewProject(pid, "setup", "golden"), el("td", {}, gold ? goldenWords(gold) : "Not chosen yet."),
      state(!!gold, "Not chosen yet", cfg.survivorship.waiting.length)),
    linkRow("Technical settings", () => viewProject(pid, "setup", "technical"), el("td", {}, cfg.settings.live ? "Version " + cfg.settings.live.version : "Standard values."),
      el("td", {}, "Optional", waiting(cfg.settings.waiting.length))),
  ];
  const missing = [st.data.length ? null : "add data and put it in use", dec ? null : "choose the matching rules", gold ? null : "choose the golden-record rules"].filter(Boolean);
  const ready = el("div", { class: "notice" + (missing.length ? " warn" : ""), role: "note" }, missing.length
    ? "Before a run can start: " + missing.join(", ") + "."
    : guided ? "Everything a run needs is chosen. Start the first run; you choose the data and the rule versions on the next screen." : "Everything a run needs is chosen.",
  !missing.length && can("runs.start") && guided ? el("div", { class: "row" }, btn("Start the first run", () => go(() => newRun(pid)), "primary", "play")) : null);
  const latest = runs.find((r) => r.status === "succeeded");
  const box = latest && can("queue.read") ? ((await api("GET", "/review/inbox")).find((x) => x.project_id === pid) || null) : null;
  const again = box && box.waiting_for_run ? el("div", { class: "notice", role: "note" }, plural(box.waiting_for_run, "decision waits", "decisions wait") +
    " for the next run: people and golden records change only when a run applies them.") : null;
  return el("div", {}, guided ? setupSteps(5, pid) : null,
    table(["Step", "What is chosen", "Status"], rows), ready, again,
    latest ? el("h2", { class: "section" }, "Latest finished run") : null,
    latest ? kv([["Run", el("button", { type: "button", class: "link", onclick: () => go(() => viewRun(pid, latest.id)) }, latest.id)], ["Finished", when(latest.finished_at)],
      ["People found", num(latest.summary.people)], box ? ["Pairs to review", box.open ? el("button", { type: "button", class: "link", onclick: link("/review/" + pid) }, num(box.open)) : "0"] : null]) : null);
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
  const ACTIONS = { propose: "Saved a connection version", activate: "Put a version in use", approve: "Approved a version", decline: "Declined a version", copy: "Copied a connection",
    source_inspect: "Looked inside a source", source_preview: "Previewed a mapping", upload: "Uploaded a file", create_project: "Created the project", grant_access: "Added a member",
    revoke_access: "Removed a member", set_second_approval: "Changed the approval setting", rename_project: "Renamed the project", start: "Started a run", cancel: "Cancelled a run",
    delete_run: "Deleted a run", delete_upload: "Deleted an uploaded file", approve_match: "Decided: same person", reject_match: "Decided: different people",
    mark_unsure: "Marked a pair not sure", undo: "Undid a decision", split_person: "Split a person", override_field: "Corrected a field", approve_pending: "Gave a second approval",
    decline_pending: "Declined a decision" };
  const RULES_OF = { decision: "the matching rules", survivorship: "the golden-record rules", settings: "the technical settings" };
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
    return rules ? ref.replace(/^[^:]+:/, "version ") : ref;
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
      "When on, a connection or rule change is approved by someone other than whoever proposed it, and sensitive decisions need a second person too. It needs at least two people in the project. When off, one person can do everything."))),
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
    el("p", {}, "This erases the project for good: its runs and results, uploaded files, person IDs, steward decisions, rules, connections and schedules. The audit log keeps who deleted it, when and why. This cannot be undone."),
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
    try { await api("POST", P(pid) + "/access", { subject: who.value.trim().toLowerCase(), role: role.value }); }
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
