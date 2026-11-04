"use strict";
// The frame around every screen: sign in and out, the menu, page building blocks and search. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
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
  (mode === "local" ? $("username") : $("token")).focus();
}
async function signIn(ev) {
  ev.preventDefault();
  const fail = $("login-error");
  fail.hidden = true;
  let mustChange = false;
  $("signin").disabled = true;
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
  } finally { $("signin").disabled = false; }
  $("login").hidden = true;
  $("app").hidden = false;
  $("who").replaceChildren(me.username, icon("down"));
  $("menu-who").replaceChildren(el("strong", {}, me.username), me.roles.map(label).join(", "));
  if (mustChange) { $("nav").replaceChildren(); await run(() => viewPassword(true)); return; }       // nothing else works until the password is changed
  buildNav();
  arrivedAt = readAddress() || "/";
  go(() => dispatch(readAddress() || "/")).finally(() => { arrivedAt = null; });                 // back to the screen that was open (the address survives a reload; the sign-in does not)
}
function signOut(msg) {
  token = null; me = null;
  stopTimer();
  pageKeys = null;
  $("app").hidden = true;
  $("login").hidden = false;
  $("overlay").hidden = true;
  $("app").inert = false;
  $("nav").replaceChildren(); $("view").replaceChildren(); say("");
  const fail = $("login-error");
  fail.hidden = !msg;
  fail.textContent = msg || "";
  initLogin();
}

// ---- navigation --------------------------------------------------------------------------------------------------------
// Four places: the daily work (Home, Projects, Review) and, for the people who run the workspace, Admin. Everything else lives inside a project.
const NAV = [["Home", "any", "home", "/"], ["Projects", "any", "folder", "/projects"], ["Review", "queue.read", "inbox", "/review"], ["Admin", "admin", "shield", "/admin"]];
const canNav = (perm) => (perm === "admin" ? ADMIN_PAGES.some((x) => can(x[1])) : can(perm));
function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }
function go(fn) {
  stopTimer();
  pageKeys = null;
  closeSearch();
  focusNext = true;
  return run(fn);
}
function buildNav() {
  $("nav").replaceChildren(...NAV.filter((i) => canNav(i[1])).map(([name, , ic, path]) =>
    el("a", { href: "#" + path, "data-name": name, onclick: (e) => { e.preventDefault(); go(() => dispatch(path)); } }, icon(ic), name)));
}
// "More": the less used and the destructive actions of a page or a row, out of the way of the main button. items: [text, action, danger?];
// the empty ones (null) are left out, and with none left there is no menu. A real menu: arrow keys move, Escape and a click elsewhere close it.
function moreMenu(items, name, text, ic) {
  const list = items.filter(Boolean);
  if (!list.length) return null;
  const opener = el("button", { type: "button", class: "small", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": (text || "More") + (name ? " for " + name : "") },
    ic ? icon(ic) : null, text || "More");
  const menu = el("div", { class: "more-menu", role: "menu", hidden: true });
  const wrap = el("div", { class: "more" }, opener, menu);
  // the menu is placed on the screen under its button (fixed), so a table that scrolls sideways does not cut it off; scrolling closes it
  const toggle = (open) => {
    menu.hidden = !open; opener.setAttribute("aria-expanded", String(open));
    if (open) {
      const at = opener.getBoundingClientRect();
      menu.style.top = Math.round(at.bottom + 2) + "px";
      menu.style.right = Math.round(document.documentElement.clientWidth - at.right) + "px";
      menu.firstChild.focus();
      setTimeout(() => { document.addEventListener("click", away); window.addEventListener("scroll", shut, true); window.addEventListener("resize", shut); });
    } else {
      document.removeEventListener("click", away); window.removeEventListener("scroll", shut, true); window.removeEventListener("resize", shut);
    }
  };
  const shut = () => toggle(false);
  const away = (e) => { if (!wrap.contains(e.target)) toggle(false); };
  opener.addEventListener("click", (e) => { e.stopPropagation(); toggle(menu.hidden); });
  for (const [text, action, danger] of list) {
    menu.append(el("button", { type: "button", role: "menuitem", class: danger ? "danger" : null, onclick: (e) => { e.stopPropagation(); toggle(false); run(action); } }, text));
  }
  menu.addEventListener("keydown", (e) => {
    const all = [...menu.children];
    const i = all.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); toggle(false); opener.focus(); return; }
    const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: all.length - 1 }[e.key];
    if (to !== undefined) { e.preventDefault(); all[(to + all.length) % all.length].focus(); }
  });
  return wrap;
}

// ---- page building blocks ------------------------------------------------------------------------------------------------
function show(...nodes) {
  $("view").replaceChildren(...nodes.flat(3).filter((n) => n != null && n !== false));      // flat: an array would otherwise print as text
  const h1 = $("view").querySelector("h1");
  document.title = (h1 ? h1.textContent + " - " : "") + "Aurionpro Dedupe";
  if (focusNext && h1) { h1.setAttribute("tabindex", "-1"); h1.focus(); }
  focusNext = false;
}
let sectionIcon = "home";            // the menu symbol of the screen's section, shown beside its title (set by here())
function page(title, subtitle, actions, crumbsNode, extra, mark) {
  return el("div", { class: "page-head" },
    el("div", {}, crumbsNode ? el("div", { class: "crumbs" }, crumbsNode) : null,
      el("div", { class: "title-row" }, mark || el("span", { class: "page-icon" }, icon(sectionIcon)), el("h1", {}, title), extra || null), subtitle ? el("p", {}, subtitle) : null),
    el("div", { class: "actions" }, actions || []));
}
const crumb = (text, path) => el("button", { type: "button", onclick: link(path) }, text);
const crumbs = (...parts) => el("nav", { "aria-label": "Breadcrumb" }, parts.flatMap((p, i) => (i ? [" / ", p] : [p])));
const head = (cols) => el("thead", {}, el("tr", {}, cols.map((h) => el("th", { scope: "col", class: Array.isArray(h) ? "num" : h === "" ? "" : null },
  Array.isArray(h) ? h[0] : h === "" ? el("span", { class: "sr-only" }, "Actions") : h))));
// Tables scroll sideways inside their box on a narrow screen instead of pushing the whole page sideways.
const table = (cols, rows, cls) => el("div", { class: "scroll" + (cls ? " " + cls : "") }, el("table", {}, head(cols), el("tbody", {}, rows)));
const wide = (cols, rows) => table(cols, rows, "wide");
const kv = (pairs) => el("table", { class: "kv" }, el("tbody", {}, pairs.filter(Boolean).map(([k, v]) => el("tr", {}, el("th", { scope: "row" }, k), el("td", {}, v)))));
const empty = (text, ...buttons) => el("div", { class: "empty" }, el("span", { class: "empty-icon" }, icon("layers")), el("div", {}, text),
  buttons.filter(Boolean).length ? el("div", { class: "row" }, buttons) : null);
// A status is its word alone ("Succeeded", "Failed"): the word carries the meaning, the colour only helps (no boxes, ticks or crosses).
const tag = (text, kind) => el("span", { class: "tag" + (kind ? " " + kind : "") }, text);
// Logos of the products a connection reads from (served from /ui/logos; decorative: the product's name is always written next to them).
const TYPE_LOGO = { upload: "upload", csv: "csv", tsv: "tsv", xlsx: "xlsx", excel: "xlsx", xls: "xls", json: "json", jsonl: "jsonl", xml: "xml", fixed_width: "fixedwidth",
  parquet: "parquet", avro: "avro", orc: "orc", folder: "folder", postgres: "postgresql", mysql: "mysql", mariadb: "mariadb", sqlserver: "sqlserver", azure_sql: "azuresql",
  oracle: "oracle", db2: "db2", rds: "aws", aurora: "aws", sqlite: "sqlite", duckdb: "duckdb", sql: "sql", snowflake: "snowflake", redshift: "aws", bigquery: "bigquery",
  databricks: "databricks", sftp: "sftp", s3: "aws", gcs: "gcs", azure_blob: "azure", salesforce: "salesforce", hubspot: "hubspot", dynamics365: "dynamics",
  rest_api: "restapi", mongodb: "mongodb", gsheet: "googlesheets", google_sheets: "googlesheets" };
const TYPE_TITLE = { upload: "Uploaded file", csv: "CSV", tsv: "TSV", xlsx: "Excel", excel: "Excel", xls: "Old Excel (.xls)", json: "JSON", jsonl: "JSON Lines", xml: "XML",
  fixed_width: "Fixed-width text", parquet: "Parquet", avro: "Avro", orc: "ORC", folder: "Folder of files", postgres: "PostgreSQL", mysql: "MySQL", mariadb: "MariaDB",
  sqlserver: "SQL Server", azure_sql: "Azure SQL", oracle: "Oracle", db2: "IBM Db2", rds: "Amazon RDS", aurora: "Amazon Aurora", sqlite: "SQLite", duckdb: "DuckDB",
  sql: "Database (URL)", snowflake: "Snowflake", redshift: "Amazon Redshift", bigquery: "BigQuery", databricks: "Databricks", sftp: "SFTP", s3: "Amazon S3",
  gcs: "Google Cloud Storage", azure_blob: "Azure Blob Storage", salesforce: "Salesforce", hubspot: "HubSpot", dynamics365: "Dynamics 365", rest_api: "REST API",
  mongodb: "MongoDB", gsheet: "Google Sheets", google_sheets: "Google Sheets" };
const typeTitle = (t) => TYPE_TITLE[t] || label(t);
const logo = (type, size) => el("img", { class: "logo-chip" + (size ? " " + size : ""), src: "/ui/logos/" + (TYPE_LOGO[type] || "database") + ".svg", alt: "", width: "20", height: "20" });
const typeCell = (type) => el("span", { class: "type-cell" }, logo(type), typeTitle(type));
function field(text, input) {
  if (!input.id) input.id = "f" + (++uid);
  if (input.matches && input.matches("input, select, textarea")) input.removeAttribute("aria-label");      // the visible label names it
  return el("div", { class: "field" }, el("label", { for: input.id }, text), input);
}
// A clickable row: the first cell holds a real button (keyboard and screen readers), the rest of the row is a mouse shortcut to the same place.
function linkRow(first, onopen, ...cells) {
  const open = () => go(onopen);
  const b = el("button", { type: "button", class: "link", onclick: (e) => { e.stopPropagation(); open(); } }, first);
  return el("tr", { class: "click", onclick: (e) => { if (!e.target.closest("button, a, input, label")) open(); } }, el("td", {}, b), cells);
}
// A symbol for each kind of number, chosen from the label's words, so every stat card has one without each screen naming it.
const STAT_ICONS = [[/review|waiting|inbox|queue/i, "inbox"], [/household/i, "home2"], [/people|person|customer|member|user/i, "users"],
  [/record|row/i, "database"], [/pair|match|merge/i, "merge"], [/source|connection/i, "plug"], [/run/i, "play"], [/project/i, "folder"],
  [/second|time|duration|minute|ago|last/i, "clock"], [/golden/i, "star"], [/precision|recall|rate|%|score|quality/i, "chart"], [/decision|approv/i, "check"],
  [/schedule/i, "calendar"], [/rule/i, "sliders"]];
const statIcon = (text) => (STAT_ICONS.find(([re]) => re.test(String(text))) || [null, "grid"])[1];
const stat = (text, value, onclick) => (onclick ? el("button", { type: "button", class: "stat", onclick }, el("span", { class: "label" }, icon(statIcon(text)), text), el("span", { class: "value" }, value))
  : el("div", { class: "stat" }, el("span", { class: "label" }, icon(statIcon(text)), text), el("span", { class: "value" }, value)));
const tile = (ic, title, text, onclick, pressed) => el("button", { type: "button", class: "tile", onclick, "aria-pressed": pressed == null ? null : String(!!pressed) },
  ic instanceof Node ? ic : el("span", { class: "tile-icon" }, icon(ic)), el("span", {}, el("strong", {}, title), el("span", { class: "muted" }, text)));
const TAB_ICON = { setup: "layers", overview: "grid", connections: "plug", runs: "play", schedules: "calendar", results: "download", decisions: "list", rules: "sliders",
  activity: "clock", access: "users", settings: "sliders", review: "inbox", people: "users", log: "terminal", decision: "merge", survivorship: "star" };
// Tabs with the keyboard pattern people expect: Left/Right move between tabs, Home/End jump, the panel is labelled by its tab.
function tabs(list, current, pick) {
  const bar = el("div", { class: "tabs", role: "tablist" }, list.map(([key, text]) =>
    el("button", { type: "button", role: "tab", id: "tab-" + key, "aria-selected": key === current ? "true" : "false", "aria-controls": "tabpanel",
      tabindex: key === current ? "0" : "-1", onclick: () => run(() => pick(key)) }, TAB_ICON[key] ? icon(TAB_ICON[key]) : null, text)));
  bar.addEventListener("keydown", (e) => {
    const all = [...bar.querySelectorAll("[role=tab]")];
    const i = all.indexOf(document.activeElement);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: all.length - 1 }[e.key];
    if (to === undefined || i < 0) return;
    e.preventDefault();
    all[(to + all.length) % all.length].focus();
  });
  return bar;
}
const tabPanel = (current, body) => el("div", { role: "tabpanel", id: "tabpanel", "aria-labelledby": "tab-" + current }, body);
// The steps of a flow. With `goTo`, the other steps are buttons, so people can move back and forth by clicking them.
const steps = (names, on, goTo) => el("ol", { class: "steps", "aria-label": "Steps" }, names.map((t, i) => el("li", {},
  goTo && i + 1 !== on ? el("button", { type: "button", class: "step", onclick: () => goTo(i + 1) }, (i + 1) + ". " + t)
    : el("span", { class: i + 1 === on ? "on" : "", "aria-current": i + 1 === on ? "step" : null }, (i + 1) + ". " + t))));
async function refreshCache() {
  const [projects, runs] = await Promise.all([api("GET", "/projects"), can("runs.read") ? api("GET", "/runs?limit=50") : []]);
  Object.assign(cache, { projects, runs });
}
const projectName = (pid) => (cache.projects.find((p) => p.id === pid) || { name: pid }).name;

// ---- search ------------------------------------------------------------------------------------------------------------
function closeSearch() { $("search-results").hidden = true; $("search").setAttribute("aria-expanded", "false"); }
function initSearch() {
  $("search-wrap").prepend(icon("search"));
  const box = $("search");
  const out = $("search-results");
  const hint = $("search-hint");
  hint.textContent = /Mac|iPhone|iPad/.test(navigator.platform || "") ? "Cmd + K" : "Ctrl + K";
  let active = -1;
  const options = () => [...out.querySelectorAll("[role=option]")];
  const mark = (i) => {
    const all = options();
    active = all.length ? (i + all.length) % all.length : -1;
    all.forEach((o, k) => o.setAttribute("aria-selected", String(k === active)));
    if (active >= 0) box.setAttribute("aria-activedescendant", all[active].id); else box.removeAttribute("aria-activedescendant");
  };
  box.addEventListener("focus", () => { if (me) run(refreshCache, true); });
  box.addEventListener("input", () => {
    const q = box.value.trim().toLowerCase();
    if (!q) { closeSearch(); return; }
    const hits = [
      ...cache.projects.filter((p) => (p.name + " " + p.id).toLowerCase().includes(q)).map((p) => ["folder", p.name, "Project", P(p.id)]),
      ...cache.runs.filter((r) => (r.id + " " + r.project_id + " " + projectName(r.project_id)).toLowerCase().includes(q))
        .map((r) => ["play", r.id + " in " + projectName(r.project_id), "Run", R(r.project_id, r.id)]),
    ].slice(0, 12);
    out.replaceChildren(...(hits.length ? hits.map(([ic, text, kind, path], i) => el("div", { role: "option", id: "hit-" + i, "aria-selected": "false",
      onclick: () => { box.value = ""; go(() => dispatch(path)); } }, icon(ic), text, el("span", { class: "kind" }, kind)))
      : [el("div", { class: "none" }, "No project or run matches. People are found inside a run's People tab.")]));
    out.hidden = false;
    box.setAttribute("aria-expanded", "true");
    mark(-1);
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { box.value = ""; closeSearch(); box.blur(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (!out.hidden) mark(active + (e.key === "ArrowDown" ? 1 : -1)); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); options()[active].click(); }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && me && $("overlay").hidden) { e.preventDefault(); box.focus(); }
  });
  document.addEventListener("click", (e) => { if (!$("search-wrap").contains(e.target)) closeSearch(); });
}
