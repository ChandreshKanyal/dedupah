"use strict";
// Dedupe workspace: projects, runs, connections, review and approvals on top of the API.
// Safety rules for this file: text is only ever put on the page with textContent (never innerHTML), the access token lives in a variable
// (never in storage or the URL), every request goes to this same origin, and the only thing kept in browser storage is the colour theme.
let token = null;
let me = null;
let timer = null;
let mode = "token";                // how people sign in: "token" (identity provider or test token) or "local" (username and password)
const cache = { projects: [], runs: [] };
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const THEME_KEY = "dedupe-theme";

// ---- small helpers -----------------------------------------------------------------------------------------------------
function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(3)) if (k != null && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const PATHS = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  folder: "M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z",
  play: "M7 4.5v15l12-7.5z",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  check: "M4 12.5 9 17.5 20 6.5",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z",
  plus: "M12 5v14 M5 12h14",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M20 20l-4-4",
  upload: "M12 16V4 M7 9l5-5 5 5 M4 20h16",
  file: "M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z M14 3v5h5",
  sheet: "M4 4h16v16H4z M4 10h16 M4 15h16 M10 4v16",
  cloud: "M7 18a4 4 0 0 1-.6-8A6 6 0 0 1 18 9a4.5 4.5 0 0 1 0 9z",
  server: "M4 4h16v6H4z M4 14h16v6H4z M8 7h.01 M8 17h.01",
  columns: "M4 4h16v16H4z M9.3 4v16 M14.7 4v16",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  users: "M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M22 20v-2a4 4 0 0 0-3-3.9 M16 4.1a4 4 0 0 1 0 7.8",
  sliders: "M4 6h9 M17 6h3 M15 4v4 M4 12h3 M11 12h9 M9 10v4 M4 18h11 M19 18h1 M17 16v4",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7 M20 4v7h-7",
};
function icon(name) {
  const s = document.createElementNS(SVG, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "icon");
  s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("d", PATHS[name] || PATHS.file);
  s.append(p);
  return s;
}
const can = (perm) => perm === "any" || (!!me && me.permissions.includes(perm));
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g, "");
const pct = (x) => (x == null ? "" : (x * 100).toFixed(1) + "%");
const when = (t) => (t ? new Date(t).toLocaleString() : "");
const num = (n) => (n == null ? "" : Number(n).toLocaleString());
const label = (s) => String(s).replace(/_/g, " ");

// ---- theme (the one thing kept in browser storage) ---------------------------------------------------------------------
function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem(THEME_KEY, t); } catch (_) { /* storage may be blocked */ }
  const b = $("theme");
  b.replaceChildren(icon(t === "dark" ? "sun" : "moon"));
  b.setAttribute("title", t === "dark" ? "Switch to light" : "Switch to dark");
}
function initTheme() {
  let t = "dark";
  try { t = localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; } catch (_) { /* default */ }
  setTheme(t);
}

// ---- messages and dialogs ----------------------------------------------------------------------------------------------
function say(msg, kind) {
  const b = $("banner");
  b.hidden = !msg;
  b.textContent = msg || "";
  b.className = kind || "";
}
function dialog(title, body, confirmLabel, onConfirm, danger) {
  return new Promise((resolve) => {
    const box = $("dialog");
    const close = (v) => { $("overlay").hidden = true; box.replaceChildren(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    const ok = el("button", { class: danger ? "danger" : "primary", onclick: async () => { const v = await onConfirm(); if (v !== undefined) close(v); } }, confirmLabel);
    box.replaceChildren(el("h2", { id: "dialog-title" }, title), body, el("div", { class: "buttons" }, el("button", { onclick: () => close(null) }, "Cancel"), ok));
    $("overlay").hidden = false;
    document.addEventListener("keydown", onKey);
    (box.querySelector("textarea, input") || ok).focus();
  });
}
function askReason(title, prompt, minLength, confirmLabel, danger) {
  const area = el("textarea", { class: "plain", maxlength: "300", "aria-label": prompt });
  const err = el("p", { class: "bad", hidden: true }, "Please write at least " + minLength + " characters.");
  const body = el("div", {}, el("p", { class: "hint" }, prompt + (minLength ? " (at least " + minLength + " characters)" : " (optional)")), area, err);
  return dialog(title, body, confirmLabel || "Confirm", () => {
    const v = area.value.trim();
    if (v.length < minLength) { err.hidden = false; return undefined; }
    return v;
  }, danger);
}

// ---- requests ----------------------------------------------------------------------------------------------------------
async function api(method, path, body, extra, raw) {
  const headers = { Authorization: "Bearer " + token };
  if (body !== undefined && !raw) headers["Content-Type"] = "application/json";
  if (extra) Object.assign(headers, extra);
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : (raw ? body : JSON.stringify(body)), credentials: "omit", cache: "no-store" });
  let data = null;
  try { data = await r.json(); } catch (_) { /* no body */ }
  if (r.status === 401) { signOut("Your session ended. Sign in again."); throw new Error("unauthorised"); }
  if (!r.ok) {
    const e = (data && data.error) || {};
    const fields = (e.fields || []).map((f) => f.field + " (" + f.problem + ")").join(", ");
    throw new Error((e.message || "The request failed.") + (fields ? " " + fields : ""));
  }
  return data;
}
async function run(fn) {
  try { say(""); await fn(); } catch (e) { if (e.message !== "unauthorised") say(e.message, "bad"); }
}

// ---- sign in and out ---------------------------------------------------------------------------------------------------
async function passwordLogin(username, password) {
  const r = await fetch("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }), credentials: "omit", cache: "no-store" });
  let data = null;
  try { data = await r.json(); } catch (_) { /* no body */ }
  if (!r.ok) throw new Error((data && data.error && data.error.message) || "Sign-in failed.");
  return data;
}
async function initLogin() {
  try {
    const r = await fetch("/auth/config", { credentials: "omit", cache: "no-store" });
    mode = r.ok && (await r.json()).password_login ? "local" : "token";
  } catch (_) { mode = "token"; }
  $("token-fields").hidden = mode === "local";
  $("password-fields").hidden = mode !== "local";
  $("login-help").textContent = mode === "local" ? "Sign in with your username and password."
    : "Paste your access token. It is kept in this page's memory only and is gone when you close or reload the tab.";
}
async function signIn(ev) {
  ev.preventDefault();
  const fail = $("login-error");
  fail.hidden = true;
  let mustChange = false;
  try {
    if (mode === "local") {
      const r = await passwordLogin($("username").value.trim().toLowerCase(), $("password").value);
      token = r.token;
      mustChange = r.must_change_password;
    } else {
      token = $("token").value.trim();
    }
    $("token").value = "";
    $("password").value = "";
    me = await api("GET", "/me");
  } catch (e) {
    token = null;
    $("password").value = "";
    fail.textContent = e.message === "unauthorised" ? "That token was not accepted." : e.message;
    fail.hidden = false;
    return;
  }
  $("login").hidden = true;
  $("app").hidden = false;
  $("avatar").textContent = (me.username || "?").slice(0, 1).toUpperCase();
  $("who").textContent = me.username;
  $("who").setAttribute("title", me.roles.join(", "));
  $("password-btn").hidden = mode !== "local";
  if (mustChange) { $("nav").replaceChildren(); await run(() => viewPassword(true)); return; }       // nothing else works until the password is changed
  buildNav();
}
function signOut(msg) {
  token = null; me = null;
  stopTimer();
  $("app").hidden = true;
  $("login").hidden = false;
  $("nav").replaceChildren(); $("view").replaceChildren(); say("");
  const fail = $("login-error");
  fail.hidden = !msg;
  fail.textContent = msg || "";
}

// ---- navigation --------------------------------------------------------------------------------------------------------
const NAV = [
  ["", [["Home", "any", "home", () => viewHome()], ["Projects", "any", "folder", () => viewProjects()], ["Runs", "runs.read", "play", () => viewRuns()]]],
  ["Governance", [["Approvals", "config.approve", "check", () => viewApprovals()], ["Audit log", "logs.read", "shield", () => viewAudit()]]],
  ["Administration", [["Users", "users.manage", "users", () => viewUsers()], ["Roles", "roles.manage", "sliders", () => viewRoles()]]],
];
const NEW_MENU = [
  ["New project", "projects.create", "folder", () => newProject(), "Projects"], ["New run", "runs.start", "play", () => newRun(), "Runs"],
  ["Add data", "sources.manage", "database", () => pickProject("Add data", "Choose the project this data belongs to.", (pid) => go("Projects", () => viewProject(pid, "connections"))), "Projects"],
];
function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }
function go(name, fn) {
  stopTimer();
  $("search-results").hidden = true;
  for (const a of $("nav").querySelectorAll("a")) {
    if (a.dataset.name === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  run(fn);
}
function buildNav() {
  const nav = $("nav");
  nav.replaceChildren();
  const items = NEW_MENU.filter((m) => can(m[1]));
  if (items.length) {
    const menu = el("div", { id: "new-menu", hidden: true }, items.map(([text, , ic, fn, section]) => el("button", { onclick: () => { menu.hidden = true; go(section, fn); } }, icon(ic), text)));
    nav.append(el("button", { id: "new-btn", onclick: () => { menu.hidden = !menu.hidden; } }, icon("plus"), "New"), menu);
  }
  for (const [group, entries] of NAV) {
    const allowed = entries.filter((i) => can(i[1]));
    if (!allowed.length) continue;
    if (group) nav.append(el("h2", {}, group));
    for (const [name, , ic, fn] of allowed) {
      nav.append(el("a", { href: "#", "data-name": name, onclick: (e) => { e.preventDefault(); go(name, fn); } }, icon(ic), name));
    }
  }
  go("Home", () => viewHome());
}

// ---- page building blocks ------------------------------------------------------------------------------------------------
const show = (...nodes) => $("view").replaceChildren(...nodes.filter((n) => n != null && n !== false));
function page(title, subtitle, actions, crumbsNode, extra) {
  return el("div", { class: "page-head" },
    el("div", {}, crumbsNode ? el("p", { class: "crumbs" }, crumbsNode) : null, el("div", { class: "title-row" }, el("h1", {}, title), extra || null), subtitle ? el("p", {}, subtitle) : null),
    el("div", { class: "actions" }, actions || []));
}
const crumb = (text, fn) => el("button", { onclick: () => run(fn) }, text);
const crumbs = (...parts) => parts.flatMap((p, i) => (i ? [" / ", p] : [p]));
const head = (cols) => el("thead", {}, el("tr", {}, cols.map((h) => el("th", { class: Array.isArray(h) ? "num" : "" }, Array.isArray(h) ? h[0] : h))));
const table = (cols, rows) => el("table", {}, head(cols), el("tbody", {}, rows));
const wide = (cols, rows) => el("div", { class: "scroll" }, table(cols, rows));             // many columns: scroll sideways, not off the page
const empty = (text, ...buttons) => el("div", { class: "empty" }, el("div", {}, text), buttons.filter(Boolean).length ? el("div", { class: "row" }, buttons) : null);
const tag = (text, kind) => el("span", { class: "tag" + (kind ? " " + kind : "") }, text);
const STATUS_KIND = { active: "success", succeeded: "success", failed: "danger", declined: "danger", interrupted: "danger", proposed: "info", running: "info", queued: "info" };
const statusTag = (s) => tag(label(s), STATUS_KIND[s]);
const field = (text, input) => el("div", { class: "field" }, el("label", {}, text), input);
const btn = (text, onclick, cls, ic) => el("button", { class: cls || "", onclick }, ic ? icon(ic) : null, text);
const stat = (text, value, onclick) => (onclick ? el("button", { class: "stat", onclick }, el("div", { class: "label" }, text), el("div", { class: "value" }, value))
  : el("div", { class: "stat" }, el("div", { class: "label" }, text), el("div", { class: "value" }, value)));
const tile = (ic, title, text, onclick, pressed) => el("button", { class: "tile", onclick, "aria-pressed": pressed == null ? null : String(!!pressed) },
  el("span", { class: "tile-icon" }, icon(ic)), el("span", {}, el("strong", {}, title), el("span", { class: "muted" }, text)));
function tabs(list, current, pick) {
  return el("div", { class: "tabs", role: "tablist" }, list.map(([key, text]) =>
    el("button", { role: "tab", "aria-selected": key === current ? "true" : "false", onclick: () => run(() => pick(key)) }, text)));
}
async function refreshCache() {
  const [projects, runs] = await Promise.all([api("GET", "/projects"), can("runs.read") ? api("GET", "/runs?limit=50") : []]);
  Object.assign(cache, { projects, runs });
}
const projectName = (pid) => (cache.projects.find((p) => p.id === pid) || { name: pid }).name;

// ---- search ------------------------------------------------------------------------------------------------------------
function initSearch() {
  $("search-wrap").prepend(icon("search"));
  const box = $("search");
  const out = $("search-results");
  box.addEventListener("focus", () => { if (me) run(refreshCache); });
  box.addEventListener("input", () => {
    const q = box.value.trim().toLowerCase();
    if (!q) { out.hidden = true; return; }
    const hits = [
      ...cache.projects.filter((p) => (p.name + " " + p.id).toLowerCase().includes(q)).map((p) => ["folder", p.name, "Project", () => viewProject(p.id)]),
      ...cache.runs.filter((r) => (r.id + " " + r.project_id).toLowerCase().includes(q)).map((r) => ["play", r.id + " in " + projectName(r.project_id), "Run", () => viewRun(r.project_id, r.id)]),
    ].slice(0, 12);
    out.replaceChildren(...(hits.length ? hits.map(([ic, text, kind, fn]) => el("button", { role: "option", onclick: () => { box.value = ""; go(kind + "s", fn); } }, icon(ic), text, el("span", { class: "kind" }, kind)))
      : [el("div", { class: "empty" }, "No matches")]));
    out.hidden = false;
  });
  box.addEventListener("keydown", (e) => { if (e.key === "Escape") { box.value = ""; out.hidden = true; box.blur(); } });
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && me) { e.preventDefault(); box.focus(); } });
  document.addEventListener("click", (e) => { if (!$("search-wrap").contains(e.target)) out.hidden = true; });
}

// ---- home --------------------------------------------------------------------------------------------------------------
async function viewHome() {
  const [h] = await Promise.all([api("GET", "/home"), refreshCache()]);
  const latest = h.recent_runs.find((r) => r.status === "succeeded");
  const stats = [stat("Projects", num(h.projects), () => go("Projects", viewProjects)), stat("Connections", num(h.connections))];
  if (h.open_review_items != null) stats.push(stat("Items to review", num(h.open_review_items), latest ? () => go("Runs", () => viewRun(latest.project_id, latest.id, "review")) : null));
  if (h.pending_approvals != null) stats.push(stat("Waiting for approval", num(h.pending_approvals), () => go("Approvals", viewApprovals)));
  const start = [];
  if (can("projects.create")) start.push(tile("folder", "Create a project", "A project has its own data connections, decisions, person IDs and rules.", () => go("Projects", newProject)));
  if (can("sources.manage")) start.push(tile("database", "Add data", "Connect a file, sheet or database, or upload a file, inside a project.", () => pickProject("Add data", "Choose the project this data belongs to.", (pid) => go("Projects", () => viewProject(pid, "connections")))));
  if (can("runs.start")) start.push(tile("play", "Start a run", "Match and merge the records of chosen connections.", () => go("Runs", () => newRun())));
  if (latest && can("queue.read")) start.push(tile("users", "Review matches", "Open the review queue of the latest finished run.", () => go("Runs", () => viewRun(latest.project_id, latest.id, "review"))));
  show(page("Welcome, " + me.username, "Find duplicate customers across your sources, review them and keep one golden record per person."),
    el("div", { class: "stats" }, stats),
    start.length ? el("h2", { class: "section" }, "Get started") : null, start.length ? el("div", { class: "tiles" }, start) : null,
    can("runs.read") ? el("h2", { class: "section" }, "Recent runs") : null,
    can("runs.read") ? runsTable(h.recent_runs, true) : null);
}
const runState = (r) => tag(r.status === "queued" && r.queue_position ? "Queued, " + (r.queue_position - 1 ? (r.queue_position - 1) + " ahead" : "next") : label(r.status), STATUS_KIND[r.status]);
function runsTable(runs, withProject, pid) {
  if (!runs.length) return empty("No runs yet.", can("runs.start") ? btn("Start a run", () => go("Runs", () => newRun(pid)), "primary", "play") : null);
  const cols = [...(withProject ? ["Project"] : []), "Run", "Status", "Connections", ["Records"], ["People"], ["To review"], "Started", "By"];
  return table(cols, runs.map((r) => el("tr", { class: "click", onclick: () => go("Runs", () => viewRun(r.project_id, r.id)) },
    withProject ? el("td", {}, projectName(r.project_id)) : null, el("td", { class: "mono" }, r.id), el("td", {}, runState(r)),
    el("td", {}, r.connections.map((c) => c.id).join(", ") || el("span", { class: "muted" }, "imported")),
    el("td", { class: "num" }, num(r.summary.records)), el("td", { class: "num" }, num(r.summary.people)), el("td", { class: "num" }, num(r.summary.review_items)),
    el("td", {}, when(r.started_at || r.created_at)), el("td", {}, r.requested_by))));
}

// ---- projects ----------------------------------------------------------------------------------------------------------
async function viewProjects() {
  await refreshCache();
  const rows = cache.projects.map((p) => el("tr", { class: "click", onclick: () => go("Projects", () => viewProject(p.id)) },
    el("td", {}, el("strong", {}, p.name), el("div", { class: "muted" }, p.description || p.id)), el("td", { class: "num" }, num(p.runs)),
    el("td", {}, p.latest_run ? statusTag(p.latest_run.status) : el("span", { class: "muted" }, "no runs")),
    el("td", {}, p.latest_run ? when(p.latest_run.started_at || p.latest_run.created_at) : ""), el("td", {}, p.created_by)));
  const create = can("projects.create") ? btn("New project", () => run(newProject), "primary", "plus") : null;
  show(page("Projects", "A project is one piece of dedupe work with its own data connections, runs, decisions and rules. You see the projects you created or were added to.", [create]),
    rows.length ? table(["Project", ["Runs"], "Latest run", "Last activity", "Created by"], rows) : empty("No projects yet.", create));
}
async function newProject() {
  const name = el("input", { type: "text", maxlength: "120", "aria-label": "Project name" });
  const id = el("input", { type: "text", maxlength: "40", "aria-label": "Project id" });
  const desc = el("textarea", { class: "plain", maxlength: "500", "aria-label": "Description" });
  const second = el("input", { type: "checkbox" });
  let touched = false;
  name.addEventListener("input", () => { if (!touched) id.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40); });
  id.addEventListener("input", () => { touched = true; });
  const err = el("p", { class: "bad", hidden: true });
  const created = await dialog("New project", el("div", {}, field("Name", name), field("Id (used in addresses: lower-case letters, digits and -)", id), field("Description (optional)", desc),
    el("label", { class: "check" }, second, el("span", {}, "Require a second person to approve changes", el("span", { class: "muted" }, "Leave this off while you work alone. Turn it on in the project's settings once others join.")))
    , err),
  "Create project", async () => {
    try { return await api("POST", "/projects", { id: id.value.trim(), name: name.value.trim(), description: desc.value.trim(), second_approval: second.checked }); } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (created) { await refreshCache(); go("Projects", () => viewProject(created.id)); }
}
async function pickProject(title, text, then) {
  await refreshCache();
  if (!cache.projects.length) { go("Projects", viewProjects); say("Create a project first.", "bad"); return; }
  if (cache.projects.length === 1) { then(cache.projects[0].id); return; }
  const sel = el("select", { "aria-label": "Project" }, cache.projects.map((p) => el("option", { value: p.id }, p.name)));
  const chosen = await dialog(title, el("div", {}, el("p", { class: "hint" }, text), field("Project", sel)), "Continue", async () => sel.value);
  if (chosen) then(chosen);
}
async function viewProject(pid, tab) {
  tab = tab || "overview";
  const [p, runs, connections] = await Promise.all([api("GET", "/projects/" + pid), can("runs.read") ? api("GET", "/projects/" + pid + "/runs") : [],
    can("config.read") ? api("GET", "/projects/" + pid + "/connections") : []]);
  await refreshCache();
  const manage = can("project.access") || p.you_own;
  const actions = can("runs.start") ? [btn("New run", () => go("Projects", () => newRun(pid)), "primary", "play")] : [];
  const tabList = [["overview", "Overview"], ["connections", "Connections"], ["runs", "Runs"], ...(can("runs.read") ? [["schedules", "Schedules"]] : []), ...(can("results.export") ? [["results", "Results"]] : []), ["rules", "Rules"],
    ...(can("judgment.approve") ? [["approvals", "Approvals"]] : []),
    ["activity", "Activity"], ...(manage ? [["access", "Access"], ["settings", "Settings"]] : [])];
  let body;
  if (tab === "overview") body = projectOverview(p, runs, connections);
  else if (tab === "connections") body = projectConnections(p, connections);
  else if (tab === "runs") body = runsTable(runs, false, pid);
  else if (tab === "schedules") body = await schedulesPanel(p, connections);
  else if (tab === "results") body = await resultsPanel(p, runs);
  else if (tab === "rules") body = await rulesPanel(pid);
  else if (tab === "activity") body = await activityPanel(pid);
  else if (tab === "access") body = await accessPanel(pid);
  else if (tab === "settings") body = settingsPanel(p);
  else body = await decisionApprovals(pid, () => viewProject(pid, "approvals"));
  show(page(p.name, p.description || "Project " + p.id, actions, crumbs(crumb("Projects", () => go("Projects", viewProjects)), p.name),
    p.second_approval ? tag("Second approval on") : null),
  tabs(tabList, tab, (t) => viewProject(pid, t)), body);
}
function projectOverview(p, runs, connections) {
  const live = connections.filter((c) => c.live_version);
  const finished = runs.find((r) => r.status === "succeeded");
  const going = runs.find((r) => r.status === "running" || r.status === "queued");
  const toReview = finished ? finished.summary.review_items : null;
  const todo = [
    ["Add your data", "Connect a file, spreadsheet or database, or upload a file, then map its columns.", connections.length > 0, "connections", can("sources.manage")],
    ["Make the data ready to use", p.second_approval ? "A second person approves each connection, then it is made live." : "Make the connection live so runs can read it.", live.length > 0, "connections",
      can("config.activate") || can("config.approve")],
    ["Run the match", "Find the records that are the same person and group households.", !!finished || !!going, "runs", can("runs.start")],
    ["Review the uncertain matches", toReview == null ? "Appears after the first run." : num(toReview) + " pairs need a person to decide.", !!finished && toReview === 0, "runs", can("queue.read")],
    ["Take the results out", "Download the golden records as Excel, CSV or Parquet, or write them into a database.", false, "results", can("results.export") && !!finished],
  ];
  const next = todo.findIndex((t) => !t[2]);
  const list = el("div", { class: "todo" }, todo.map(([title, text, done, tab, allowed], i) => el("div", { class: "todo-row" + (i === next ? " next" : "") },
    el("span", { class: "todo-mark" }, done ? icon("check") : String(i + 1)), el("div", {}, el("strong", {}, title), el("div", { class: "muted" }, text)),
    allowed && !done ? btn(i === next ? "Go" : "Open", () => run(() => viewProject(p.id, tab)), i === next ? "primary small" : "small") : null)));
  return el("div", {}, el("div", { class: "stats" }, stat("Connections", num(connections.length), () => run(() => viewProject(p.id, "connections"))), stat("Live connections", num(live.length)),
    stat("Runs", num(runs.length), () => run(() => viewProject(p.id, "runs"))), stat("People found", finished ? num(finished.summary.people) : "-"),
    stat("Items to review", toReview == null ? "-" : num(toReview), finished && can("queue.read") ? () => go("Projects", () => viewRun(p.id, finished.id, "review")) : null)),
  el("h2", { class: "section" }, "Where you are"), list);
}
async function activityPanel(pid, before) {
  const events = await api("GET", "/projects/" + pid + "/activity?limit=50" + (before ? "&before_id=" + before : ""));
  const ACTIONS = { propose: "Saved a connection version", activate: "Made a version live", approve: "Approved a version", decline: "Declined a version", copy: "Copied a connection",
    source_inspect: "Looked inside a source", source_preview: "Previewed a mapping", upload: "Uploaded a file", create_project: "Created the project", grant_access: "Added a member",
    revoke_access: "Removed a member", set_second_approval: "Changed the approval setting", start: "Started a run", cancel: "Cancelled a run" };
  const what = (e) => (e.detail && ACTIONS[e.detail.action]) || (label(e.event) + (e.detail && e.detail.action ? ": " + label(e.detail.action) : ""));
  const target = (e) => (e.object_ref || "").replace(pid + "/", "").replace(/^connection\//, "");
  const more = events.length === 50 ? btn("Older events", () => run(async () => { const w = await activityPanel(pid, events[events.length - 1].id); $("view").lastChild.replaceWith(w); }), "small") : null;
  return el("div", {}, el("p", { class: "hint" }, "Who did what in this project, newest first. Personal data values are never recorded here."),
    events.length ? table(["Time", "Who", "What", "On", ["Status"]], events.map((e) => el("tr", {}, el("td", {}, when(e.created_at)), el("td", {}, e.actor), el("td", {}, what(e)),
      el("td", { class: "mono" }, target(e)), el("td", { class: "num" }, e.status == null ? "" : e.status)))) : empty("Nothing has happened in this project yet."), more);
}
function settingsPanel(p) {
  const second = el("input", { type: "checkbox", checked: p.second_approval });
  const save = () => run(async () => {
    await api("PATCH", "/projects/" + p.id + "/settings", { second_approval: second.checked });
    await refreshCache();
    await viewProject(p.id, "settings");
    say("Saved.", "good");
  });
  return el("div", {}, el("table", { class: "kv" }, el("tbody", {}, el("tr", {}, el("th", {}, "Project id"), el("td", { class: "mono" }, p.id)),
    el("tr", {}, el("th", {}, "Created by"), el("td", {}, p.created_by)), el("tr", {}, el("th", {}, "Created"), el("td", {}, when(p.created_at))))),
  el("h3", { class: "section" }, "Approvals"),
  el("label", { class: "check" }, second, el("span", {}, "Require a second person to approve changes", el("span", { class: "muted" },
    "When on, a connection or rule change is approved by someone other than whoever proposed it, and sensitive decisions need a second person too. It needs at least two people in the project. When off, one person can do everything."))),
  el("div", { class: "row" }, btn("Save", save, "primary")));
}
async function accessPanel(pid) {
  const [members, roles] = await Promise.all([api("GET", "/projects/" + pid + "/access"), api("GET", "/roles")]);
  const who = el("input", { type: "text", maxlength: "200", "aria-label": "Username" });
  const role = el("select", { "aria-label": "Role" }, roles.roles.map((x) => el("option", { value: x.name, selected: x.name === "analyst" }, label(x.name))));
  const err = el("p", { class: "bad", hidden: true });
  const add = async () => {
    try { await api("POST", "/projects/" + pid + "/access", { subject: who.value.trim().toLowerCase(), role: role.value }); await viewProject(pid, "access"); }
    catch (e) { err.textContent = e.message; err.hidden = false; }
  };
  const drop = (g) => run(async () => { await api("DELETE", "/projects/" + pid + "/access/" + encodeURIComponent(g.subject) + "/" + encodeURIComponent(g.role)); await viewProject(pid, "access"); });
  return el("div", {},
    el("p", { class: "hint" }, members.length ? "Only the people below can open this project, plus administrators and auditors. What each person may do inside it comes from their role."
      : "This project has no members listed, so anyone whose role allows an action can use it. Add a person to make it members-only."),
    members.length ? table(["Person", "Role", "Added by", "Added", ""], members.map((g) => el("tr", {}, el("td", {}, g.subject), el("td", {}, g.role === "owner" ? tag("Owner", "info") : label(g.role)),
      el("td", {}, g.granted_by), el("td", {}, when(g.granted_at)), el("td", {}, g.role === "owner" ? "" : btn("Remove", () => drop(g)))))) : null,
    el("h3", { class: "section" }, "Add a person"), el("div", { class: "row" }, field("Username", who), field("Role", role), btn("Add", add, "primary")), err);
}

// ---- runs --------------------------------------------------------------------------------------------------------------
async function viewRuns() {
  await refreshCache();
  show(page("Runs", "Every run of every project. Open a run to review its results.", can("runs.start") ? [btn("New run", () => go("Runs", () => newRun()), "primary", "play")] : []),
    runsTable(cache.runs, true));
}
async function cancelRun(pid, rid) {
  const ok = await dialog("Cancel this run?", el("p", {}, "The run stops now and keeps no results. Nothing else is changed; you can start it again."), "Cancel run", async () => true, true);
  if (!ok) return;
  stopTimer();
  await api("POST", "/projects/" + pid + "/runs/" + rid + "/cancel");
  await refreshCache();
  say("The run was cancelled.", "good");
  await viewRun(pid, rid);
}
async function startRun(pid, connections, fromRun, reason) {
  const r = await api("POST", "/projects/" + pid + "/runs", { connections, from_run: fromRun || null, reason: reason || "" }, { "Idempotency-Key": uuid() });
  go("Runs", () => viewRun(pid, r.id));
  say("Run started. This page updates by itself until it finishes.", "good");
}
async function newRun(pid, step, chosen) {
  await refreshCache();
  chosen = chosen || new Set();
  step = step || (pid ? 2 : 1);
  const steps = el("div", { class: "steps" }, ["1. Project", "2. Connections", "3. Start"].map((t, i) => el("span", { class: i + 1 === step ? "on" : "" }, t)));
  const to = (s, p) => run(() => newRun(p || pid, s, chosen));
  if (step === 1) {
    const tiles = cache.projects.map((p) => tile("folder", p.name, p.description || p.id, () => to(2, p.id)));
    show(page("New run", "Choose the project this run belongs to."), steps,
      tiles.length ? el("div", { class: "tiles" }, tiles) : empty("Create a project first.", can("projects.create") ? btn("New project", () => run(newProject), "primary", "plus") : null));
    return;
  }
  if (step === 2) {
    const all = can("config.read") ? await api("GET", "/projects/" + pid + "/connections") : [];
    const live = all.filter((c) => c.live_version);
    const tiles = live.map((c) => tile(TYPE_ICON[c.type] || "database", c.id, label(c.type) + ", " + c.fields_mapped + " fields mapped, version " + c.live_version,
      () => { if (chosen.has(c.id)) chosen.delete(c.id); else chosen.add(c.id); to(2); }, chosen.has(c.id)));
    show(page("New run", "Project " + projectName(pid) + ". Choose the connections to read; only live connections are listed.",
      [btn("Back", () => (cache.projects.length > 1 ? to(1) : go("Projects", () => viewProject(pid)))), btn("Next", () => (chosen.size ? to(3) : say("Choose at least one connection.", "bad")), "primary")]), steps,
    tiles.length ? el("div", { class: "tiles" }, tiles) : empty("This project has no live connections yet.", can("sources.manage") ? btn("Add data", () => go("Projects", () => viewProject(pid, "connections")), "primary", "plus") : null));
    return;
  }
  const reason = el("textarea", { class: "plain", maxlength: "300", "aria-label": "Reason" });
  show(page("New run", "Check and start.", [btn("Back", () => to(2)), btn("Start run", () => run(() => startRun(pid, [...chosen], null, reason.value)), "primary", "play")]), steps,
    el("table", { class: "kv" }, el("tbody", {}, el("tr", {}, el("th", {}, "Project"), el("td", {}, projectName(pid))),
      el("tr", {}, el("th", {}, "Connections"), el("td", {}, [...chosen].join(", "))),
      el("tr", {}, el("th", {}, "Rules"), el("td", {}, "The project's live rules, or the built-in defaults when none are live")))),
    el("h3", { class: "section" }, "Reason (optional)"), reason);
}
async function viewRun(pid, rid, tab) {
  tab = tab || "overview";
  const [r] = await Promise.all([api("GET", "/projects/" + pid + "/runs/" + rid), refreshCache()]);
  const going = r.status === "running" || r.status === "queued";
  const actions = [];
  if (going) actions.push(btn("Refresh", () => run(() => viewRun(pid, rid, tab)), "", "refresh"));
  if (going && can("runs.start")) actions.push(btn("Cancel run", () => run(() => cancelRun(pid, rid)), "danger"));
  if (can("runs.start") && !going && r.connections.length) actions.push(btn("Run again", () => run(() => startRun(pid, [], rid)), "", "refresh"));
  const top = page("Run " + rid, "Started " + when(r.started_at || r.created_at) + " by " + r.requested_by + (r.finished_at ? ". Finished " + when(r.finished_at) + "." : "."), actions,
    crumbs(crumb("Projects", () => go("Projects", viewProjects)), crumb(projectName(pid), () => go("Projects", () => viewProject(pid, "runs"))), rid), runState(r));
  if (going) {
    timer = setInterval(() => run(async () => {
      const now = await api("GET", "/projects/" + pid + "/runs/" + rid);
      if (now.status !== "running" && now.status !== "queued") { stopTimer(); await viewRun(pid, rid); }
    }), 4000);
    show(top, empty(r.status === "queued" ? "This run is waiting its turn" + (r.queue_position > 1 ? " (" + (r.queue_position - 1) + " ahead of it)" : " and starts next") + ". It starts by itself; you can cancel it above."
      : "This run is in progress. Its results appear here when it finishes; earlier runs stay readable meanwhile."));
    return;
  }
  if (r.status === "cancelled") { show(top, empty("This run was cancelled before it finished, so it has no results. Start it again from the button above.")); return; }
  if (r.status !== "succeeded") {
    show(top, empty(r.status === "interrupted" ? "This run was interrupted when the server restarted, so it has no results. Start it again from the button above."
      : "This run did not finish successfully (exit code " + r.exit_code + "), so it has no results. The end of its log is below."), await runLogPanel(pid, rid));
    return;
  }
  const tabList = [["overview", "Overview"], ...(can("queue.read") ? [["review", "Review queue"]] : []), ...(can("person.read") ? [["people", "People"]] : []), ["log", "Log"]];
  const base = "/projects/" + pid + "/runs/" + rid;
  let body;
  if (tab === "overview") body = runOverview(r);
  else if (tab === "review") body = await reviewQueue(pid, rid, base, "open");
  else if (tab === "log") body = await runLogPanel(pid, rid);
  else body = peopleLookup(base);
  show(top, tabs(tabList, tab, (t) => viewRun(pid, rid, t)), body);
}
async function runLogPanel(pid, rid) {
  const log = await api("GET", "/projects/" + pid + "/runs/" + rid + "/log?lines=200");
  return el("div", {}, el("h2", { class: "section" }, "Run log"),
    log.available ? el("p", { class: "hint" }, "The end of the log" + (log.truncated ? " (older lines are cut)" : "") + ". Anything that looks like personal data is removed.") : null,
    log.available ? el("pre", { class: "log" }, log.lines.join("\n")) : empty("No log was kept for this run."));
}
function runOverview(r) {
  const s = r.summary;
  const parts = [el("div", { class: "stats" }, stat("Records read", num(s.records)), stat("People found", num(s.people)), stat("Households", num(s.households)),
    stat("Pairs merged automatically", num(s.auto_merged_pairs)), stat("Items to review", num(s.review_items)), stat("Time taken", s.seconds == null ? "" : s.seconds.toFixed(1) + " s"))];
  const rowsOf = (id) => (s.records_by_source || {})[id];
  const srcRows = r.connections.length ? r.connections.map((c) => el("tr", {}, el("td", {}, c.id), el("td", {}, "Version " + c.version), el("td", { class: "num" }, num(rowsOf(c.id)))))
    : (s.sources || []).map((x) => el("tr", {}, el("td", {}, x), el("td", { class: "muted" }, "imported run"), el("td", { class: "num" }, num(rowsOf(x)))));
  const cfgRows = Object.entries(r.configs || {}).filter(([k]) => k !== "sources").map(([k, v]) => el("tr", {}, el("td", {}, label(k)),
    el("td", {}, v.source === "store" ? "Version " + v.version : "Built-in default"), el("td", { class: "mono" }, (v.hash || "").slice(0, 12))));
  parts.push(el("div", { class: "cols" }, el("div", {}, el("h2", { class: "section" }, "Sources read"), table(["Connection", "Version", ["Records read"]], srcRows)),
    el("div", {}, el("h2", { class: "section" }, "Rules used"), cfgRows.length ? table(["Configuration", "Version", "Fingerprint"], cfgRows) : empty("Not recorded."))));
  if (r.stages) {
    parts.push(el("h2", { class: "section" }, "Stages"), table(["Stage", ["Seconds"], ["Peak memory (MB)"]], r.stages.map((st) => el("tr", {}, el("td", {}, label(st.stage)),
      el("td", { class: "num" }, st.seconds), el("td", { class: "num" }, num(st.peak_memory_mb))))));
  }
  return el("div", {}, parts);
}

// ---- review queue, comparison, people -----------------------------------------------------------------------------------
async function reviewQueue(pid, rid, base, status) {
  const [summary, items] = await Promise.all([api("GET", base + "/queue/summary"), api("GET", base + "/queue?limit=50&status=" + status)]);
  const box = el("div");
  const filter = el("select", { "aria-label": "Show", onchange: () => run(async () => box.replaceWith(await reviewQueue(pid, rid, base, filter.value))) },
    [["open", "Open"], ["unsure", "Marked not sure"], ["all", "All"]].map(([v, t]) => el("option", { value: v, selected: v === status }, t)));
  const rows = items.map((i) => el("tr", { class: "click", onclick: () => run(() => viewCompare(pid, rid, base, i.person_l, i.person_r)) },
    el("td", { class: "mono" }, i.person_l), el("td", { class: "mono" }, i.person_r), el("td", { class: "num" }, i.size_l + " and " + i.size_r),
    el("td", { class: "num" }, pct(i.top_probability)), el("td", {}, i.forced ? tag("Flagged by a rule", "info") : ""), el("td", {}, i.state === "open" ? "" : tag(label(i.state)))));
  box.append(el("div", { class: "row" }, filter, el("span", { class: "muted" }, num(summary.open_now) + " open. " + num(summary.pending_approval) + " waiting for a second person.")),
    items.length ? table(["Person A", "Person B", ["Records"], ["Match score"], "Reason", "State"], rows) : empty("Nothing to review here."));
  return box;
}
function personBlock(p) {
  const rows = Object.entries(p.golden || {}).filter(([, v]) => v != null && v !== "").map(([k, v]) => el("tr", {}, el("th", {}, label(k)), el("td", {}, String(v))));
  return el("div", {}, el("h3", { class: "section" }, p.person_id + " (" + p.record_count + (p.record_count === 1 ? " record)" : " records)")),
    rows.length ? el("table", { class: "kv" }, el("tbody", {}, rows)) : empty("No values."));
}
async function viewCompare(pid, rid, base, l, r, reveal) {
  let path = base + "/people/" + l + "/compare/" + r;
  if (reveal) path += "?unmask=true&reason=" + encodeURIComponent(reveal);
  const c = await api("GET", path);
  const note = el("textarea", { class: "plain", maxlength: "300", "aria-label": "Decision note", placeholder: "Optional note, kept with the decision." });
  const decide = (kind) => run(async () => {
    const out = await api("POST", base + "/decisions/" + kind, { person_l: l, person_r: r, reason: note.value }, { "Idempotency-Key": uuid() });
    await viewRun(pid, rid, "review");
    say("Decision recorded" + (out.pending_approval ? ". It waits for a second person to approve it." : ". It applies from the project's next run."), "good");
  });
  const actions = [];
  if (can("pii.unmask") && !c.unmasked) {
    actions.push(btn("Show identifiers", () => run(async () => {
      const why = await askReason("Show identifiers", "Why do you need to see them? This is logged", 10, "Show");
      if (why) await viewCompare(pid, rid, base, l, r, why);
    })));
  }
  if (can("judgment.write")) actions.push(btn("Not sure", () => decide("unsure")), btn("Different people", () => decide("reject"), "danger"), btn("Same person", () => decide("approve"), "primary", "check"));
  const why = (list) => (list.length ? el("ul", { class: "plain" }, list.map((x) => el("li", {}, label(x.comparison) + ": " + x.level + " (" + x.bits.toFixed(1) + " bits)")))
    : el("p", { class: "muted" }, "None."));
  show(page("Compare people", "Match score " + pct(c.probability) + (c.decided_by ? ". Decided by rule: " + c.decided_by : "") +
    (c.hard_conflicts.length ? ". Conflicting identifiers: " + c.hard_conflicts.join(", ") : ""), actions,
  crumbs(crumb(projectName(pid), () => go("Projects", () => viewProject(pid))), crumb(rid, () => viewRun(pid, rid, "review")), l + " and " + r)),
  c.unmasked ? el("p", { class: "hint" }, "Identifiers are shown in full. This view is logged.") : null,
  el("div", { class: "cols" }, c.persons.map(personBlock)),
  el("h2", { class: "section" }, "Evidence"),
  el("div", { class: "cols" }, el("div", { class: "panel" }, el("h3", {}, "For the same person"), el("div", { class: "body" }, why(c.why_match))),
    el("div", { class: "panel" }, el("h3", {}, "For different people"), el("div", { class: "body" }, why(c.why_not)))),
  can("judgment.write") ? el("div", {}, el("h3", { class: "section" }, "Decision note"), note) : null);
}
function peopleLookup(base) {
  const id = el("input", { type: "text", placeholder: "Person id, for example P000000123", maxlength: "10", "aria-label": "Person id" });
  const out = el("div");
  const open = () => run(async () => {
    const v = await api("GET", base + "/people/" + encodeURIComponent(id.value.trim()));
    out.replaceChildren(personBlock(v), el("h3", { class: "section" }, "Source records"),
      table(["Source", "Record", "Name as written", "City"], v.records.map((r) => el("tr", {}, el("td", {}, r.source_id), el("td", { class: "mono" }, r.source_record_id),
        el("td", {}, r.name_as_written || ""), el("td", {}, r.city || "")))));
  });
  id.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  return el("div", {}, el("div", { class: "row" }, id, btn("Open", open, "primary")), out);
}

// ---- rules and settings of a project --------------------------------------------------------------------------------------
const CONFIGS = [["decision", "Matching rules"], ["survivorship", "Golden record"], ["settings", "Run settings"]];
async function rulesPanel(pid, name) {
  name = name || "decision";
  const base = "/projects/" + pid + "/config/" + name;
  const versions = await api("GET", base);
  const wrap = el("div");
  const again = () => run(async () => wrap.replaceWith(await rulesPanel(pid, name)));
  const result = el("div");
  const act = (v, what, title, minLength, prompt, danger) => run(async () => {
    const why = await askReason(title, prompt, minLength, title.split(" ")[0], danger);
    if (why === null) return;
    const out = await api("POST", base + "/versions/" + v.version + "/" + what, { reason: why });
    again();
    say(title + ": done" + (out.applies ? ". Applies " + out.applies + "." : "."), "good");
  });
  const dry = (v) => run(async () => {
    const r = await api("POST", base + "/versions/" + v.version + "/dry-run", {});
    const zones = ["auto_merge", "review", "reject"];
    result.replaceChildren(el("h3", { class: "section" }, "Dry run of version " + v.version + " on " + num(r.pairs) + " scored pairs of run " + r.run_id),
      table(["Decision", ["Before"], ["After"], ["Change"]], zones.map((z) => el("tr", {}, el("td", {}, label(z)), el("td", { class: "num" }, num(r.zones_before[z])),
        el("td", { class: "num" }, num(r.zones_after[z])), el("td", { class: "num" }, (r.zones_after[z] - r.zones_before[z] > 0 ? "+" : "") + num(r.zones_after[z] - r.zones_before[z]))))),
      el("p", { class: "hint" }, Object.keys(r.moved).length ? "Moved: " + Object.entries(r.moved).map(([k, n]) => k + " " + num(n)).join(", ") : "No pair changes decision."));
  });
  const back = (v) => run(async () => {
    const why = await askReason("Roll back to version " + v.version, "Why go back?", 10, "Roll back", true);
    if (why === null) return;
    await api("POST", "/projects/" + pid + "/config/" + name + "/rollback", { to_version: v.version, reason: why });
    again();
    say("Rolled back. It applies from the project's next run.", "good");
  });
  const buttons = (v) => [
    name === "decision" && can("config.propose") ? btn("Dry run", () => dry(v), "small") : null,
    can("config.approve") && v.status === "proposed" ? btn("Approve", () => act(v, "approve", "Approve version " + v.version, 0, "Note"), "small") : null,
    can("config.approve") && v.status === "proposed" ? btn("Decline", () => act(v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger") : null,
    can("config.activate") && v.status === "proposed" && v.approved_by.length ? btn("Make live", () => act(v, "activate", "Make version " + v.version + " live", 0, "Note"), "small primary") : null,
    can("config.activate") && v.status === "superseded" ? btn("Roll back to this", () => back(v), "small") : null,
  ].filter(Boolean);
  const rows = versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version), el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by),
    el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")), el("td", { class: "actions-cell" }, buttons(v))));
  wrap.append(tabs(CONFIGS, name, async (n) => wrap.replaceWith(await rulesPanel(pid, n))),
    el("p", { class: "hint" }, "Every change is proposed, approved by a different person, and made live. The project's next run uses the live version."),
    versions.length ? table(["Version", "Status", "Proposed by", "Reason", "Approved by", ""], rows) : empty("No versions yet: runs use the built-in defaults."), result);
  if (can("config.propose")) {
    const editor = el("textarea", { "aria-label": "Content as JSON", spellcheck: "false" });
    const why = el("input", { type: "text", placeholder: "Reason for this change (at least 10 characters)", maxlength: "300", "aria-label": "Reason" });
    const propose = () => run(async () => {
      let content;
      try { content = JSON.parse(editor.value); } catch (_) { throw new Error("That is not valid JSON."); }
      const out = await api("POST", base + "/propose", { content, reason: why.value });
      again();
      say("Proposed as version " + out.version + ". Another person must approve it.", "good");
    });
    wrap.append(el("h3", { class: "section" }, "Propose a new version"), el("p", { class: "hint" }, "Edit a copy of the latest version. The server checks it; nothing changes until it is approved and made live."),
      editor, el("div", { class: "row" }, why, btn("Propose", propose, "primary")));
    if (versions.length) api("GET", base + "/versions/" + versions[versions.length - 1].version).then((v) => { editor.value = JSON.stringify(v.content, null, 2); }).catch(() => {});
  }
  return wrap;
}

// ---- approvals ------------------------------------------------------------------------------------------------------------
async function decisionApprovals(pid, after) {
  const list = await api("GET", "/projects/" + pid + "/approvals");
  const act = (i, what) => run(async () => {
    const why = what === "decline" ? await askReason("Decline decision " + i.id, "Why is it declined?", 3, "Decline", true) : await askReason("Approve decision " + i.id, "Note", 0, "Approve");
    if (why === null) return;
    await api("POST", "/projects/" + pid + "/approvals/" + i.id + "/" + what, { reason: why });
    await after();
    say("Done.", "good");
  });
  return list.length ? table(["Id", "Decision", "People", "Made by", "Reason", ""], list.map((i) => el("tr", {}, el("td", {}, i.id), el("td", {}, label(i.kind)),
    el("td", { class: "mono" }, (i.persons || []).join(", ")), el("td", {}, i.actor), el("td", {}, i.reason),
    el("td", { class: "actions-cell" }, btn("Approve", () => act(i, "approve"), "small"), btn("Decline", () => act(i, "decline"), "small danger")))))
    : empty("No decisions are waiting for approval.");
}
async function viewApprovals() {
  await refreshCache();
  const parts = [page("Approvals", "Everything waiting for someone: new connection versions and steward decisions, across your projects.")];
  const waiting = [];
  for (const p of cache.projects) {
    if (!can("config.read")) break;
    for (const c of await api("GET", "/projects/" + p.id + "/connections")) if (c.latest_status === "proposed") waiting.push([p, c]);
  }
  parts.push(el("h2", { class: "section" }, "Connections"), waiting.length ? table(["Project", "Connection", "Type", "Version", "Proposed by", ""], waiting.map(([p, c]) =>
    el("tr", { class: "click", onclick: () => go("Approvals", () => viewConnection(p.id, c.id)) }, el("td", {}, p.name), el("td", {}, c.id), el("td", {}, label(c.type)),
      el("td", {}, "Version " + c.latest_version), el("td", {}, c.updated_by), el("td", { class: "actions-cell" }, btn("Open", () => go("Approvals", () => viewConnection(p.id, c.id)), "small")))))
    : empty("No connection versions are waiting."));
  if (can("judgment.approve")) {
    for (const p of cache.projects) parts.push(el("h2", { class: "section" }, "Decisions in " + p.name), await decisionApprovals(p.id, viewApprovals));
  }
  show(...parts);
}

// ---- connections (they belong to a project) -------------------------------------------------------------------------------
const TYPE_ICON = { upload: "upload", csv: "file", tsv: "file", xlsx: "sheet", excel: "sheet", parquet: "columns", gsheet: "cloud", google_sheets: "cloud", sql: "server" };
const ADD_TILES = [
  ["upload", "Upload a file", "CSV or Excel from your computer. Checked and stored encrypted."],
  ["xlsx", "Excel on the server", "An .xlsx workbook in an approved data folder."],
  ["csv", "CSV on the server", "A CSV or TSV file in an approved data folder."],
  ["sql", "SQL database", "PostgreSQL, MySQL, SQL Server and more, read-only."],
  ["gsheet", "Google Sheets", "A sheet shared by link or with an access token."],
  ["parquet", "Parquet on the server", "A Parquet file in an approved data folder."],
];
function projectConnections(p, connections) {
  const pid = p.id;
  const open = (c) => go("Projects", () => viewConnection(pid, c.id));
  const rows = connections.map((c) => el("tr", { class: "click", onclick: () => open(c) },
    el("td", {}, el("span", { class: "title-row" }, icon(TYPE_ICON[c.type] || "database"), c.id)), el("td", {}, label(c.type)), el("td", { class: "num" }, c.fields_mapped),
    el("td", {}, c.live_version ? "Version " + c.live_version : el("span", { class: "muted" }, "Not live")),
    el("td", {}, c.latest_status === "active" ? "" : statusTag(c.latest_status)),
    el("td", {}, c.last_check ? tag(c.last_check.ok ? "OK" : "Failed", c.last_check.ok ? "success" : "danger") : el("span", { class: "muted" }, "never")), el("td", {}, when(c.updated_at))));
  const copy = can("sources.manage") ? btn("Copy from another project", () => run(() => copyDialog(pid)), "", "folder") : null;
  return el("div", {},
    can("sources.manage") ? el("h2", { class: "section" }, "Add data") : null,
    can("sources.manage") ? el("div", { class: "tiles" }, ADD_TILES.map(([t, title, text]) => tile(TYPE_ICON[t], title, text, () => run(() => connectionWizard(pid, t))))) : null,
    el("div", { class: "title-row" }, el("h2", { class: "section" }, "This project's connections"), copy),
    rows.length ? table(["Connection", "Type", ["Fields"], "Live", "Pending", "Last check", "Updated"], rows)
      : empty("No connections yet. Add your first data source above; it belongs to this project only."));
}
async function copyDialog(pid) {
  const others = cache.projects.filter((x) => x.id !== pid);
  if (!others.length) { say("You have no other project to copy from.", "bad"); return; }
  const from = el("select", { "aria-label": "Project" }, others.map((x) => el("option", { value: x.id }, x.name)));
  const which = el("select", { "aria-label": "Connection" });
  const newId = el("input", { type: "text", maxlength: "40", "aria-label": "New connection id" });
  const err = el("p", { class: "bad", hidden: true });
  const load = async () => {
    which.replaceChildren();
    try {
      const list = await api("GET", "/projects/" + from.value + "/connections");
      which.append(...list.filter((c) => c.type !== "upload").map((c) => el("option", { value: c.id }, c.id + " (" + label(c.type) + ")")));
      if (!which.children.length) err.textContent = "That project has no connection that can be copied (uploaded files stay with their project).";
      else err.textContent = "";
      err.hidden = !err.textContent;
    } catch (e) { err.textContent = e.message; err.hidden = false; }
  };
  from.addEventListener("change", load);
  await load();
  const done = await dialog("Copy a connection", el("div", {}, el("p", { class: "hint" }, "The copy is independent: later changes to either one do not affect the other. It starts as a proposal in this project."),
    field("From project", from), field("Connection", which), field("New id here (leave empty to keep the same)", newId), err), "Copy", async () => {
    try {
      return await api("POST", "/projects/" + pid + "/connections/copy", { from_project: from.value, connection_id: which.value, new_id: newId.value.trim() || null });
    } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (done) { go("Projects", () => viewConnection(pid, done.id)); say("Copied as version " + done.version + ". Check the mapping, then make it live.", "good"); }
}
async function viewConnection(pid, cid) {
  const base = "/projects/" + pid + "/connections/" + cid;
  const c = await api("GET", base);
  const latest = c.versions[c.versions.length - 1];
  const live = c.versions.find((v) => v.status === "active");
  const second = c.second_approval;
  const act = (v, what, title, minLength, prompt, danger) => run(async () => {
    const why = await askReason(title, prompt, minLength, title.split(" ")[0], danger);
    if (why === null) return;
    await api("POST", base + "/versions/" + v.version + "/" + what, { reason: why });
    await viewConnection(pid, cid);
    say(title + ": done.", "good");
  });
  const buttons = (v) => [
    second && can("config.approve") && v.status === "proposed" ? btn("Approve", () => act(v, "approve", "Approve version " + v.version, 0, "Note"), "small") : null,
    can("config.approve") && v.status === "proposed" ? btn("Decline", () => act(v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger") : null,
    can("config.activate") && v.status === "proposed" && (!second || v.approved_by.length) ? btn("Make live", () => act(v, "activate", "Make version " + v.version + " live", 0, "Note"), "small primary") : null,
  ].filter(Boolean);
  const shown = c.live || c.latest;
  const mapRows = Object.entries(shown.fields || {}).map(([f, col]) => el("tr", {}, el("td", {}, label(f)), el("td", {}, typeof col === "string" ? col : JSON.stringify(col))));
  const settings = Object.entries(shown.connector).filter(([k]) => k !== "type").map(([k, v]) => el("tr", {}, el("th", {}, label(k)), el("td", { class: "mono" }, v)));
  show(page(cid, label(shown.connector.type) + " connection. " + (live ? "Live version " + live.version + "." : "Not live yet."),
    can("sources.manage") ? [btn("Edit", () => run(() => connectionWizard(pid, shown.connector.type, JSON.parse(JSON.stringify(c.latest)))), "", "sliders")] : [],
    crumbs(crumb("Projects", () => go("Projects", viewProjects)), crumb(projectName(pid), () => go("Projects", () => viewProject(pid, "connections"))), cid), statusTag(latest.status)),
  el("h2", { class: "section" }, "Versions"),
  table(["Version", "Status", "Proposed by", "Reason", "Approved by", ""], c.versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version),
    el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by), el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")), el("td", { class: "actions-cell" }, buttons(v))))),
  el("div", { class: "title-row" }, el("h2", { class: "section" }, "Health"), can("sources.manage") ? btn("Test now", () => run(async () => {
    const t = await api("POST", base + "/test");
    await viewConnection(pid, cid);
    say(t.ok ? "The source reads fine (" + num(t.rows) + " sample rows)." + driftText(t.drift) : "The source could not be read: " + t.error, t.ok ? "good" : "bad");
  }), "", "refresh") : null),
  c.health.length ? table(["When", "Check", "Version", "Result", ["Rows"], "Changes in the source", "By"], c.health.map((h) => el("tr", {}, el("td", {}, when(h.checked_at)),
    el("td", {}, h.kind === "run" ? "A run" : "Test now"), el("td", {}, h.version == null ? "" : "Version " + h.version), el("td", {}, h.ok ? tag("OK", "success") : tag("Failed", "danger")),
    el("td", { class: "num" }, h.rows == null ? "" : num(h.rows)), el("td", {}, h.ok ? driftText(h.drift) : (h.error || "")), el("td", {}, h.checked_by))))
    : empty("Not checked yet. Press Test now, or start a run."),
  el("div", { class: "cols" }, el("div", {}, el("h2", { class: "section" }, "Settings" + (live ? " (live)" : "")), settings.length ? el("table", { class: "kv" }, el("tbody", {}, settings)) : empty("None.")),
    el("div", {}, el("h2", { class: "section" }, "Field mapping" + (live ? " (live)" : "")), table(["Field", "Source column"], mapRows))));
}
const driftText = (d) => ((d && ((d.added || []).length || (d.removed || []).length))
  ? [(d.added || []).length ? "Added: " + d.added.join(", ") : "", (d.removed || []).length ? "Removed: " + d.removed.join(", ") : ""].filter(Boolean).join(". ") : "");

// The add-a-connection wizard: 1 connect (or upload), 2 map the columns, 3 preview and propose. Everything it does belongs to the project.
async function connectionWizard(pid, type, preset) {
  const opts = await api("GET", "/connectors");
  const root = "/projects/" + pid;
  const state = { source: preset || { source_id: "", connector: { type }, record_id: null, fields: {}, attributes: [] }, info: null, step: 1, uploadName: null };
  const out = el("div");
  const kind = () => state.source.connector.type;
  const render = () => {
    const steps = el("div", { class: "steps" }, ["1. Connect", "2. Map columns", "3. Preview and propose"].map((t, i) => el("span", { class: i + 1 === state.step ? "on" : "" }, t)));
    const known = ADD_TILES.find((t) => t[0] === type);
    const header = page(preset ? "Edit connection " + state.source.source_id : "Add data: " + (known ? known[1] : label(type)),
      opts.connectors[kind()] ? "Settings: " + opts.connectors[kind()].help : "", [],
      crumbs(crumb("Projects", () => go("Projects", viewProjects)), crumb(projectName(pid), () => go("Projects", () => viewProject(pid, "connections"))), preset ? state.source.source_id : "Add data"));
    if (state.step === 1) show(header, steps, connectStep());
    else if (state.step === 2) show(header, steps, mapStep());
    else show(header, steps, previewStep(), out);
  };
  const idField = () => {
    const i = el("input", { type: "text", value: state.source.source_id, maxlength: "40", placeholder: "For example bank_crm", "aria-label": "Connection id", disabled: preset ? true : null });
    i.addEventListener("input", () => { state.source.source_id = i.value.trim(); });
    return field("Connection id (stamped on every record it delivers: letters, digits, _ and -)", i);
  };
  const openSource = () => run(async () => {
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(state.source.source_id)) throw new Error("Give the connection an id of letters, digits, _ or -.");
    if (kind() === "upload" && !state.source.connector.upload_id) throw new Error("Upload a file first.");
    state.info = await api("POST", root + "/connections/inspect", { connector: state.source.connector });
    if (!Object.keys(state.source.fields).length) for (const s of state.info.suggestions) state.source.fields[s.field] = s.column;
    state.step = 2;
    render();
  });
  function connectStep() {
    const parts = [el("div", { class: "fields" }, idField())];
    if (kind() === "upload") {
      const input = el("input", { type: "file", accept: ".csv,.tsv,.txt,.xlsx", hidden: true });
      const status = el("div", {}, state.source.connector.upload_id ? "Uploaded " + (state.uploadName || state.source.connector.upload_id) + "." : "CSV, TSV or Excel (.xlsx). The first row must hold the column names.");
      const zone = el("div", { class: "dropzone" }, icon("upload"), el("strong", {}, "Drop a file here"), btn("Choose a file", () => input.click(), "small"), status);
      const send = (f) => run(async () => {
        status.textContent = "Uploading and checking " + f.name + ".";
        const r = await api("POST", root + "/uploads?filename=" + encodeURIComponent(f.name), f, { "Content-Type": "application/octet-stream" }, true);
        state.source.connector = { type: "upload", upload_id: r.upload_id };
        state.uploadName = r.filename + " (" + num(r.rows) + " rows, " + r.columns.length + " columns, stored encrypted)";
        if (!state.source.source_id) state.source.source_id = r.filename.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "_").slice(0, 40);
        render();
      });
      input.addEventListener("change", () => { if (input.files[0]) send(input.files[0]); });
      zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); if (e.dataTransfer.files[0]) send(e.dataTransfer.files[0]); });
      parts.push(zone, input);
    } else {
      const grid = el("div", { class: "fields" });
      for (const k of opts.connectors[kind()].settings) {
        const i = el("input", { type: "text", value: state.source.connector[k] || "", "aria-label": k });
        i.addEventListener("input", () => { if (i.value) state.source.connector[k] = i.value; else delete state.source.connector[k]; });
        grid.append(field(label(k), i));
      }
      parts.push(el("p", { class: "hint" }, "Passwords and tokens are never entered here. Give the name of an environment variable that starts with DEDUPE_SRC_."), grid);
    }
    parts.push(el("div", { class: "row" }, btn("Open the source and suggest a mapping", openSource, "primary", "search")));
    return el("div", {}, parts);
  }
  function mapStep() {
    const cols = state.info.columns;
    const suggested = Object.fromEntries(state.info.suggestions.map((x) => [x.field, x.column]));
    const pick = (value, onchange, text) => {
      const s = el("select", { "aria-label": text }, el("option", { value: "" }, "Not mapped"), cols.map((c) => el("option", { value: c, selected: c === value }, c)));
      s.addEventListener("change", () => onchange(s.value));
      return s;
    };
    const rows = opts.canonical_fields.map((f) => {
      const cur = state.source.fields[f];
      const simple = typeof cur === "string" || cur === undefined;
      return el("tr", {}, el("td", {}, label(f)), el("td", {}, simple ? pick(cur || "", (v) => { if (v) state.source.fields[f] = v; else delete state.source.fields[f]; }, f)
        : el("span", { class: "muted" }, "Combined columns (kept as is)")), el("td", {}, suggested[f] && suggested[f] === cur ? tag("Suggested", "info") : ""));
    });
    const attrs = el("div", {}, cols.map((c) => {
      const cb = el("input", { type: "checkbox", checked: (state.source.attributes || []).includes(c) });
      cb.addEventListener("change", () => { const set = new Set(state.source.attributes || []); if (cb.checked) set.add(c); else set.delete(c); state.source.attributes = [...set]; });
      return el("label", { class: "check" }, cb, c);
    }));
    return el("div", {},
      el("h3", { class: "section" }, "Sample rows (values are shortened)"), wide(cols, state.info.sample.map((r) => el("tr", {}, cols.map((c) => el("td", {}, r[c] == null ? "" : r[c]))))),
      el("div", { class: "cols" }, el("div", {}, el("h3", { class: "section" }, "Map columns to fields"), table(["Field", "Source column", ""], rows)),
        el("div", {}, el("h3", { class: "section" }, "Record identifier"), el("p", { class: "hint" }, "The column that identifies a record in the source. Without one, rows are numbered."),
          pick(state.source.record_id || "", (v) => { state.source.record_id = v || null; }, "Record identifier"),
          el("h3", { class: "section" }, "Other columns to keep with each record"), attrs)),
      el("div", { class: "row" }, btn("Back", () => { state.step = 1; render(); }), btn("Next: preview", () => { state.step = 3; render(); }, "primary")));
  }
  function previewStep() {
    const doPreview = () => run(async () => {
      const r = await api("POST", root + "/connections/preview", state.source);
      const cols = Object.keys(r.rows[0] || {}).filter((c) => c !== "attributes" && r.rows.some((row) => row[c] != null));
      out.replaceChildren(el("h3", { class: "section" }, "Fields filled in " + r.sample_size + " sample rows"),
        table(["Field", ["Rows filled"]], Object.entries(r.filled).map(([f, n]) => el("tr", {}, el("td", {}, label(f)), el("td", { class: "num" }, n + " of " + r.sample_size)))),
        r.rows.length ? el("h3", { class: "section" }, "Standardised sample (values are shortened)") : null,
        r.rows.length ? wide(cols, r.rows.map((row) => el("tr", {}, cols.map((c) => el("td", {}, row[c] == null ? "" : row[c]))))) : null);
    });
    const propose = () => run(async () => {
      const why = await askReason("Propose connection " + state.source.source_id, "Why add or change it?", 10, "Propose");
      if (why === null) return;
      const res = await api("POST", root + "/connections", { connection: state.source, reason: why });
      go("Projects", () => viewConnection(pid, res.id));
      say("Saved as version " + res.version + ". " + (res.second_approval ? "Another person approves it and makes it live; then runs can use it." : "Make it live when you are ready; then runs can use it."), "good");
    });
    setTimeout(doPreview, 0);
    return el("div", { class: "row" }, btn("Back", () => { state.step = 2; render(); }), btn("Preview again", doPreview, "", "refresh"), btn("Save connection", propose, "primary", "check"));
  }
  render();
}

// ---- results: take a run's output out as a file or into a database ------------------------------------------------------------
async function resultsPanel(p, runs) {
  const done = runs.filter((r) => r.status === "succeeded");
  if (!done.length) return empty("Results appear here after a run has finished. Start a run first.", can("runs.start") ? btn("New run", () => go("Projects", () => newRun(p.id)), "primary", "play") : null);
  const pick = el("select", { "aria-label": "Run" }, done.map((r) => el("option", { value: r.id }, r.id + ", " + when(r.finished_at || r.created_at) + " (" + num(r.summary.people) + " people)")));
  const box = el("div");
  const load = () => run(async () => box.replaceChildren(...(await datasetCards(p, pick.value))));
  pick.addEventListener("change", load);
  const wrap = el("div", {}, el("p", { class: "hint" }, "Results contain personal data. Every download or database write asks for a reason and is recorded in the project's activity."),
    el("div", { class: "row" }, field("Results of run", pick)), box, await destinationsPanel(p));
  await load();
  return wrap;
}
async function datasetCards(p, rid) {
  const info = await api("GET", "/projects/" + p.id + "/runs/" + rid + "/datasets");
  return info.datasets.map((d) => el("div", { class: "panel" }, el("h3", {}, d.title + " (" + num(d.rows) + " rows)"),
    el("div", { class: "body" }, el("p", {}, d.description), el("p", { class: "muted" }, "Columns: " + d.columns.join(", ")),
      el("div", { class: "row" }, ...["xlsx", "csv", "parquet"].map((f) => btn(info.formats[f], () => run(() => downloadResults(p.id, rid, d.name, f)), "small", "upload")),
        can("sources.manage") || can("results.export") ? btn("Write to a database", () => run(() => databaseDialog(p, rid, d)), "small", "database") : null))));
}
async function downloadResults(pid, rid, dataset, format) {
  const why = await askReason("Take out these results", "Why do you need this file? It is recorded.", 10, "Download");
  if (why === null) return;
  const r = await fetch("/projects/" + pid + "/runs/" + rid + "/export", { method: "POST", credentials: "omit", cache: "no-store",
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
  const dests = (await api("GET", "/projects/" + p.id + "/destinations")).filter((d) => d.live_version);
  if (!dests.length) { say("This project has no live database destination yet. Add one below, then make it live.", "bad"); return; }
  const dest = el("select", { "aria-label": "Destination" }, dests.map((d) => el("option", { value: d.id }, d.id + (d.schema_name ? " (schema " + d.schema_name + ")" : ""))));
  const table = el("input", { type: "text", maxlength: "63", "aria-label": "Table name", placeholder: "for example dedupe_" + dataset.name });
  const mode = el("select", { "aria-label": "What to do" }, [["create", "Create a new table"], ["append", "Add rows to an existing table"], ["replace", "Replace the rows of an existing table"]]
    .map(([v, t]) => el("option", { value: v }, t)));
  const schema = el("input", { type: "text", maxlength: "63", "aria-label": "Schema", placeholder: "leave empty for the destination's default" });
  const confirm = el("input", { type: "checkbox" });
  const confirmRow = el("label", { class: "check", hidden: true }, confirm, el("span", {}, "I understand this deletes the rows the table has now"));
  const why = el("input", { type: "text", maxlength: "300", "aria-label": "Reason", placeholder: "Why? At least 10 characters. It is recorded." });
  const err = el("p", { class: "bad", hidden: true });
  mode.addEventListener("change", () => { confirmRow.hidden = mode.value !== "replace"; });
  const result = await dialog("Write " + dataset.title.toLowerCase() + " to a database", el("div", {}, el("p", { class: "hint" }, num(dataset.rows) + " rows. All of it is written, or none: a refused row leaves the table as it was."),
    field("Destination", dest), field("Table name", table), field("What to do", mode), confirmRow, field("Schema (optional)", schema), field("Reason", why), err), "Write", async () => {
    try {
      return await api("POST", "/projects/" + p.id + "/runs/" + rid + "/export/database", { destination: dest.value, dataset: dataset.name, table: table.value.trim(), mode: mode.value,
        schema_name: schema.value.trim() || null, confirm_replace: confirm.checked, reason: why.value.trim() });
    } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (result) say("Wrote " + num(result.rows) + " rows to " + (result.schema ? result.schema + "." : "") + result.table + ".", "good");
}
async function destinationsPanel(p) {
  const list = await api("GET", "/projects/" + p.id + "/destinations");
  const second = p.second_approval;
  const detail = async (d) => (d.latest_status === "proposed" ? api("GET", "/projects/" + p.id + "/destinations/" + d.id) : null);
  const details = await Promise.all(list.map(detail));
  const act = (d, v, what, title, minLength, prompt, danger) => run(async () => {
    const reason = await askReason(title, prompt, minLength, title.split(" ")[0], danger);
    if (reason === null) return;
    await api("POST", "/projects/" + p.id + "/destinations/" + d.id + "/versions/" + v.version + "/" + what, { reason });
    await viewProject(p.id, "results");
    say(title + ": done.", "good");
  });
  const rows = list.map((d, i) => {
    const v = details[i] ? details[i].versions[details[i].versions.length - 1] : null;
    const buttons = v ? [
      second && can("config.approve") ? btn("Approve", () => act(d, v, "approve", "Approve version " + v.version, 0, "Note"), "small") : null,
      can("config.approve") ? btn("Decline", () => act(d, v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger") : null,
      can("config.activate") && (!second || v.approved_by.length) ? btn("Make live", () => act(d, v, "activate", "Make version " + v.version + " live", 0, "Note"), "small primary") : null,
    ].filter(Boolean) : [];
    return el("tr", {}, el("td", {}, d.id), el("td", { class: "mono" }, d.url_env), el("td", {}, d.schema_name || ""), el("td", {}, d.live_version ? "Version " + d.live_version : el("span", { class: "muted" }, "Not live")),
      el("td", {}, d.latest_status === "active" ? "" : statusTag(d.latest_status)), el("td", { class: "actions-cell" }, buttons,
        can("sources.manage") ? btn("Test", () => run(async () => {
          const t = await api("POST", "/projects/" + p.id + "/destinations/" + d.id + "/test");
          say(t.ok ? "The database can be reached." : "The database could not be reached: " + t.error, t.ok ? "good" : "bad");
        }), "small") : null));
  });
  return el("div", {}, el("div", { class: "title-row" }, el("h2", { class: "section" }, "Database destinations"),
    can("sources.manage") ? btn("Add a destination", () => run(() => destinationDialog(p)), "", "plus") : null),
  el("p", { class: "hint" }, "A destination is the name of an environment variable (starting with DEDUPE_DST_) that holds a database address. The address and password are set on the server by whoever runs it, never typed here."),
  rows.length ? table(["Destination", "Environment variable", "Schema", "Live", "Pending", ""], rows) : empty("No destinations yet. Add one to write results into a database."));
}
async function destinationDialog(p) {
  const id = el("input", { type: "text", maxlength: "40", "aria-label": "Destination id", placeholder: "for example warehouse" });
  const env = el("input", { type: "text", maxlength: "70", "aria-label": "Environment variable", placeholder: "DEDUPE_DST_WAREHOUSE" });
  const schema = el("input", { type: "text", maxlength: "63", "aria-label": "Default schema" });
  const why = el("input", { type: "text", maxlength: "300", "aria-label": "Reason", placeholder: "Why? At least 10 characters." });
  const err = el("p", { class: "bad", hidden: true });
  const res = await dialog("Add a database destination", el("div", {}, field("Destination id (letters, digits, - and _)", id), field("Environment variable that holds the database address", env),
    field("Default schema (optional)", schema), field("Reason", why), err), "Save", async () => {
    try {
      return await api("POST", "/projects/" + p.id + "/destinations", { destination: { destination_id: id.value.trim(), url_env: env.value.trim().toUpperCase(), schema_name: schema.value.trim() || null }, reason: why.value.trim() });
    } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (res) { await viewProject(p.id, "results"); say("Saved as version " + res.version + ". " + (res.second_approval ? "Another person approves it, then it can be made live." : "Make it live to use it."), "good"); }
}

// ---- schedules: run the project's connections by themselves -----------------------------------------------------------------
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const ZONES = ["UTC", "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago", "America/Los_Angeles", "Australia/Sydney"];
function scheduleWhen(s) {
  const t = s.at_time;
  if (s.frequency === "hourly") return "Every hour at :" + t.slice(3) + " (" + s.timezone + ")";
  if (s.frequency === "weekly") return "Every " + WEEKDAYS[s.weekday] + " at " + t + " (" + s.timezone + ")";
  return "Every day at " + t + " (" + s.timezone + ")";
}
async function schedulesPanel(p, connections) {
  const list = await api("GET", "/projects/" + p.id + "/schedules");
  const again = () => viewProject(p.id, "schedules");
  const change = (s, patch, done) => run(async () => { await api("PATCH", "/projects/" + p.id + "/schedules/" + s.id, patch); await again(); say(done, "good"); });
  const remove = (s) => run(async () => {
    const ok = await dialog("Delete schedule " + s.name + "?", el("p", {}, "Runs it already started are kept. Nothing will start from it any more."), "Delete", async () => true, true);
    if (!ok) return;
    await api("DELETE", "/projects/" + p.id + "/schedules/" + s.id);
    await again();
    say("Schedule deleted.", "good");
  });
  const manage = can("runs.start");
  const rows = list.map((s) => el("tr", {}, el("td", {}, el("strong", {}, s.name)), el("td", {}, s.connections.join(", ")), el("td", {}, scheduleWhen(s)),
    el("td", {}, s.enabled ? when(s.next_run_at) : el("span", { class: "muted" }, "Paused")),
    el("td", {}, s.last_run_id ? el("a", { href: "#", onclick: (e) => { e.preventDefault(); go("Projects", () => viewRun(p.id, s.last_run_id)); } }, label(s.last_status || "")) : (s.last_status ? label(s.last_status) : "")),
    el("td", {}, s.enabled ? tag("On", "success") : el("span", {}, tag("Paused", "danger"), s.paused_reason ? el("div", { class: "muted" }, s.paused_reason) : null)),
    el("td", { class: "actions-cell" }, manage ? [
      s.enabled ? btn("Pause", () => change(s, { enabled: false }, "Schedule paused."), "small") : btn("Resume", () => change(s, { enabled: true }, "Schedule resumed."), "small primary"),
      btn("Delete", () => remove(s), "small danger")] : null)));
  return el("div", {}, el("div", { class: "title-row" }, el("h2", { class: "section" }, "Scheduled runs"),
    manage ? btn("New schedule", () => run(() => scheduleDialog(p, connections)), "primary", "plus") : null),
  el("p", { class: "hint" }, "A schedule starts an ordinary run of the chosen connections at the times you set, using each connection's live version at that moment. It never starts a new run while its last one is still going, runs once (not once per missed slot) after the server was off, and pauses itself after three failed runs in a row."),
  rows.length ? table(["Name", "Connections", "When", "Next run", "Last run", "State", ""], rows) : empty("No schedules yet.", manage ? btn("New schedule", () => run(() => scheduleDialog(p, connections)), "primary", "plus") : null));
}
async function scheduleDialog(p, connections) {
  const live = connections.filter((c) => c.live_version);
  if (!live.length) { say("This project has no live connection to run yet.", "bad"); return; }
  const name = el("input", { type: "text", maxlength: "100", "aria-label": "Name", placeholder: "for example Nightly sweep" });
  const picks = checkList(live.map((c) => ({ name: c.id, description: label(c.type) + ", version " + c.live_version })), new Set(live.map((c) => c.id)), () => true, false);
  const freq = el("select", { "aria-label": "How often" }, [["daily", "Every day"], ["weekly", "Every week"], ["hourly", "Every hour"]].map(([v, t]) => el("option", { value: v }, t)));
  const at = el("input", { type: "text", maxlength: "5", value: "02:00", "aria-label": "Time", placeholder: "02:00" });
  const day = el("select", { "aria-label": "Day of the week" }, WEEKDAYS.map((d, i) => el("option", { value: String(i) }, d)));
  const dayField = field("Day of the week", day);
  dayField.hidden = true;
  const zones = el("datalist", { id: "zones" }, ZONES.map((z) => el("option", { value: z })));
  const browserZone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (_) { return "UTC"; } })();
  const zone = el("input", { type: "text", list: "zones", value: browserZone, maxlength: "40", "aria-label": "Time zone" });
  const err = el("p", { class: "bad", hidden: true });
  freq.addEventListener("change", () => { dayField.hidden = freq.value !== "weekly"; });
  const created = await dialog("New schedule", el("div", {}, field("Name", name), el("h3", { class: "section" }, "Connections to read"), picks.node, field("How often", freq), dayField,
    field(freq.value === "hourly" ? "Minutes past the hour (the hour is ignored), 24-hour clock" : "Time, 24-hour clock (for example 02:30)", at), field("Time zone (a name such as Asia/Kolkata)", zone), zones, err), "Save schedule", async () => {
    try {
      return await api("POST", "/projects/" + p.id + "/schedules", { name: name.value.trim(), connections: picks.values(), frequency: freq.value, at_time: at.value.trim(),
        weekday: freq.value === "weekly" ? Number(day.value) : null, timezone: zone.value.trim() });
    } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (created) { await viewProject(p.id, "schedules"); say("Schedule saved. Next run: " + when(created.next_run_at) + ".", "good"); }
}

// ---- audit log ------------------------------------------------------------------------------------------------------------
async function viewAudit(event) {
  const rows = await api("GET", "/security/events?limit=200" + (event ? "&event=" + event : ""));
  const filter = el("select", { "aria-label": "Event", onchange: () => run(() => viewAudit(filter.value)) }, el("option", { value: "" }, "All events"),
    ["auth_failed", "login", "permission_denied", "rate_limited", "pii_unmasked", "run_started", "run_finished", "run_cancelled", "export", "config_change", "judgment_change", "server_error", "startup"]
      .map((e) => el("option", { value: e, selected: e === event }, label(e))));
  const verify = () => run(async () => {
    const v = await api("GET", "/security/verify");
    say(v.ok ? "The log is intact: the hash chain verifies." : "The log chain is BROKEN. Investigate before trusting it.", v.ok ? "good" : "bad");
  });
  show(page("Audit log", "Security-relevant events, append-only and hash-chained. Personal data values are never written here.", [filter, btn("Verify integrity", verify, "", "shield")]),
    rows.length ? table(["Time", "Event", "Who", "What", "Object", ["Status"]], rows.slice().reverse().map((e) => el("tr", {}, el("td", {}, when(e.created_at)), el("td", {}, label(e.event)),
      el("td", {}, e.actor), el("td", {}, (e.method ? e.method + " " : "") + (e.route || (e.detail && e.detail.action) || "")), el("td", { class: "mono" }, e.object_ref),
      el("td", { class: "num" }, e.status == null ? "" : e.status)))) : empty("No events."));
}

// ---- password, users and roles ---------------------------------------------------------------------------------------------
async function viewPassword(forced) {
  const cur = el("input", { type: "password", autocomplete: "current-password", "aria-label": "Current password" });
  const nw = el("input", { type: "password", autocomplete: "new-password", "aria-label": "New password" });
  const again = el("input", { type: "password", autocomplete: "new-password", "aria-label": "Repeat the new password" });
  const err = el("p", { class: "bad", hidden: true });
  const save = async () => {
    err.hidden = true;
    if (nw.value !== again.value) { err.textContent = "The two new passwords differ."; err.hidden = false; return; }
    try {
      const r = await api("POST", "/auth/password", { current_password: cur.value, new_password: nw.value });
      token = r.token;
      me = await api("GET", "/me");
      cur.value = ""; nw.value = ""; again.value = "";
      buildNav();
      say("Your password was changed.", "good");
    } catch (e) { err.textContent = e.message; err.hidden = false; }
  };
  show(page(forced ? "Choose a new password" : "Change password",
    forced ? "You signed in with a temporary password. Choose your own before you continue." : "Changing it signs you out of any other session.", [btn("Save password", save, "primary")]),
  el("div", {}, field("Current password", cur), field("New password (12 to 128 characters, not your username)", nw), field("Repeat the new password", again), err));
}
function checkList(items, chosen, allowed, readOnly) {
  const boxes = items.map((it) => {
    const box = el("input", { type: "checkbox", value: it.name, checked: chosen.has(it.name), disabled: readOnly || !allowed(it) });
    return [box, el("label", { class: "check" }, box, el("span", {}, el("strong", { class: readOnly ? "" : "mono" }, it.name), el("span", { class: "muted" }, it.description || "")))];
  });
  return { node: el("div", { class: "checks" }, boxes.map((b) => b[1])), values: () => boxes.filter((b) => b[0].checked).map((b) => b[0].value) };
}
const heldBy = (perms) => { const mine = new Set(me.permissions); return perms.every((p) => mine.has(p)); };
const roleTags = (roles) => el("span", { class: "row" }, roles.map((r) => tag(label(r))));
async function viewUsers() {
  const [users, roles] = await Promise.all([api("GET", "/users"), api("GET", "/roles")]);
  const state = (u) => (u.disabled ? tag("Disabled", "danger") : u.locked ? tag("Locked", "danger") : u.must_change_password ? tag("Temporary password", "info") : tag("Active", "success"));
  const rows = users.map((u) => el("tr", {}, el("td", {}, u.username), el("td", {}, u.display_name), el("td", {}, roleTags(u.roles)), el("td", {}, state(u)),
    el("td", {}, u.last_login_at ? when(u.last_login_at) : "Never"),
    el("td", {}, el("div", { class: "row" }, btn("Edit", () => run(() => userDialog(u, roles))), u.username === me.sub ? null : btn("Reset password", () => run(() => resetDialog(u)))))));
  show(page("Users", "People who can sign in with a username and password. Roles decide what each person may do.", [btn("New user", () => run(() => userDialog(null, roles)), "primary", "plus")]),
    rows.length ? table(["Username", "Name", "Roles", "Status", "Last sign-in", ""], rows) : empty("No users yet."));
}
async function userDialog(u, catalogue) {
  const roles = catalogue.roles.filter((r) => heldBy(r.permissions) || (u && u.roles.includes(r.name)));
  const self = !!u && u.username === me.sub;
  const name = el("input", { type: "text", maxlength: "64", "aria-label": "Username", value: u ? u.username : "", disabled: !!u });
  const full = el("input", { type: "text", maxlength: "100", "aria-label": "Name", value: u ? u.display_name : "" });
  const pass = el("input", { type: "password", autocomplete: "new-password", "aria-label": "Temporary password" });
  const off = el("input", { type: "checkbox", checked: !!(u && u.disabled), disabled: self });
  const picks = checkList(roles.map((r) => ({ name: r.name, description: r.description })), new Set(u ? u.roles : []),
    (it) => heldBy(roles.find((r) => r.name === it.name).permissions), self);
  const err = el("p", { class: "bad", hidden: true });
  const body = el("div", {}, field("Username (lower-case letters, digits, . - _)", name), field("Name (optional)", full),
    u ? null : field("Temporary password (12 to 128 characters; the person must change it at first sign-in)", pass),
    el("h3", { class: "section" }, "Roles"), picks.node,
    u ? el("label", { class: "check" }, off, el("span", {}, "Disabled: this person cannot sign in")) : null, err);
  const done = await dialog(u ? "Edit " + u.username : "New user", body, u ? "Save" : "Create user", async () => {
    try {
      if (u) {
        const patch = { display_name: full.value.trim() };
        if (!self) { patch.roles = picks.values(); patch.disabled = off.checked; }
        await api("PATCH", "/users/" + u.username, patch);
      } else {
        await api("POST", "/users", { username: name.value.trim().toLowerCase(), display_name: full.value.trim(), password: pass.value, roles: picks.values() });
      }
      return true;
    } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (done) { await viewUsers(); say(u ? "User saved." : "User created. Give them the temporary password in person; they must change it at first sign-in.", "good"); }
}
async function resetDialog(u) {
  const pass = el("input", { type: "password", autocomplete: "new-password", "aria-label": "New temporary password" });
  const err = el("p", { class: "bad", hidden: true });
  const body = el("div", {}, el("p", { class: "hint" }, "Their current sessions end and they must choose a new password at the next sign-in."),
    field("New temporary password (12 to 128 characters)", pass), err);
  const done = await dialog("Reset password for " + u.username, body, "Reset password", async () => {
    try { await api("POST", "/users/" + u.username + "/password", { password: pass.value }); return true; } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (done) { await viewUsers(); say("Password reset.", "good"); }
}
async function viewRoles() {
  const data = await api("GET", "/roles");
  const rows = data.roles.map((r) => el("tr", {}, el("td", {}, label(r.name)), el("td", {}, r.builtin ? tag("Built-in") : tag("Custom", "info")), el("td", {}, r.description),
    el("td", { class: "num" }, r.permissions.length), el("td", { class: "num" }, r.users),
    el("td", {}, el("div", { class: "row" }, btn(r.builtin ? "View" : "Edit", () => run(() => roleDialog(r, data.permissions))),
      r.builtin ? null : btn("Delete", () => run(() => deleteRole(r)), "danger")))));
  show(page("Roles", "A role is a named set of permissions. Built-in roles are fixed; create your own to give people exactly the access they need. Nobody can give a role more than they hold themselves.",
    [btn("New role", () => run(() => roleDialog(null, data.permissions)), "primary", "plus")]), table(["Role", "Type", "Description", ["Permissions"], ["People"], ""], rows));
}
async function roleDialog(role, catalogue) {
  const fixed = !!(role && role.builtin);
  const name = el("input", { type: "text", maxlength: "40", "aria-label": "Role name" });
  const desc = el("input", { type: "text", maxlength: "300", "aria-label": "Description", value: role ? role.description : "", disabled: fixed });
  const picks = checkList(catalogue, new Set(role ? role.permissions : []), (it) => heldBy([it.name]), fixed);
  const err = el("p", { class: "bad", hidden: true });
  const body = el("div", {}, role ? null : field("Name (lower-case letters, digits, - or _)", name), field("Description", desc),
    el("h3", { class: "section" }, "What this role may do"), picks.node, err);
  const title = role ? (fixed ? label(role.name) + " (built-in, read only)" : "Edit " + role.name) : "New role";
  const done = await dialog(title, body, fixed ? "Close" : "Save role", async () => {
    if (fixed) return true;
    try {
      if (role) await api("PUT", "/roles/" + role.name, { description: desc.value.trim(), permissions: picks.values() });
      else await api("POST", "/roles", { name: name.value.trim().toLowerCase(), description: desc.value.trim(), permissions: picks.values() });
      return true;
    } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
  });
  if (done && !fixed) { await viewRoles(); say("Role saved. It applies to everyone holding it from their next request.", "good"); }
}
async function deleteRole(r) {
  const ok = await dialog("Delete role " + r.name + "?", el("p", {}, "This cannot be undone. A role that people or projects still use cannot be deleted."), "Delete role", async () => true, true);
  if (!ok) return;
  await api("DELETE", "/roles/" + r.name);
  await viewRoles();
  say("Role deleted.", "good");
}

initTheme();
initSearch();
initLogin();
$("theme").addEventListener("click", () => setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
$("login-form").addEventListener("submit", signIn);
$("signout").addEventListener("click", () => signOut(""));
$("password-btn").addEventListener("click", () => go("", () => viewPassword(false)));
