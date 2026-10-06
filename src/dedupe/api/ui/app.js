"use strict";
// Dedupe workspace: projects, runs, connections, review and approvals on top of the API.
// Safety rules for this file: text is only ever put on the page with textContent (never innerHTML), the access token lives in a variable
// (never in storage or the URL), every request goes to this same origin, and the only thing kept in browser storage is the colour theme.
let token = null;
let me = null;
let timer = null;
const cache = { projects: [], connections: [], runs: [] };
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
async function signIn(ev) {
  ev.preventDefault();
  token = $("token").value.trim();
  $("token").value = "";
  const fail = $("login-error");
  fail.hidden = true;
  try {
    me = await api("GET", "/me");
  } catch (e) {
    token = null;
    fail.textContent = e.message === "unauthorised" ? "That token was not accepted." : e.message;
    fail.hidden = false;
    return;
  }
  $("login").hidden = true;
  $("app").hidden = false;
  $("avatar").textContent = (me.username || "?").slice(0, 1).toUpperCase();
  $("who").textContent = me.username;
  $("who").setAttribute("title", me.roles.join(", "));
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
  ["", [["Home", "any", "home", () => viewHome()], ["Projects", "any", "folder", () => viewProjects()], ["Runs", "runs.read", "play", () => viewRuns()],
    ["Connections", "config.read", "database", () => viewConnections()]]],
  ["Governance", [["Approvals", "config.approve", "check", () => viewApprovals()], ["Audit log", "logs.read", "shield", () => viewAudit()]]],
];
const NEW_MENU = [
  ["New project", "runs.start", "folder", () => newProject(), "Projects"], ["New run", "runs.start", "play", () => newRun(), "Runs"],
  ["Add a connection", "sources.manage", "database", () => viewConnections(), "Connections"], ["Upload a file", "sources.manage", "upload", () => connectionWizard("upload"), "Connections"],
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
  const [projects, connections, runs] = await Promise.all([api("GET", "/projects"), can("config.read") ? api("GET", "/connections") : [],
    can("runs.read") ? api("GET", "/runs?limit=50") : []]);
  Object.assign(cache, { projects, connections, runs });
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
      ...cache.connections.filter((c) => c.id.toLowerCase().includes(q)).map((c) => ["database", c.id, "Connection", () => viewConnection(c.id)]),
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
  const stats = [stat("Projects", num(h.projects), () => go("Projects", viewProjects)), stat("Connections", num(h.connections), can("config.read") ? () => go("Connections", viewConnections) : null)];
  if (h.open_review_items != null) stats.push(stat("Items to review", num(h.open_review_items), latest ? () => go("Runs", () => viewRun(latest.project_id, latest.id, "review")) : null));
  if (h.pending_approvals != null) stats.push(stat("Waiting for approval", num(h.pending_approvals), () => go("Approvals", viewApprovals)));
  const start = [];
  if (can("runs.start")) start.push(tile("folder", "Create a project", "A project keeps its own decisions, person IDs and rules.", () => go("Projects", newProject)));
  if (can("sources.manage")) start.push(tile("database", "Add data", "Connect a file, sheet or database, or upload a file.", () => go("Connections", viewConnections)));
  if (can("runs.start")) start.push(tile("play", "Start a run", "Match and merge the records of chosen connections.", () => go("Runs", () => newRun())));
  if (latest && can("queue.read")) start.push(tile("users", "Review matches", "Open the review queue of the latest finished run.", () => go("Runs", () => viewRun(latest.project_id, latest.id, "review"))));
  show(page("Welcome, " + me.username, "Find duplicate customers across your sources, review them and keep one golden record per person."),
    el("div", { class: "stats" }, stats),
    start.length ? el("h2", { class: "section" }, "Get started") : null, start.length ? el("div", { class: "tiles" }, start) : null,
    can("runs.read") ? el("h2", { class: "section" }, "Recent runs") : null,
    can("runs.read") ? runsTable(h.recent_runs, true) : null);
}
function runsTable(runs, withProject) {
  if (!runs.length) return empty("No runs yet.", can("runs.start") ? btn("Start a run", () => go("Runs", () => newRun()), "primary", "play") : null);
  const cols = [...(withProject ? ["Project"] : []), "Run", "Status", "Connections", ["Records"], ["People"], ["To review"], "Started", "By"];
  return table(cols, runs.map((r) => el("tr", { class: "click", onclick: () => go("Runs", () => viewRun(r.project_id, r.id)) },
    withProject ? el("td", {}, projectName(r.project_id)) : null, el("td", { class: "mono" }, r.id), el("td", {}, statusTag(r.status)),
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
  const create = can("runs.start") ? btn("New project", () => run(newProject), "primary", "plus") : null;
  show(page("Projects", "Each project keeps its own steward decisions, person IDs and rules.", [create]),
    rows.length ? table(["Project", ["Runs"], "Latest run", "Last activity", "Created by"], rows) : empty("No projects yet.", create));
}
async function newProject() {
  const name = el("input", { type: "text", maxlength: "120", "aria-label": "Project name" });
  const id = el("input", { type: "text", maxlength: "40", "aria-label": "Project id" });
  const desc = el("textarea", { class: "plain", maxlength: "500", "aria-label": "Description" });
  let touched = false;
  name.addEventListener("input", () => { if (!touched) id.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40); });
  id.addEventListener("input", () => { touched = true; });
  const err = el("p", { class: "bad", hidden: true });
  const created = await dialog("New project", el("div", {}, field("Name", name), field("Id (used in addresses: lower-case letters, digits and -)", id), field("Description (optional)", desc), err),
    "Create project", async () => {
      try { return await api("POST", "/projects", { id: id.value.trim(), name: name.value.trim(), description: desc.value.trim() }); } catch (e) { err.textContent = e.message; err.hidden = false; return undefined; }
    });
  if (created) { await refreshCache(); go("Projects", () => viewProject(created.id)); }
}
async function viewProject(pid, tab) {
  tab = tab || "runs";
  const [p, runs] = await Promise.all([api("GET", "/projects/" + pid), can("runs.read") ? api("GET", "/projects/" + pid + "/runs") : []]);
  await refreshCache();
  const actions = can("runs.start") ? [p.latest_run && p.latest_run.connections.length ? btn("Run again", () => run(() => startRun(pid, [], p.latest_run.id)), "", "refresh") : null,
    btn("New run", () => go("Runs", () => newRun(pid)), "primary", "play")] : [];
  const tabList = [["runs", "Runs"], ["rules", "Rules and settings"], ...(can("judgment.approve") ? [["approvals", "Approvals"]] : [])];
  let body;
  if (tab === "runs") body = runsTable(runs, false);
  else if (tab === "rules") body = await rulesPanel(pid);
  else body = await decisionApprovals(pid, () => viewProject(pid, "approvals"));
  show(page(p.name, p.description || "Project " + p.id, actions, crumbs(crumb("Projects", () => go("Projects", viewProjects)), p.name)),
    tabs(tabList, tab, (t) => viewProject(pid, t)), body);
}

// ---- runs --------------------------------------------------------------------------------------------------------------
async function viewRuns() {
  await refreshCache();
  show(page("Runs", "Every run of every project. Open a run to review its results.", can("runs.start") ? [btn("New run", () => go("Runs", () => newRun()), "primary", "play")] : []),
    runsTable(cache.runs, true));
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
      tiles.length ? el("div", { class: "tiles" }, tiles) : empty("Create a project first.", btn("New project", () => run(newProject), "primary", "plus")));
    return;
  }
  if (step === 2) {
    const live = cache.connections.filter((c) => c.live_version);
    const tiles = live.map((c) => tile(TYPE_ICON[c.type] || "database", c.id, label(c.type) + ", " + c.fields_mapped + " fields mapped, version " + c.live_version,
      () => { if (chosen.has(c.id)) chosen.delete(c.id); else chosen.add(c.id); to(2); }, chosen.has(c.id)));
    show(page("New run", "Project " + projectName(pid) + ". Choose the connections to read; only approved, live connections are listed.",
      [btn("Back", () => to(1)), btn("Next", () => (chosen.size ? to(3) : say("Choose at least one connection.", "bad")), "primary")]), steps,
    tiles.length ? el("div", { class: "tiles" }, tiles) : empty("No live connections yet.", can("sources.manage") ? btn("Add data", () => go("Connections", viewConnections), "primary", "plus") : null));
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
  if (can("runs.start") && !going && r.connections.length) actions.push(btn("Run again", () => run(() => startRun(pid, [], rid)), "", "refresh"));
  const top = page("Run " + rid, "Started " + when(r.started_at || r.created_at) + " by " + r.requested_by + (r.finished_at ? ". Finished " + when(r.finished_at) + "." : "."), actions,
    crumbs(crumb("Projects", () => go("Projects", viewProjects)), crumb(projectName(pid), () => go("Projects", () => viewProject(pid))), rid), statusTag(r.status));
  if (going) {
    timer = setInterval(() => run(async () => {
      const now = await api("GET", "/projects/" + pid + "/runs/" + rid);
      if (now.status !== "running" && now.status !== "queued") { stopTimer(); await viewRun(pid, rid); }
    }), 4000);
    show(top, empty("This run is in progress. Its results appear here when it finishes; earlier runs stay readable meanwhile."));
    return;
  }
  if (r.status !== "succeeded") { show(top, empty("This run did not finish successfully (exit code " + r.exit_code + "), so it has no results. The run log is kept on the server.")); return; }
  const tabList = [["overview", "Overview"], ...(can("queue.read") ? [["review", "Review queue"]] : []), ...(can("person.read") ? [["people", "People"]] : [])];
  const base = "/projects/" + pid + "/runs/" + rid;
  let body;
  if (tab === "overview") body = runOverview(r);
  else if (tab === "review") body = await reviewQueue(pid, rid, base, "open");
  else body = peopleLookup(base);
  show(top, tabs(tabList, tab, (t) => viewRun(pid, rid, t)), body);
}
function runOverview(r) {
  const s = r.summary;
  const parts = [el("div", { class: "stats" }, stat("Records read", num(s.records)), stat("People found", num(s.people)), stat("Households", num(s.households)),
    stat("Pairs merged automatically", num(s.auto_merged_pairs)), stat("Items to review", num(s.review_items)), stat("Time taken", s.seconds == null ? "" : s.seconds.toFixed(1) + " s"))];
  const srcRows = r.connections.length ? r.connections.map((c) => el("tr", {}, el("td", {}, c.id), el("td", {}, "Version " + c.version)))
    : (s.sources || []).map((x) => el("tr", {}, el("td", {}, x), el("td", { class: "muted" }, "imported run")));
  const cfgRows = Object.entries(r.configs || {}).filter(([k]) => k !== "sources").map(([k, v]) => el("tr", {}, el("td", {}, label(k)),
    el("td", {}, v.source === "store" ? "Version " + v.version : "Built-in default"), el("td", { class: "mono" }, (v.hash || "").slice(0, 12))));
  parts.push(el("div", { class: "cols" }, el("div", {}, el("h2", { class: "section" }, "Sources read"), table(["Connection", "Version"], srcRows)),
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
  const parts = [page("Approvals", "Everything waiting for a second person: new connection versions and steward decisions.")];
  const waiting = cache.connections.filter((c) => c.latest_status === "proposed");
  parts.push(el("h2", { class: "section" }, "Connections"), waiting.length ? table(["Connection", "Type", "Version", "Status", "Proposed by", ""], waiting.map((c) =>
    el("tr", { class: "click", onclick: () => go("Connections", () => viewConnection(c.id)) }, el("td", {}, c.id), el("td", {}, label(c.type)), el("td", {}, "Version " + c.latest_version),
      el("td", {}, statusTag(c.latest_status)), el("td", {}, c.updated_by), el("td", { class: "actions-cell" }, btn("Open", () => go("Connections", () => viewConnection(c.id)), "small")))))
    : empty("No connection versions are waiting."));
  if (can("judgment.approve")) {
    for (const p of cache.projects) parts.push(el("h2", { class: "section" }, "Decisions in " + p.name), await decisionApprovals(p.id, viewApprovals));
  }
  show(...parts);
}

// ---- connections ----------------------------------------------------------------------------------------------------------
const TYPE_ICON = { upload: "upload", csv: "file", tsv: "file", xlsx: "sheet", excel: "sheet", parquet: "columns", gsheet: "cloud", google_sheets: "cloud", sql: "server" };
const ADD_TILES = [
  ["upload", "Upload a file", "CSV or Excel from your computer. Checked and stored encrypted."],
  ["xlsx", "Excel on the server", "An .xlsx workbook in an approved data folder."],
  ["csv", "CSV on the server", "A CSV or TSV file in an approved data folder."],
  ["sql", "SQL database", "PostgreSQL, MySQL, SQL Server and more, read-only."],
  ["gsheet", "Google Sheets", "A sheet shared by link or with an access token."],
  ["parquet", "Parquet on the server", "A Parquet file in an approved data folder."],
];
async function viewConnections() {
  await refreshCache();
  const rows = cache.connections.map((c) => el("tr", { class: "click", onclick: () => go("Connections", () => viewConnection(c.id)) },
    el("td", {}, el("span", { class: "title-row" }, icon(TYPE_ICON[c.type] || "database"), c.id)), el("td", {}, label(c.type)), el("td", { class: "num" }, c.fields_mapped),
    el("td", {}, c.live_version ? "Version " + c.live_version : el("span", { class: "muted" }, "Not live")),
    el("td", {}, c.latest_status === "active" ? "" : statusTag(c.latest_status)), el("td", {}, when(c.updated_at)), el("td", {}, c.updated_by)));
  show(page("Connections", "Saved sources with their field mapping, shared by all projects. Each version is approved by a second person before runs can use it."),
    can("sources.manage") ? el("h2", { class: "section" }, "Add data") : null,
    can("sources.manage") ? el("div", { class: "tiles" }, ADD_TILES.map(([t, title, text]) => tile(TYPE_ICON[t], title, text, () => run(() => connectionWizard(t))))) : null,
    el("h2", { class: "section" }, "Your connections"),
    rows.length ? table(["Connection", "Type", ["Fields"], "Live", "Pending", "Updated", "By"], rows) : empty("No connections yet."));
}
async function viewConnection(cid) {
  const c = await api("GET", "/connections/" + cid);
  const latest = c.versions[c.versions.length - 1];
  const live = c.versions.find((v) => v.status === "active");
  const act = (v, what, title, minLength, prompt, danger) => run(async () => {
    const why = await askReason(title, prompt, minLength, title.split(" ")[0], danger);
    if (why === null) return;
    await api("POST", "/connections/" + cid + "/versions/" + v.version + "/" + what, { reason: why });
    await viewConnection(cid);
    say(title + ": done.", "good");
  });
  const buttons = (v) => [
    can("config.approve") && v.status === "proposed" ? btn("Approve", () => act(v, "approve", "Approve version " + v.version, 0, "Note"), "small") : null,
    can("config.approve") && v.status === "proposed" ? btn("Decline", () => act(v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger") : null,
    can("config.activate") && v.status === "proposed" && v.approved_by.length ? btn("Make live", () => act(v, "activate", "Make version " + v.version + " live", 0, "Note"), "small primary") : null,
  ].filter(Boolean);
  const shown = c.live || c.latest;
  const mapRows = Object.entries(shown.fields || {}).map(([f, col]) => el("tr", {}, el("td", {}, label(f)), el("td", {}, typeof col === "string" ? col : JSON.stringify(col))));
  const settings = Object.entries(shown.connector).filter(([k]) => k !== "type").map(([k, v]) => el("tr", {}, el("th", {}, label(k)), el("td", { class: "mono" }, v)));
  show(page(cid, label(shown.connector.type) + " connection. " + (live ? "Live version " + live.version + "." : "Not live yet."),
    can("sources.manage") ? [btn("Edit", () => run(() => connectionWizard(shown.connector.type, JSON.parse(JSON.stringify(c.latest)))), "", "sliders")] : [],
    crumbs(crumb("Connections", () => go("Connections", viewConnections)), cid), statusTag(latest.status)),
  el("h2", { class: "section" }, "Versions"),
  table(["Version", "Status", "Proposed by", "Reason", "Approved by", ""], c.versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version),
    el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by), el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")), el("td", { class: "actions-cell" }, buttons(v))))),
  el("div", { class: "cols" }, el("div", {}, el("h2", { class: "section" }, "Settings" + (live ? " (live)" : "")), settings.length ? el("table", { class: "kv" }, el("tbody", {}, settings)) : empty("None.")),
    el("div", {}, el("h2", { class: "section" }, "Field mapping" + (live ? " (live)" : "")), table(["Field", "Source column"], mapRows))));
}

// The add-a-connection wizard: 1 connect (or upload), 2 map the columns, 3 preview and propose.
async function connectionWizard(type, preset) {
  const opts = await api("GET", "/connections/options");
  const state = { source: preset || { source_id: "", connector: { type }, record_id: null, fields: {}, attributes: [] }, info: null, step: 1, uploadName: null };
  const out = el("div");
  const kind = () => state.source.connector.type;
  const render = () => {
    const steps = el("div", { class: "steps" }, ["1. Connect", "2. Map columns", "3. Preview and propose"].map((t, i) => el("span", { class: i + 1 === state.step ? "on" : "" }, t)));
    const known = ADD_TILES.find((t) => t[0] === type);
    const header = page(preset ? "Edit connection " + state.source.source_id : "Add data: " + (known ? known[1] : label(type)),
      opts.connectors[kind()] ? "Settings: " + opts.connectors[kind()].help : "", [],
      crumbs(crumb("Connections", () => go("Connections", viewConnections)), preset ? state.source.source_id : "Add data"));
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
    state.info = await api("POST", "/connections/inspect", { connector: state.source.connector });
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
        const r = await api("POST", "/uploads?filename=" + encodeURIComponent(f.name), f, { "Content-Type": "application/octet-stream" }, true);
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
      const r = await api("POST", "/connections/preview", state.source);
      const cols = Object.keys(r.rows[0] || {}).filter((c) => c !== "attributes" && r.rows.some((row) => row[c] != null));
      out.replaceChildren(el("h3", { class: "section" }, "Fields filled in " + r.sample_size + " sample rows"),
        table(["Field", ["Rows filled"]], Object.entries(r.filled).map(([f, n]) => el("tr", {}, el("td", {}, label(f)), el("td", { class: "num" }, n + " of " + r.sample_size)))),
        r.rows.length ? el("h3", { class: "section" }, "Standardised sample (values are shortened)") : null,
        r.rows.length ? wide(cols, r.rows.map((row) => el("tr", {}, cols.map((c) => el("td", {}, row[c] == null ? "" : row[c]))))) : null);
    });
    const propose = () => run(async () => {
      const why = await askReason("Propose connection " + state.source.source_id, "Why add or change it?", 10, "Propose");
      if (why === null) return;
      const res = await api("POST", "/connections", { connection: state.source, reason: why });
      go("Connections", () => viewConnection(res.id));
      say("Proposed as version " + res.version + ". Another person approves it and makes it live; then runs can use it.", "good");
    });
    setTimeout(doPreview, 0);
    return el("div", { class: "row" }, btn("Back", () => { state.step = 2; render(); }), btn("Preview again", doPreview, "", "refresh"), btn("Propose", propose, "primary", "check"));
  }
  render();
}

// ---- audit log ------------------------------------------------------------------------------------------------------------
async function viewAudit(event) {
  const rows = await api("GET", "/security/events?limit=200" + (event ? "&event=" + event : ""));
  const filter = el("select", { "aria-label": "Event", onchange: () => run(() => viewAudit(filter.value)) }, el("option", { value: "" }, "All events"),
    ["auth_failed", "permission_denied", "rate_limited", "pii_unmasked", "run_started", "run_finished", "config_change", "judgment_change", "server_error", "startup"]
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

initTheme();
initSearch();
$("theme").addEventListener("click", () => setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
$("login-form").addEventListener("submit", signIn);
$("signout").addEventListener("click", () => signOut(""));
