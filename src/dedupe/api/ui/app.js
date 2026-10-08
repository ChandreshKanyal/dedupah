"use strict";
// Aurionpro dedupe workspace: projects, runs, connections, review and approvals on top of the API.
// Safety rules for this file: text is only ever put on the page with textContent (never innerHTML), the access token lives in a variable
// (never in storage or the address), every request goes to this same origin, and the only thing kept in browser storage is the colour theme.
// The address bar holds only which screen is open (#/projects/demo/runs/R...): it is read in one place, matched against fixed patterns with every
// part checked, and never put on the page as markup.
let token = null;
let me = null;
let timer = null;
let mode = "token";                // how people sign in: "token" (identity provider or test token) or "local" (username and password)
let pageKeys = null;               // keyboard shortcuts of the current screen; cleared whenever another screen opens
let focusNext = false;             // move focus to the new screen's heading (after a navigation, not after a background refresh)
let uid = 0;
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
  download: "M12 4v12 M7 11l5 5 5-5 M4 20h16",
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
  inbox: "M4 13h4l2 3h4l2-3h4 M4 13l2-8h12l2 8v6H4z",
  list: "M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01",
  trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
  edit: "M4 20h4L20 8l-4-4L4 16z",
  undo: "M9 14 4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3",
  split: "M6 4v5a4 4 0 0 0 4 4h4a4 4 0 0 0 4-4V4 M12 13v7",
  next: "M9 6l6 6-6 6",
  prev: "M15 6l-6 6 6 6",
  x: "M6 6l12 12 M18 6 6 18",
  alert: "M12 4 2.5 20h19z M12 10v4 M12 17h.01",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 11v5 M12 8h.01",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 7v5l3 2",
  key: "M14 10a4 4 0 1 0-1.2 2.9L20 20 M17 17l2-2 M15 15l2-2",
  lock: "M6 11h12v9H6z M8 11V8a4 4 0 0 1 8 0v3",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1 M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  external: "M14 4h6v6 M20 4l-9 9 M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  plug: "M9 3v5 M15 3v5 M6 8h12v3a6 6 0 0 1-12 0z M12 17v4",
  star: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z",
  layers: "M12 3 3 8l9 5 9-5z M3 13l9 5 9-5 M3 17.5l9 5 9-5",
  chart: "M4 20V10 M10 20V4 M16 20v-7 M22 20H2",
  calendar: "M4 6h16v14H4z M4 10h16 M8 3v5 M16 3v5",
  user: "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M4 21a8 8 0 0 1 16 0",
  filter: "M3 5h18l-7 8v6l-4 2v-8z",
  grid: "M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z",
  book: "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z M4 19V5 M8 7h7",
  zap: "M13 2 4 14h7l-1 8 9-12h-7z",
  merge: "M6 3v6a6 6 0 0 0 6 6h0a6 6 0 0 1 6 6 M18 3v4 M6 21v-4",
  home2: "M3 10.5 12 3l9 7.5 M5 9v11h14V9 M10 20v-5h4v5",
  terminal: "M4 5h16v14H4z M8 10l3 2-3 2 M13 15h4",
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
const plural = (n, one, many) => num(n) + " " + (n === 1 ? one : many);
// One word for each state, everywhere: what people see never depends on which screen they are on.
const STATUS_LABEL = { active: "Live", proposed: "Waiting for approval", approved: "Approved, not live", superseded: "Replaced", declined: "Declined",
  succeeded: "Succeeded", failed: "Failed", interrupted: "Interrupted", cancelled: "Cancelled", running: "Running", queued: "Queued", deleted: "Deleted",
  open: "Open", unsure: "Not sure", decided: "Decided", in_force: "In force", waiting_for_run: "Waits for the next run",
  pending_approval: "Waits for a second person", undone: "Undone" };
const STATUS_KIND = { active: "success", succeeded: "success", in_force: "success", failed: "danger", declined: "danger", interrupted: "danger",
  proposed: "info", approved: "info", running: "info", queued: "info", waiting_for_run: "info", pending_approval: "warn", unsure: "warn" };
const KIND_LABEL = { same_person: "Same person", different_person: "Different people", unsure: "Not sure", split: "Split", field_override: "Corrected a field" };
const statusTag = (s) => tag(STATUS_LABEL[s] || label(s), STATUS_KIND[s]);

// ---- theme (the one thing kept in browser storage) ---------------------------------------------------------------------
function setTheme(t, keep) {
  document.documentElement.setAttribute("data-theme", t);
  if (keep) { try { localStorage.setItem(THEME_KEY, t); } catch (_) { /* storage may be blocked */ } }
  const b = $("theme");
  b.replaceChildren(icon(t === "dark" ? "sun" : "moon"));
  b.setAttribute("title", t === "dark" ? "Switch to light" : "Switch to dark");
  b.setAttribute("aria-label", t === "dark" ? "Switch to the light theme" : "Switch to the dark theme");
}
function initTheme() {
  let t = null;
  try { t = localStorage.getItem(THEME_KEY); } catch (_) { /* default */ }
  if (t !== "light" && t !== "dark") t = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  setTheme(t, false);
}

// ---- messages and dialogs ----------------------------------------------------------------------------------------------
// One message at a time, fixed to the corner so it is seen wherever the page is scrolled. It may carry one action (for example Undo).
// replaceChildren prints a null or false as text: fill() leaves them out, like el() does
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(3).filter((k) => k != null && k !== false));
const add = (node, ...kids) => node.append(...kids.flat(3).filter((k) => k != null && k !== false));
function say(msg, kind, action) {
  const b = $("banner");
  b.hidden = !msg;
  b.className = kind || "";
  b.setAttribute("role", kind === "bad" ? "alert" : "status");
  if (!msg) { b.replaceChildren(); return; }
  b.replaceChildren(...[el("span", { class: "msg" }, msg), action ? btn(action.label, action.fn, "small", action.icon) : null,
    btn("Dismiss", () => say(""), "small ghost")].filter(Boolean));          // replaceChildren would print a null as the text "null"
}
// A button that cannot be pressed twice: it is disabled while its action runs, so a double click never starts two runs or writes rows twice.
function btn(text, onclick, cls, ic, attrs) {
  const b = el("button", { type: "button", class: cls || "", ...(attrs || {}) }, ic ? icon(ic) : null, text);
  b.addEventListener("click", async (e) => {
    if (b.getAttribute("aria-busy") === "true") return;
    b.setAttribute("aria-busy", "true");
    b.disabled = true;
    try { await onclick(e); } catch (err) { if (err && err.message !== "unauthorised") say(err.message, "bad"); }
    finally { b.disabled = false; b.removeAttribute("aria-busy"); }
  });
  return b;
}
const errBox = () => el("p", { class: "bad", role: "alert", hidden: true });
function fail(box, msg, input) {
  box.textContent = msg;
  box.hidden = !msg;
  if (input) input.setAttribute("aria-invalid", msg ? "true" : "false");
}
function dialog(title, body, confirmLabel, onConfirm, danger) {
  return new Promise((resolve) => {
    const box = $("dialog");
    const opener = document.activeElement;
    const close = (v) => {
      $("overlay").hidden = true;
      $("app").inert = false;
      box.replaceChildren();
      document.removeEventListener("keydown", onKey);
      resolve(v);
      if (opener && opener.isConnected) setTimeout(() => opener.focus(), 0);      // after its button is enabled again
    };
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    const ok = btn(confirmLabel, async () => { const v = await onConfirm(); if (v !== undefined) close(v); }, danger ? "danger" : "primary");
    box.replaceChildren(el("h2", { id: "dialog-title" }, title), body, el("div", { class: "buttons" }, btn("Cancel", () => close(null)), ok));
    $("overlay").hidden = false;
    $("app").inert = true;                       // the page behind cannot be reached with the keyboard or a screen reader while the dialog is open
    document.addEventListener("keydown", onKey);
    (box.querySelector("textarea, input:not([type=checkbox]):not([disabled]), select") || ok).focus();
  });
}
function askReason(title, prompt, minLength, confirmLabel, danger) {
  const area = el("textarea", { class: "plain", maxlength: "300" });
  const err = errBox();
  const body = el("div", {}, field(prompt + (minLength ? " (at least " + minLength + " characters)" : " (optional)"), area), err);
  return dialog(title, body, confirmLabel || "Confirm", () => {
    const v = area.value.trim();
    if (v.length < minLength) { fail(err, "Please write at least " + minLength + " characters.", area); return undefined; }
    return v;
  }, danger);
}
const confirmDialog = (title, text, confirmLabel, danger) => dialog(title, el("p", {}, text), confirmLabel, async () => true, danger);
// Showing identifiers: straight away (every view is logged), or after a written reason when the deployment requires one.
// Resolves to the reason, `true` (shown without one) or null (cancelled); unmaskQuery turns that into the query string.
const revealIdentifiers = (title) => (me && me.unmask_reason_required ? askReason(title, "Why do you need to see them? This is logged", 10, "Show") : Promise.resolve(true));
const unmaskQuery = (why) => "unmask=true" + (typeof why === "string" && why ? "&reason=" + encodeURIComponent(why) : "");

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
let loadingTimer = null;
// Runs a screen change or an action: shows "Loading" when the server is slow, and turns any failure into a message. A background refresh
// (quiet) keeps the message that is showing.
async function run(fn, quiet) {
  if (!quiet) say("");
  clearTimeout(loadingTimer);
  if (!quiet) loadingTimer = setTimeout(() => { $("loading").textContent = "Loading..."; $("view").setAttribute("aria-busy", "true"); }, 400);
  try { await fn(); } catch (e) { if (e.message !== "unauthorised") say(e.message, "bad"); }
  finally {
    if (!quiet) { clearTimeout(loadingTimer); $("loading").textContent = ""; $("view").removeAttribute("aria-busy"); }
  }
}

// ---- addresses: Back, Forward, reload and shared links open the same screen -------------------------------------------------
let quietAddress = null;
const PART = /^[A-Za-z0-9_.-]{1,64}$/;
function readAddress() { return window.location.hash.replace(/^#/, ""); }
function writeAddress(path) { if (readAddress() !== path) { quietAddress = path; window.location.hash = path; } }
function dispatch(path) {
  const parts = (path || "/").split("/").filter(Boolean);
  if (!parts.every((x) => PART.test(x))) return viewHome();
  const [a, b, c, d, e, f, g] = parts;
  if (!a) return viewHome();
  if (a === "projects") {
    if (!b) return viewProjects();
    if (c === "runs" && d) {
      if (e === "compare" && f && g) return viewCompare(b, d, f, g);
      if (e === "people" && f) return viewPerson(b, d, f);
      return viewRun(b, d, e);
    }
    if (c === "connections" && d) return viewConnection(b, d);
    if (c === "add-data") return addDataPage(b);
    if (c === "new-run") return newRun(b, 2);
    return viewProject(b, c);
  }
  const top = { runs: viewRuns, "new-run": () => newRun(), review: () => (b === "decisions" ? viewDecisions() : viewInbox()), approvals: viewApprovals,
    audit: () => viewAudit(), users: viewUsers, roles: viewRoles, password: () => viewPassword(false) }[a];
  return top ? top() : viewHome();
}
const SECTIONS = [["/review/decisions", "Decisions"], ["/review", "Review inbox"], ["/runs", "Runs"], ["/new-run", "Runs"], ["/approvals", "Approvals"],
  ["/audit", "Audit log"], ["/users", "Users"], ["/roles", "Roles"], ["/projects", "Projects"]];
// Every screen says where it is: the address (so Back works), the highlighted menu item and the window title (set by show()).
function here(path) {
  writeAddress(path);
  const sec = path === "/" ? "Home" : (SECTIONS.find(([p]) => path.startsWith(p)) || [null, ""])[1];
  sectionIcon = (NAV.flatMap(([, items]) => items).find(([name]) => name === sec) || [null, null, "home"])[2];
  if (/\/connections|\/add-data/.test(path)) sectionIcon = "plug";
  for (const a of $("nav").querySelectorAll("a")) {
    if (a.dataset.name === sec) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
}
const link = (path) => () => go(() => dispatch(path));
const P = (pid) => "/projects/" + pid;
const R = (pid, rid) => P(pid) + "/runs/" + rid;

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
  $("avatar").textContent = (me.username || "?").slice(0, 1).toUpperCase();
  $("who").textContent = me.username;
  $("who").setAttribute("title", me.roles.map(label).join(", "));
  $("password-btn").hidden = mode !== "local";
  if (mustChange) { $("nav").replaceChildren(); await run(() => viewPassword(true)); return; }       // nothing else works until the password is changed
  buildNav();
  go(() => dispatch(readAddress() || "/"));                 // back to the screen that was open (the address survives a reload; the sign-in does not)
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
const NAV = [
  ["", [["Home", "any", "home", "/"], ["Projects", "any", "folder", "/projects"], ["Runs", "runs.read", "play", "/runs"]]],
  ["Review", [["Review inbox", "queue.read", "inbox", "/review"], ["Decisions", "queue.read", "list", "/review/decisions"]]],
  ["Governance", [["Approvals", "config.read", "check", "/approvals"], ["Audit log", "logs.read", "shield", "/audit"]]],
  ["Administration", [["Users", "users.manage", "users", "/users"], ["Roles", "roles.manage", "sliders", "/roles"]]],
];
const NEW_MENU = [
  ["New project", "projects.create", "folder", () => newProject()], ["New run", "runs.start", "play", () => newRun()],
  ["Add data", "sources.manage", "database", () => pickProject("Add data", "Choose the project this data belongs to.", (pid) => go(() => addDataPage(pid)))],
];
function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }
function go(fn) {
  stopTimer();
  pageKeys = null;
  closeSearch();
  focusNext = true;
  return run(fn);
}
function buildNav() {
  const nav = $("nav");
  nav.replaceChildren();
  const items = NEW_MENU.filter((m) => can(m[1]));
  if (items.length) {
    const menu = el("div", { id: "new-menu", hidden: true });
    const opener = el("button", { id: "new-btn", type: "button", "aria-expanded": "false", "aria-controls": "new-menu" }, icon("plus"), "New");
    const toggle = (open) => { menu.hidden = !open; opener.setAttribute("aria-expanded", String(open)); if (open) menu.querySelector("button").focus(); };
    opener.addEventListener("click", () => toggle(menu.hidden));
    menu.append(...items.map(([text, , ic, fn]) => el("button", { type: "button", onclick: () => { toggle(false); go(fn); } }, icon(ic), text)));
    menu.addEventListener("keydown", (e) => { if (e.key === "Escape") { toggle(false); opener.focus(); } });
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target) && e.target !== opener && !opener.contains(e.target)) toggle(false); });
    nav.append(opener, menu);
  }
  for (const [group, entries] of NAV) {
    const allowed = entries.filter((i) => can(i[1]));
    if (!allowed.length) continue;
    if (group) nav.append(el("h2", {}, group));
    for (const [name, , ic, path] of allowed) {
      nav.append(el("a", { href: "#" + path, "data-name": name, onclick: (e) => { e.preventDefault(); go(() => dispatch(path)); } }, icon(ic), name));
    }
  }
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
// Every coloured tag carries a symbol as well as a colour, so its meaning does not depend on seeing the colour.
const TAG_ICON = { success: "check", danger: "x", warn: "alert", info: "info" };
const tag = (text, kind) => el("span", { class: "tag" + (kind ? " " + kind : "") }, TAG_ICON[kind] ? icon(TAG_ICON[kind]) : null, text);
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
const TAB_ICON = { overview: "grid", connections: "plug", runs: "play", schedules: "calendar", results: "download", decisions: "list", rules: "sliders",
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

// ---- home --------------------------------------------------------------------------------------------------------------
async function viewHome() {
  here("/");
  const [h] = await Promise.all([api("GET", "/home"), refreshCache()]);
  const stats = [stat("Projects", num(h.projects), link("/projects")), stat("Connections", num(h.connections))];
  if (h.open_review_items != null) stats.push(stat("Pairs to review", num(h.open_review_items), link("/review")));
  if (h.pending_approvals != null) stats.push(stat("Waiting for approval", num(h.pending_approvals), link("/approvals")));
  const start = [];
  if (can("projects.create")) start.push(tile("folder", "Create a project", "A project has its own data connections, decisions, person IDs and rules.", () => go(newProject)));
  if (can("sources.manage")) start.push(tile("database", "Add data", "Connect a file, sheet or database, or upload a file, inside a project.", () => pickProject("Add data", "Choose the project this data belongs to.", (pid) => go(() => viewProject(pid, "connections")))));
  if (can("runs.start")) start.push(tile("play", "Start a run", "Match and merge the records of chosen connections.", () => go(() => newRun())));
  if (can("queue.read")) start.push(tile("inbox", "Review matches", "Every pair waiting for a person, across your projects.", link("/review")));
  show(page("Welcome, " + me.username, "Find duplicate customers across your sources, review them and keep one golden record per person."),
    el("div", { class: "stats" }, stats),
    start.length ? el("h2", { class: "section" }, "Get started") : null, start.length ? el("div", { class: "tiles" }, start) : null,
    can("runs.read") ? el("h2", { class: "section" }, "Recent runs") : null,
    can("runs.read") ? runsTable(h.recent_runs, true) : null);
}

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
  const create = can("projects.create") ? btn("New project", () => run(newProject), "primary", "plus") : null;
  show(page("Projects", "A project is one piece of dedupe work with its own data connections, runs, decisions and rules. You see the projects you created or were added to.", [create]),
    rows.length ? table(["Project", "Description", ["Runs"], "Latest run", "Last activity", "Owner", ...(del ? [""] : [])], rows)
      : empty(create ? "No projects yet. Create the first one." : "You are not in any project yet. Ask an administrator to create one or to add you to one.", create));
}
async function newProject() {
  const name = el("input", { type: "text", maxlength: "120" });
  const id = el("input", { type: "text", maxlength: "40" });
  const desc = el("textarea", { class: "plain", maxlength: "500" });
  const second = el("input", { type: "checkbox" });
  let touched = false;
  name.addEventListener("input", () => { if (!touched) id.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40); });
  id.addEventListener("input", () => { touched = true; });
  const err = errBox();
  const created = await dialog("New project", el("div", {}, field("Name", name), field("Id (used in addresses: lower-case letters, digits and -)", id), field("Description (optional)", desc),
    el("label", { class: "check" }, second, el("span", {}, "Require a second person to approve changes", el("span", { class: "muted" }, "Leave this off while you work alone. Turn it on in the project's settings once others join."))),
    err),
  "Create project", async () => {
    try { return await api("POST", "/projects", { id: id.value.trim(), name: name.value.trim(), description: desc.value.trim(), second_approval: second.checked }); }
    catch (e) { fail(err, e.message); return undefined; }
  });
  if (created) { await refreshCache(); await go(() => viewProject(created.id)); say("Project created. Start by adding its data.", "good"); }
}
async function pickProject(title, text, then) {
  await refreshCache();
  if (!cache.projects.length) { await go(viewProjects); say("Create a project first.", "bad"); return; }
  if (cache.projects.length === 1) { then(cache.projects[0].id); return; }
  const sel = el("select", {}, cache.projects.map((p) => el("option", { value: p.id }, p.name)));
  const chosen = await dialog(title, el("div", {}, el("p", { class: "hint" }, text), field("Project", sel)), "Continue", async () => sel.value);
  if (chosen) then(chosen);
}
async function viewProject(pid, tab) {
  const [p, runs, connections] = await Promise.all([api("GET", P(pid)), can("runs.read") ? api("GET", P(pid) + "/runs") : [],
    can("config.read") ? api("GET", P(pid) + "/connections") : []]);
  await refreshCache();
  const manage = can("project.access") || p.you_own;
  const tabList = [["overview", "Overview"], ...(can("config.read") ? [["connections", "Connections"]] : []), ...(can("runs.read") ? [["runs", "Runs"], ["schedules", "Schedules"]] : []),
    ...(can("results.export") ? [["results", "Results"]] : []), ...(can("queue.read") ? [["decisions", "Decisions"]] : []),
    ...(can("config.read") ? [["rules", "Rules"]] : []), ...(can("runs.read") ? [["activity", "Activity"]] : []), ...(manage ? [["access", "Access"], ["settings", "Settings"]] : [])];
  if (!tabList.some(([k]) => k === tab)) tab = "overview";
  here(P(pid) + (tab === "overview" ? "" : "/" + tab));
  const actions = can("runs.start") ? [btn("New run", () => go(() => newRun(pid)), "primary", "play")] : [];
  let body;
  if (tab === "overview") body = await projectOverview(p, runs, connections);
  else if (tab === "connections") body = await projectConnections(p, connections);
  else if (tab === "runs") body = runsTable(runs, false, pid);
  else if (tab === "schedules") body = await schedulesPanel(p, connections);
  else if (tab === "results") body = await resultsPanel(p, runs);
  else if (tab === "decisions") body = await decisionsPanel([p], { project: true });
  else if (tab === "rules") body = await rulesPanel(p);
  else if (tab === "activity") body = await activityPanel(pid);
  else if (tab === "access") body = await accessPanel(pid);
  else body = settingsPanel(p);
  show(page(p.name, p.description || "Project " + p.id, actions, crumbs(crumb("Projects", "/projects"), p.name), p.second_approval ? tag("Second approval on", "info") : null),
    tabs(tabList, tab, (t) => go(() => viewProject(pid, t))), tabPanel(tab, body));
}
async function projectOverview(p, runs, connections) {
  const live = connections.filter((c) => c.live_version);
  const finished = runs.find((r) => r.status === "succeeded");
  const going = runs.find((r) => r.status === "running" || r.status === "queued");
  const box = can("queue.read") ? ((await api("GET", "/review/inbox")).find((x) => x.project_id === p.id) || null) : null;
  const open = box ? box.open : null;
  const todo = [
    ["Add your data", "Connect a file, spreadsheet or database, or upload a file, then map its columns.", connections.length > 0, () => viewProject(p.id, "connections"), can("sources.manage")],
    ["Make the data ready to use", p.second_approval ? "A second person approves each connection, then it is made live." : "Make the connection live so runs can read it.", live.length > 0,
      () => viewProject(p.id, "connections"), can("config.activate") || can("config.approve")],
    ["Run the match", "Find the records that are the same person and group households.", !!finished || !!going, () => newRun(p.id), can("runs.start")],
    ["Review the uncertain matches", open == null ? "Appears after the first run." : open ? plural(open, "pair waits", "pairs wait") + " for a person to decide." : "Nothing waits for a person.",
      !!finished && open === 0, () => viewRun(p.id, finished.id, "review"), can("queue.read") && !!finished],
    ["Take the results out", "Download the golden records as Excel, CSV or Parquet, or write them into a database.", false, () => viewRun(p.id, finished.id, "results"), can("results.export") && !!finished],
  ];
  const next = todo.findIndex((t) => !t[2]);
  const list = el("div", { class: "todo" }, todo.map(([title, text, done, open, allowed], i) => el("div", { class: "todo-row" + (i === next ? " next" : "") },
    el("span", { class: "todo-mark" }, done ? icon("check") : String(i + 1)), el("div", {}, el("strong", {}, title), el("div", { class: "muted" }, text)),
    allowed && !done ? btn(i === next ? "Go" : "Open", () => go(open), i === next ? "primary small" : "small") : null)));
  const waiting = box && box.waiting_for_run ? el("div", { class: "notice", role: "note" }, plural(box.waiting_for_run, "decision waits", "decisions wait") +
    " for the next run: the people and golden records change only when a run applies them.",
  can("runs.start") && finished && finished.connections.length ? el("div", { class: "row" }, btn("Run again now", () => startRun(p.id, [], finished.id, "apply the latest review decisions"), "primary small", "play")) : null) : null;
  return el("div", {}, waiting, el("div", { class: "stats" }, stat("Connections", num(connections.length), () => go(() => viewProject(p.id, "connections"))), stat("Live connections", num(live.length)),
    stat("Runs", num(runs.length), () => go(() => viewProject(p.id, "runs"))), stat("People found", finished ? num(finished.summary.people) : "-"),
    stat("Pairs to review", open == null ? "-" : num(open), finished && can("queue.read") ? () => go(() => viewRun(p.id, finished.id, "review")) : null),
    box && box.pending_approval ? stat("Waiting for a second person", num(box.pending_approval), link("/approvals")) : null),
  el("h2", { class: "section" }, "Where you are"), list);
}
async function activityPanel(pid) {
  const ACTIONS = { propose: "Saved a connection version", activate: "Made a version live", approve: "Approved a version", decline: "Declined a version", copy: "Copied a connection",
    source_inspect: "Looked inside a source", source_preview: "Previewed a mapping", upload: "Uploaded a file", create_project: "Created the project", grant_access: "Added a member",
    revoke_access: "Removed a member", set_second_approval: "Changed the approval setting", rename_project: "Renamed the project", start: "Started a run", cancel: "Cancelled a run",
    delete_run: "Deleted a run", delete_upload: "Deleted an uploaded file", approve_match: "Decided: same person", reject_match: "Decided: different people",
    mark_unsure: "Marked a pair not sure", undo: "Undid a decision", split_person: "Split a person", override_field: "Corrected a field", approve_pending: "Gave a second approval",
    decline_pending: "Declined a decision" };
  const what = (e) => (e.detail && ACTIONS[e.detail.action]) || (label(e.event) + (e.detail && e.detail.action ? ": " + label(e.detail.action) : ""));
  const target = (e) => (e.object_ref || "").replace(pid + "/", "").replace(/^connection\//, "");
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

// ---- runs --------------------------------------------------------------------------------------------------------------
async function viewRuns() {
  here("/runs");
  await refreshCache();
  show(page("Runs", "The latest runs of every project you can open. Open a run to see its results and review queue.", can("runs.start") ? [btn("New run", () => go(() => newRun()), "primary", "play")] : []),
    runsTable(cache.runs, true));
}
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
  say("Run " + rid + " was deleted.", "good");
}
async function startRun(pid, connections, fromRun, reason, key, full) {
  const r = await api("POST", P(pid) + "/runs", { connections, from_run: fromRun || null, reason: reason || "", full_rerun: !!full }, { "Idempotency-Key": key || uuid() });
  await go(() => viewRun(pid, r.id));
  say(r.replayed ? "This run was already started." : "Run started. This page updates by itself until it finishes.", "good");
}
async function newRun(pid, step, chosen) {
  await refreshCache();
  chosen = chosen || new Set();
  step = step || (pid ? 2 : 1);
  here(pid ? P(pid) + "/new-run" : "/new-run");
  const bar = steps(["Project", "Connections", "Start"], step);
  const to = (s, p) => run(() => newRun(p || pid, s, chosen));
  const back = pid ? crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), "New run") : null;
  if (step === 1) {
    const tiles = cache.projects.map((p) => tile("folder", p.name, p.description || p.id, () => to(2, p.id)));
    show(page("New run", "Choose the project this run belongs to."), bar,
      tiles.length ? el("div", { class: "tiles" }, tiles) : empty("Create a project first.", can("projects.create") ? btn("New project", () => run(newProject), "primary", "plus") : null));
    return;
  }
  if (step === 2) {
    const all = can("config.read") ? await api("GET", P(pid) + "/connections") : [];
    const live = all.filter((c) => c.live_version);
    const tiles = live.map((c) => tile(logo(c.type, "big"), c.id, typeTitle(c.type) + ", " + c.fields_mapped + " fields mapped, version " + c.live_version,
      () => { if (chosen.has(c.id)) chosen.delete(c.id); else chosen.add(c.id); to(2); }, chosen.has(c.id)));
    show(page("New run", "Project " + projectName(pid) + ". Choose the connections to read; only live connections are listed.",
      [btn("Back", () => (cache.projects.length > 1 ? to(1) : go(() => viewProject(pid)))), btn("Next", () => (chosen.size ? to(3) : say("Choose at least one connection.", "bad")), "primary")], back), bar,
    tiles.length ? el("div", { class: "tiles" }, tiles) : empty("This project has no live connections yet." + (all.length ? " Make one live first." : ""),
      can("sources.manage") || can("config.activate") ? btn("Open connections", () => go(() => viewProject(pid, "connections")), "primary", "database") : null));
    return;
  }
  const reason = el("textarea", { class: "plain", maxlength: "300" });
  const key = uuid();                              // one key for this form: a second press replays the first start instead of starting twice
  const load = choiceCards("load", LOAD_MODES, "incremental");
  show(page("New run", "Check and start.", [btn("Back", () => to(2)), btn("Start run", () => startRun(pid, [...chosen], null, reason.value, key, load.value() === "full"), "primary", "play")], back), bar,
    kv([["Project", projectName(pid)], ["Connections", [...chosen].join(", ")], ["Rules", "The project's live rules, or the built-in defaults when none are live"]]),
    el("h3", { class: "section" }, "How to load"), load.node,
    field("Reason (optional)", reason));
}
async function viewRun(pid, rid, tab) {
  const [r] = await Promise.all([api("GET", R(pid, rid)), refreshCache()]);
  const going = r.status === "running" || r.status === "queued";
  const okRun = r.status === "succeeded";
  const tabList = okRun ? [["overview", "Overview"], ["results", "Results"], ...(can("queue.read") ? [["review", "Review queue"]] : []),
    ...(can("person.read") ? [["people", "People"]] : []), ["log", "Log"]] : [];
  if (!tabList.some(([k]) => k === tab)) tab = "overview";
  here(R(pid, rid) + (tab === "overview" ? "" : "/" + tab));
  const again = can("runs.start") && !going && r.connections.length;
  const actions = [];
  if (okRun && can("results.export") && tab !== "results") actions.push(btn("Get the results", () => go(() => viewRun(pid, rid, "results")), "primary", "download"));
  if (going) actions.push(btn("Refresh", () => run(() => viewRun(pid, rid, tab)), "", "refresh"));
  if (going && can("runs.start")) actions.push(btn("Stop run", () => cancelRun(pid, rid), "danger"));
  if (again) actions.push(btn("Run again", () => startRun(pid, [], rid), "", "play"));
  if (!going && can("data.delete")) actions.push(btn("Delete run", () => deleteRun(pid, rid), "danger", "trash"));
  const top = page("Run " + rid, "Started " + when(r.started_at || r.created_at) + " by " + r.requested_by + (r.finished_at ? ". Finished " + when(r.finished_at) + "." : "."), actions,
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Runs", P(pid) + "/runs"), rid), runState(r));
  if (going) {
    timer = setInterval(() => run(async () => {
      const now = await api("GET", R(pid, rid));
      if (now.status !== "running" && now.status !== "queued") { stopTimer(); await viewRun(pid, rid); say("The run finished: " + (STATUS_LABEL[now.status] || now.status) + ".", now.status === "succeeded" ? "good" : "bad"); }
    }, true), 4000);
    show(top, empty(r.status === "queued" ? "This run is waiting its turn" + (r.queue_position > 1 ? " (" + (r.queue_position - 1) + " ahead of it)" : " and starts next") + ". It starts by itself; you can stop it above."
      : "This run is in progress. Its results appear here when it finishes (this page checks every few seconds); earlier runs stay readable meanwhile."));
    return;
  }
  const restart = again ? " Start it again with Run again above." : " Someone who can start runs can start it again.";
  if (r.status === "cancelled") { show(top, empty("This run was stopped before it finished, so it has no results." + restart)); return; }
  if (!okRun) {
    show(top, empty(r.status === "interrupted" ? "This run was interrupted when the server restarted, so it has no results." + restart
      : "This run did not finish successfully (exit code " + r.exit_code + "), so it has no results. The end of its log is below." + restart), await runLogPanel(pid, rid));
    return;
  }
  const stale = r.latest_run_id && r.latest_run_id !== rid ? el("div", { class: "notice warn", role: "note" },
    el("strong", {}, "A newer run has finished. "), "This run's people, review queue and results are out of date. Decisions made here still count, but review in the newest run.",
    el("div", { class: "row" }, btn("Open the newest run", () => go(() => viewRun(pid, r.latest_run_id, tab)), "small primary"))) : null;
  let body;
  if (tab === "overview") body = runOverview(r);
  else if (tab === "review") body = el("div", {}, scoringNotice(r.summary), await reviewQueue(pid, rid, { status: "open", band: "all", offset: 0 }));
  else if (tab === "log") body = await runLogPanel(pid, rid);
  else if (tab === "results") body = await runResults(pid, rid);
  else body = peopleLookup(pid, rid);
  show(top, stale, tabs(tabList, tab, (t) => go(() => viewRun(pid, rid, t))), tabPanel(tab, body));
}
// The results of one finished run, right on the run page: the three datasets, each as a file or into a database.
async function runResults(pid, rid) {
  if (!can("results.export")) {
    return empty("Your role cannot take results out of the portal. You can still read them here (Overview, Review queue, People). Ask an administrator for the results.export permission if you need files.");
  }
  const p = await api("GET", P(pid));
  const refresh = () => go(() => viewRun(pid, rid, "results"));
  return el("div", {}, el("p", { class: "hint" }, "Results contain personal data. Every download or database write asks for a reason and is recorded in the project's activity."),
    ...(await datasetCards(p, rid)), await destinationsPanel(p, refresh));
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
function runOverview(r) {
  const s = r.summary;
  const j = s.judgments;
  const parts = [scoringNotice(s), el("div", { class: "stats" }, stat("Records read", num(s.records)), stat("People found", num(s.people)), stat("Households", num(s.households)),
    stat("Pairs merged automatically", num(s.auto_merged_pairs)), stat("In the review queue", num(s.review_items)), stat("Time taken", s.seconds == null ? "" : s.seconds.toFixed(1) + " s"))];
  if (j && j.judgments_applied_to_run) {
    parts.push(el("h2", { class: "section" }, "Steward decisions this run applied"),
      kv([["Decisions applied", num(j.judgments_applied_to_run)], ...Object.entries(j.by_kind || {}).map(([k, n]) => ["  " + (KIND_LABEL[k] || label(k)), num(n)]),
        ["Kept in the result", num(j.held)], ["Not kept", j.not_held ? el("span", { class: "bad" }, num(j.not_held) + " (a later decision or an identifier rule overruled them)") : "0"]]));
  }
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
        el("td", {}, Object.entries(x.invalid || {}).map(([f, n]) => label(f) + " " + num(n)).join(", ") || "None")))));
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
        g.auto_ranked_sources && g.auto_ranked_sources.length ? ["Ranked automatically", g.auto_ranked_sources.join(", ") + " (by how complete their records are; set the order in Rules > Golden record)"] : null,
        g.sources_without_dates && g.sources_without_dates.length ? ["No record date", el("span", { class: "bad" }, g.sources_without_dates.join(", ") + ": 'newest record' could not use them")] : null,
        ["Fields where sources disagreed", num(g.values_with_source_conflict)],
        g.shared_values_passed_over ? ["Shared phones or emails passed over", num(g.shared_values_passed_over)] : null]));
  }
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

// ---- rules of a project --------------------------------------------------------------------------------------------------
// One page per kind of rule. It reads top to bottom: what is live now (in plain words), changes waiting to be made live, and the version
// history folded away. The editor opens only when someone presses "Change the rules".
const CONFIGS = [["decision", "Matching rules"], ["survivorship", "Golden record"], ["settings", "Run settings"]];
const CONFIG_INTRO = {
  decision: "How the engine decides whether two records belong to the same person.",
  survivorship: "Which value a person's golden record keeps when their records disagree.",
  settings: "Technical settings of a run. Change them together with your administrator.",
};
const CONFIG_HEAD = { decision: ["What the live matching rules do", "Change the matching rules"], survivorship: ["What the live golden-record rules do", "Change the golden-record rules"],
  settings: ["The live run settings", "Change the run settings"] };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
async function rulesPanel(p, name, editing) {
  name = name || "decision";
  const pid = p.id;
  const base = P(pid) + "/config/" + name;
  const versions = await api("GET", base);
  const live = versions.find((v) => v.status === "active");
  const waiting = (v) => v.status === "proposed" || v.status === "approved";
  const pending = versions.filter(waiting).reverse();
  const latest = pending[0] || live;                // edits start from the newest version still waiting, else the live one (never a declined one)
  const second = p.second_approval;
  const wrap = el("div", { class: "rules-page" });
  const again = async (edit) => wrap.replaceWith(await rulesPanel(p, name, edit));
  const result = el("div", { class: "rules-result" });
  const closeResult = () => btn("Close", () => result.replaceChildren(), "small ghost", "x");
  const act = async (v, what, title, minLength, prompt, danger) => {
    const why = await askReason(title, prompt, minLength, title.split(" ")[0], danger);
    if (why === null) return;
    const out = await api("POST", base + "/versions/" + v.version + "/" + what, { reason: why });
    await again();
    say(title + ": done" + (out && out.applies ? ". Applies " + out.applies + "." : what === "activate" ? ". The project's next run uses it." : "."), "good");
  };
  const view = async (v) => {
    const [content, changes] = await Promise.all([api("GET", base + "/versions/" + v.version),
      live && live.version !== v.version ? api("GET", base + "/diff?old=" + live.version + "&new=" + v.version) : null]);
    fill(result, el("div", { class: "title-row" }, el("h3", { class: "section" }, "Version " + v.version + (changes ? ": what changes compared with the live version " + live.version : "")), closeResult()),
      changes ? (changes.length ? el("ul", { class: "changes" }, changes.map((c) => el("li", {}, c))) : el("p", { class: "muted" }, "No difference.")) : null,
      el("details", { class: "advanced" }, el("summary", {}, "The whole version as JSON"), el("pre", { class: "log", tabindex: "0" }, JSON.stringify(content.content, null, 2))));
    result.scrollIntoView({ block: "nearest" });
  };
  const dry = async (v) => {
    const r = await api("POST", base + "/versions/" + v.version + "/dry-run", {});
    const zones = ["auto_merge", "review", "reject"];
    fill(result, el("div", { class: "title-row" }, el("h3", { class: "section" }, "Try-out of version " + v.version + " on the " + num(r.pairs) + " scored pairs of run " + r.run_id), closeResult()),
      el("p", { class: "hint" }, "Nothing is changed: this shows how the pairs of the latest run would have been decided with this version."),
      table(["Decision", ["Now"], ["With this version"], ["Change"]], zones.map((z) => el("tr", {}, el("td", {}, label(z)), el("td", { class: "num" }, num(r.zones_before[z])),
        el("td", { class: "num" }, num(r.zones_after[z])), el("td", { class: "num" }, (r.zones_after[z] - r.zones_before[z] > 0 ? "+" : "") + num(r.zones_after[z] - r.zones_before[z]))))),
      el("p", { class: "hint" }, Object.keys(r.moved).length ? "Moved: " + Object.entries(r.moved).map(([k, n]) => k + " " + num(n)).join(", ") : "No pair changes decision."));
    result.scrollIntoView({ block: "nearest" });
  };
  const back = async (v) => {
    const why = await askReason("Go back to version " + v.version, "Why go back?", 10, "Go back", true);
    if (why === null) return;
    await api("POST", base + "/rollback", { to_version: v.version, reason: why });
    await again();
    say("Done. The project's next run uses version " + v.version + " again.", "good");
  };
  const mine = (v) => v.proposed_by === me.sub;
  const decide = (v) => [
    btn(live ? "What changes" : "View", () => view(v), "small", "list"),
    name === "decision" && can("config.propose") ? btn("Try it on the latest run", () => dry(v), "small", "play") : null,
    second && can("config.approve") && v.status === "proposed" && !mine(v) ? btn("Approve", () => act(v, "approve", "Approve version " + v.version, 0, "Note"), "small", "check") : null,
    can("config.activate") && (!second || v.approved_by.length) ? btn("Make live", () => act(v, "activate", "Make version " + v.version + " live", 0, "Note"), "small primary", "zap") : null,
    can("config.approve") ? btn("Decline", () => act(v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger", "x") : null,
    second && v.status === "proposed" && mine(v) ? tag("Yours: someone else approves it", "warn") : null,
  ];

  // ---- what is live, and what waits
  const status = el("div", { class: "rules-status" },
    live ? el("div", { class: "live-line on" }, icon("check"), el("strong", {}, "Live: version " + live.version),
      el("span", { class: "muted" }, (live.activated_by ? "made live by " + live.activated_by + ", " : "") + when(live.activated_at)))
      : el("div", { class: "live-line" }, icon("info"), el("strong", {}, "Built-in defaults in use"),
        el("span", { class: "muted" }, "this project has not made a version of its own live yet")),
    pending.map((v) => el("div", { class: "pending-card" }, icon("clock"),
      el("div", { class: "grow" }, el("strong", {}, "Version " + v.version + (v.status === "approved" ? " is approved and waits to be made live" : second ? " waits for a second person to approve it" : " waits to be made live")),
        el("div", { class: "muted" }, "Proposed by " + v.proposed_by + ", " + when(v.proposed_at) + (v.reason ? ": “" + v.reason + "”" : ""))),
      el("div", { class: "row" }, decide(v)))),
    el("ol", { class: "flow-steps", "aria-label": "How a change goes live" }, withArrows([el("li", {}, icon("edit"), "Propose a change"),
      second ? el("li", {}, icon("users"), "A second person approves") : null, el("li", {}, icon("zap"), "Make it live"), el("li", {}, icon("play"), "The next run uses it")])));

  // ---- the body: a plain summary of the live rules, or the editor
  const canEdit = can("config.propose");
  const start = async () => (latest ? api("GET", base + "/versions/" + latest.version) : api("GET", base + "/default"));
  const shown = live ? api("GET", base + "/versions/" + live.version) : api("GET", base + "/default");
  const proposed = async (content, reason) => {
    const out = await api("POST", base + "/propose", { content, reason });
    await again(false);
    say("Saved as version " + out.version + ". " + (second ? "Another person approves it, then it can be made live." : "Make it live when you are ready; the project's next run uses it."), "good");
  };
  const cancel = () => run(() => again(false));
  const head = el("div", { class: "rules-head" }, el("div", {}, el("h2", { class: "section" }, CONFIG_HEAD[name][editing ? 1 : 0]), el("p", { class: "hint" }, CONFIG_INTRO[name])),
    !editing && canEdit ? btn(name === "settings" ? "Change the settings" : "Change the rules", () => run(() => again(true)), "primary", "edit") : null);
  let body;
  if (editing && canEdit) {
    const startFrom = await start();
    const note = latest ? el("div", { class: "notice", role: "note" }, icon("info"), " You are editing a copy of version " + latest.version +
      (latest.status === "active" ? " (live)" : waiting(latest) ? " (waiting, not live yet)" : "") + ". Nothing changes until the new version is made live.")
      : el("div", { class: "notice", role: "note" }, icon("info"), " You are starting from the built-in defaults. Nothing changes until the new version is made live.");
    if (name === "decision") {
      const [cat, defaults] = await Promise.all([api("GET", "/rules-catalogue"), api("GET", base + "/default")]);
      body = [note, matchingEditor(startFrom.content, defaults.content, cat, proposed, cancel)];
    } else if (name === "survivorship") {
      const guide = await api("GET", P(pid) + "/golden-record-guide");
      body = [note, goldenForm(startFrom.content, guide, proposed, cancel)];
    } else body = [note, jsonEditor(startFrom.content, proposed, cancel)];
  } else {
    const content = (await shown).content;
    if (name === "decision") body = matchingSummary(content, await api("GET", "/rules-catalogue"));
    else if (name === "survivorship") body = goldenSummary(content, await api("GET", P(pid) + "/golden-record-guide"));
    else body = settingsSummary(content);
  }

  // ---- history, folded away
  const rows = versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version), el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by),
    el("td", {}, when(v.proposed_at)), el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")),
    el("td", { class: "actions-cell" }, btn("View", () => view(v), "small"),
      can("config.activate") && v.status === "superseded" ? btn("Go back to this", () => back(v), "small", "undo") : null)));
  const history = versions.length ? el("details", { class: "history" }, el("summary", {}, icon("clock"), "Version history (" + versions.length + ")"),
    table(["Version", "Status", "Proposed by", "When", "Reason", "Approved by", ""], rows)) : null;

  wrap.append(tabs(CONFIGS, name, async (n) => wrap.replaceWith(await rulesPanel(p, n))), status, result, head, el("div", { class: "rules-body" }, body), history);
  return wrap;
}

// The run settings: a short list in view mode, JSON in the editor (they are technical).
function settingsSummary(content) {
  const entries = Object.entries(content || {});
  if (!entries.length) return empty("The built-in run settings are used.");
  return kv(entries.map(([k, v]) => [label(k), v !== null && typeof v === "object" ? el("code", {}, JSON.stringify(v)) : String(v)]));
}
function jsonEditor(content, propose, cancel) {
  const editor = el("textarea", { spellcheck: "false", rows: "16", class: "code" });
  editor.value = JSON.stringify(content, null, 2);
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  const save = async () => {
    let parsed;
    try { parsed = JSON.parse(editor.value); } catch (_) { fail(err, "That is not valid JSON.", editor); return; }
    try { await propose(parsed, why.value); } catch (e) { fail(err, e.message); }
  };
  return el("div", {}, field("Settings (JSON). The server checks them.", editor), saveBar(why, err, save, cancel));
}
// The bottom of every editor: why, then save or cancel.
function saveBar(why, err, save, cancel) {
  why.placeholder = "For example: stricter merging after the March audit";
  return el("div", { class: "save-bar" }, field("Why this change? (at least 10 characters; kept in the history)", why), err,
    el("div", { class: "row" }, btn("Save as a new version", save, "primary", "check"), cancel ? btn("Cancel", cancel) : null));
}

// ---- matching rules in plain words ----------------------------------------------------------------------------------------
const RULE_KIND = { never_merge: ["Never merge", "danger", "x", "Wins over everything: the two records stay apart whatever their score."],
  force_review: ["Ask a person", "warn", "users", "Never merged automatically: the pair goes to the review queue."],
  auto_merge: ["Always merge", "success", "merge", "Merged even with a lower score, unless a 'never merge' rule applies."],
  not_enough_evidence: ["Not enough evidence", "", "info", "Neither merged nor sent for review."] };
const RULE_ORDER = ["never_merge", "force_review", "auto_merge", "not_enough_evidence"];
// short names for the built-in rules (their full reasons stay one click away)
const RULE_TITLE = {
  veto_pan_conflict: "Two different PANs", veto_aadhaar_conflict: "Two different Aadhaar numbers", veto_gender_conflict: "Different genders and no identifier agrees",
  veto_birth_year_far_apart: "Birth years more than 10 years apart", veto_customer_number_conflict: "Two different customer numbers",
  auto_same_pan_compatible_name: "Same PAN and a compatible name", auto_same_aadhaar_compatible_name: "Same Aadhaar and a compatible name",
  auto_same_customer_number: "Same customer number and a compatible name", review_identifier_with_different_name: "Same identifier but a clearly different name",
  review_identifiers_contradict: "One identifier agrees while another conflicts", review_possible_twins: "Possible twins: minors with the same birth date and PIN",
  review_possible_namesake: "Only a similar name and a birth date agree", review_age_only_evidence: "Only an age, a name and a place agree",
  not_enough_evidence: "Too little evidence to merge or even ask",
};
const ruleTitle = (r) => RULE_TITLE[r.id] || r.reason || label(r.id);
// A rule's condition in words, from the same building blocks the engine runs.
function describeRule(node, cat) {
  const title = (c) => ((cat.comparisons.find((x) => x.name === c) || {}).title || c);
  const q = (s) => "“" + s + "”";
  if (node.all) return node.all.map((n) => describeRule(n, cat)).join(" and ");
  if (node.any) return "(" + node.any.map((n) => describeRule(n, cat)).join(" or ") + ")";
  if (node.not) return "not " + describeRule(node.not, cat);
  if (node.comparison) {
    const t = title(node.comparison);
    if (node.missing) return t + " is missing on one side";
    if (node.is) return t + ": " + q(node.is);
    if (node.is_not) return t + " is not " + q(node.is_not);
    if (node.in) return t + ": " + node.in.map(q).join(" or ");
    if (node.not_in) return t + " is none of " + node.not_in.map(q).join(", ");
  }
  if (node.field) {
    const f = label(node.field).replace("dob year", "birth years");
    if (node.op === "abs_diff_gt") return f + " differ by more than " + node.value;
    if (node.op === "differs") return f + " differ";
    if (node.op === "same") return f + " are the same";
    if (node.op === "both_born_within_last") return "both born in the last " + node.value + " years";
    if (node.op === "both_gte") return f + " both at least " + node.value;
    if (node.op === "both_lte") return f + " both at most " + node.value;
  }
  if (node.score) return (node.score === "probability" ? "match score " + node.op + " " + Math.round(node.value * 100) + "%" : "match weight " + node.op + " " + node.value);
  return JSON.stringify(node);
}
// The order the engine checks things in, drawn once at the top.
// steps of a flow with an arrow between them (hidden from screen readers: the list order already says it)
const withArrows = (items) => items.filter(Boolean).flatMap((it, i) => (i ? [el("li", { class: "sep", "aria-hidden": "true" }, icon("next")), it] : [it]));
function decisionFlow() {
  return el("ol", { class: "decision-flow", "aria-label": "How a pair of records is decided" }, withArrows([
    el("li", {}, icon("users"), el("span", {}, el("strong", {}, "A pair of records"), el("span", { class: "muted" }, "that share something"))),
    ...RULE_ORDER.slice(0, 3).map((k) => el("li", { class: RULE_KIND[k][1] }, icon(RULE_KIND[k][2]), el("span", {}, el("strong", {}, RULE_KIND[k][0] + " rules"), el("span", { class: "muted" }, "checked " + (k === "never_merge" ? "first" : "next"))))),
    el("li", { class: "info" }, icon("chart"), el("span", {}, el("strong", {}, "Match score"), el("span", { class: "muted" }, "decides the rest")))]));
}
// The score scale: keep separate / ask a person / merge, drawn to scale. Returns { node, set(review, auto) } so an editor can redraw it.
function scoreScale(review, auto) {
  const zones = { separate: el("span", { class: "zone separate" }), review: el("span", { class: "zone review" }), merge: el("span", { class: "zone merge" }) };
  const legend = { separate: el("span"), review: el("span"), merge: el("span") };
  const bar = el("div", { class: "score-bar", role: "img" }, zones.separate, zones.review, zones.merge);
  const set = (r, a) => {
    const pr = Math.round(r * 100);
    const pa = Math.round(a * 100);
    zones.separate.style.flexGrow = String(Math.max(pr, 0));
    zones.review.style.flexGrow = String(Math.max(pa - pr, 0));
    zones.merge.style.flexGrow = String(Math.max(100 - pa, 0));
    legend.separate.textContent = "below " + pr + "%";
    legend.review.textContent = pr + "% to " + Math.max(pa - 1, pr) + "%";
    legend.merge.textContent = pa + "% and above";
    bar.setAttribute("aria-label", "Below " + pr + "%: keep separate. " + pr + "% to " + (pa - 1) + "%: ask a person. " + pa + "% and above: merge automatically.");
  };
  set(review, auto);
  const key = (k, text) => el("div", { class: "score-key " + k }, el("i", { "aria-hidden": "true" }), el("span", {}, el("strong", {}, text), legend[k]));
  return { node: el("div", { class: "score-scale" }, bar, el("div", { class: "score-ticks", "aria-hidden": "true" }, el("span", {}, "0%"), el("span", {}, "100%")),
    el("div", { class: "score-keys" }, key("separate", "Keep separate"), key("review", "Ask a person"), key("merge", "Merge automatically"))), set };
}
const step = (n, title, text, ...body) => el("section", { class: "rule-step" }, el("div", { class: "step-head" }, el("span", { class: "step-no", "aria-hidden": "true" }, String(n)),
  el("div", {}, el("h3", {}, title), text ? el("p", { class: "muted" }, text) : null)), body);
function ruleRow(r, cat, control, extra) {
  return el("div", { class: "rule" + (r.enabled === false ? " off" : "") }, control || null,
    el("div", { class: "rule-text" }, el("strong", {}, ruleTitle(r)),
      el("details", {}, el("summary", {}, "Details"), RULE_TITLE[r.id] && r.reason ? el("p", {}, r.reason) : null,
        el("p", { class: "muted" }, "Applies when " + describeRule(r.when, cat) + "."))), extra || null);
}
// Read-only: what the live matching rules do.
function matchingSummary(content, cat) {
  const owned = new Set(Object.values(KEY_RULES).flatMap((k) => [k[1], k[2]]));
  const rules = content.rules || [];
  const ids = Object.keys(KEY_RULES).map((k) => {
    const on = keyOn(content, k);
    return el("div", { class: "id-card" + (on ? " on" : "") }, el("span", { class: "id-icon", "aria-hidden": "true" }, icon("key")),
      el("div", {}, el("strong", {}, cap(KEY_RULES[k][0])), on ? tag("Decides", "success") : tag("Off"),
        el("p", { class: "muted" }, on ? "Same " + KEY_RULES[k][0] + " and a compatible name: merged. Two different " + KEY_RULES[k][0] + "s: never merged."
          : "Counts towards the match score only.")));
  });
  const groups = RULE_ORDER.map((type) => {
    const all = rules.filter((r) => r.type === type && !owned.has(r.id));
    const on = all.filter((r) => r.enabled !== false);
    const off = all.filter((r) => r.enabled === false);
    if (!all.length) return null;
    return el("div", { class: "rule-group " + RULE_KIND[type][1] }, el("div", { class: "group-head" }, icon(RULE_KIND[type][2]), el("strong", {}, RULE_KIND[type][0]),
      el("span", { class: "count-badge" }, String(on.length)), el("span", { class: "muted" }, RULE_KIND[type][3])),
      on.map((r) => ruleRow(r, cat)), off.length ? el("p", { class: "muted off-list" }, "Switched off: " + off.map(ruleTitle).join("; ") + ".") : null);
  });
  return el("div", { class: "matching-view" }, decisionFlow(),
    step(1, "The match score", "Every pair gets a score from all the fields the two records share. A pair no rule catches is decided here.", scoreScale(content.review_at || 0.1, content.auto_merge_at || 0.9).node),
    step(2, "Identifiers that decide on their own", "A trusted identifier can settle a pair without the score.", el("div", { class: "id-cards" }, ids)),
    step(3, "Other rules", "Checked before the score, in this order. Open a rule's details to see exactly when it applies.", groups));
}
// The editor: the same three steps with controls, a rule builder in a dialog, and the raw JSON for experts.
function matchingEditor(content, defaults, cat, propose, cancel) {
  const state = JSON.parse(JSON.stringify(content));
  state.rules = state.rules || [];
  const keyRules = (k) => [KEY_RULES[k][1], KEY_RULES[k][2]];
  const owned = new Set(Object.keys(KEY_RULES).flatMap(keyRules));
  const keys = Object.fromEntries(Object.keys(KEY_RULES).map((k) => [k, keyOn(state, k)]));
  // the identifier switches own their two rules; a project whose rules predate a switch gets the rule from the built-in defaults
  const applyKeys = () => {
    for (const k of Object.keys(KEY_RULES)) for (const id of keyRules(k)) {
      let r = state.rules.find((x) => x.id === id);
      if (!r) { const d = (defaults.rules || []).find((x) => x.id === id); if (d) { r = JSON.parse(JSON.stringify(d)); state.rules.push(r); } }
      if (r) r.enabled = keys[k];
    }
  };
  // step 1: the score levels, with the scale redrawn as you type
  const pct = (x, d) => String(Math.round((x || d) * 100));
  const reviewAt = el("input", { type: "number", min: "1", max: "99", step: "1", value: pct(state.review_at, 0.1), class: "pct" });
  const autoAt = el("input", { type: "number", min: "1", max: "100", step: "1", value: pct(state.auto_merge_at, 0.9), class: "pct" });
  const scale = scoreScale(Number(reviewAt.value) / 100, Number(autoAt.value) / 100);
  const redraw = () => { const r = Number(reviewAt.value) / 100; const a = Number(autoAt.value) / 100; if (r > 0 && r < a && a <= 1) scale.set(r, a); };
  reviewAt.addEventListener("input", redraw);
  autoAt.addEventListener("input", redraw);
  // step 2: identifier switches
  const ids = Object.keys(KEY_RULES).map((k) => {
    const cb = el("input", { type: "checkbox", checked: keys[k] });
    const card = el("label", { class: "id-card editable" + (keys[k] ? " on" : "") }, el("span", { class: "id-icon", "aria-hidden": "true" }, icon("key")),
      el("div", { class: "grow" }, el("strong", {}, cap(KEY_RULES[k][0]) + " decides on its own"),
        el("p", { class: "muted" }, "Same " + KEY_RULES[k][0] + " and a compatible name: merged. Two different " + KEY_RULES[k][0] + "s: never merged.")), cb);
    cb.addEventListener("change", () => { keys[k] = cb.checked; card.classList.toggle("on", cb.checked); });
    return card;
  });
  // step 3: the other rules, switchable; your own rules can be removed
  const list = el("div", { class: "rule-groups" });
  const drawRules = () => {
    list.replaceChildren(...RULE_ORDER.map((type) => {
      const rs = state.rules.filter((r) => r.type === type && !owned.has(r.id));
      if (!rs.length) return null;
      return el("div", { class: "rule-group " + RULE_KIND[type][1] }, el("div", { class: "group-head" }, icon(RULE_KIND[type][2]), el("strong", {}, RULE_KIND[type][0]),
        el("span", { class: "muted" }, RULE_KIND[type][3])),
        rs.map((r) => {
          const cb = el("input", { type: "checkbox", checked: r.enabled !== false, "aria-label": ruleTitle(r) + ": on" });
          const row = ruleRow(r, cat, cb, r.id.startsWith("custom_") ? btn("Remove", () => { state.rules = state.rules.filter((x) => x !== r); drawRules(); }, "small ghost danger", "trash", { "aria-label": "Remove rule " + ruleTitle(r) }) : null);
          cb.addEventListener("change", () => { r.enabled = cb.checked; row.classList.toggle("off", !cb.checked); });
          return row;
        }));
    }).filter(Boolean));
  };
  drawRules();
  const addRule = async () => {
    const rule = await ruleBuilder(cat, state.rules);
    if (!rule) return;
    state.rules.push(rule);
    drawRules();
    say("Rule added. Save the rules as a new version to keep it.", "good");
  };
  // advanced: the same content as JSON
  const editor = el("textarea", { spellcheck: "false", rows: "14", class: "code" });
  const json = el("details", { class: "advanced" }, el("summary", {}, "For experts: edit as JSON (nested conditions)"),
    el("p", { class: "hint" }, "Saving from here uses the JSON below as it is, not the choices above."), field("Rules (JSON)", editor));
  json.addEventListener("toggle", () => { if (json.open) { applyKeys(); editor.value = JSON.stringify({ ...state, auto_merge_at: Number(autoAt.value) / 100, review_at: Number(reviewAt.value) / 100 }, null, 2); } });
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  const save = async () => {
    fail(err, "");
    let out;
    if (json.open) {
      try { out = JSON.parse(editor.value); } catch (_) { fail(err, "That is not valid JSON.", editor); return; }
    } else {
      const a = Number(autoAt.value) / 100;
      const r = Number(reviewAt.value) / 100;
      if (!(r > 0 && r < a && a <= 1)) { fail(err, "'Ask a person' must start above 0% and below 'Merge automatically', which is at most 100%.", reviewAt); return; }
      applyKeys();
      out = { ...state, auto_merge_at: a, review_at: r };
    }
    try { await propose(out, why.value); } catch (e) { fail(err, e.message); }
  };
  return el("div", { class: "matching-view editing" }, decisionFlow(),
    step(1, "The match score", "Choose where 'ask a person' and 'merge automatically' start. Below the first level the records stay separate.",
      el("div", { class: "fields" }, field("Ask a person from (%)", reviewAt), field("Merge automatically from (%)", autoAt)), scale.node),
    step(2, "Identifiers that decide on their own", "Switch one on only when it is reliable in your sources (one per person, rarely mistyped).", el("div", { class: "id-cards" }, ids)),
    step(3, "Other rules", "Switch a rule off to stop it; your own rules can be removed.", list,
      el("div", { class: "row" }, btn("Add your own rule", addRule, "", "plus"))),
    json, saveBar(why, err, save, cancel));
}
// "Add your own rule" in a dialog: what happens, when (one or more conditions), a name people will read.
function ruleBuilder(cat, existing) {
  const what = el("select", {}, ["never_merge", "force_review", "auto_merge"].map((k) => el("option", { value: k }, cat.rule_types[k])));
  const join = el("select", { "aria-label": "How the conditions combine" }, el("option", { value: "all" }, "all of these are true"), el("option", { value: "any" }, "any of these is true"));
  const conds = el("div", { class: "conds" });
  const condRow = () => {
    const subject = el("select", { "aria-label": "Field" }, cat.comparisons.map((c) => el("option", { value: "c:" + c.name }, c.title)),
      el("option", { value: "score" }, "Match score"), el("option", { value: "years" }, "Birth years"));
    const op = el("select", { "aria-label": "Condition" });
    const val = el("span");
    const row = el("div", { class: "cond row" }, subject, op, val, btn("Remove", () => row.remove(), "small ghost", "x", { "aria-label": "Remove this condition" }));
    const fillVal = () => {
      const s = subject.value;
      if (s.startsWith("c:")) {
        const c = cat.comparisons.find((x) => x.name === s.slice(2));
        val.replaceChildren(op.value === "missing" ? "" : el("select", { "aria-label": "Value" }, c.levels.map((l) => el("option", { value: l }, l))));
      } else val.replaceChildren(el("input", { type: "number", min: "0", max: s === "score" ? "100" : "120", value: s === "score" ? "90" : "10", "aria-label": "Value" }));
    };
    const fillOps = () => {
      const s = subject.value;
      const ops = s.startsWith("c:") ? [["is", "is"], ["is_not", "is not"], ["missing", "is missing on one side"]]
        : s === "score" ? [[">=", "is at least (%)"], ["<", "is below (%)"]] : [["abs_diff_gt", "differ by more than (years)"]];
      op.replaceChildren(...ops.map(([v, t]) => el("option", { value: v }, t)));
      fillVal();
    };
    subject.addEventListener("change", fillOps);
    op.addEventListener("change", fillVal);
    fillOps();
    row.build = () => {
      const s = subject.value;
      const v = val.firstChild ? val.firstChild.value : null;
      if (s.startsWith("c:")) {
        if (op.value === "missing") return { comparison: s.slice(2), missing: true };
        return op.value === "is" ? { comparison: s.slice(2), is: v } : { comparison: s.slice(2), is_not: v };
      }
      if (s === "score") return { score: "probability", op: op.value, value: Math.min(100, Math.max(0, Number(v))) / 100 };
      return { field: "dob_year", op: "abs_diff_gt", value: Number(v) };
    };
    return row;
  };
  conds.append(condRow());
  const ruleName = el("input", { type: "text", maxlength: "120", placeholder: "for example: Different employee numbers" });
  const err = errBox();
  const body = el("div", { class: "rule-builder" }, field("What should happen", what),
    el("div", { class: "row when" }, el("strong", {}, "When"), join), conds,
    el("div", { class: "row" }, btn("Add a condition", () => conds.append(condRow()), "small", "plus")),
    field("Name (shown to reviewers)", ruleName), err);
  return dialog("Add your own rule", body, "Add rule", () => {
    const parts = [...conds.children].map((r) => r.build());
    if (!parts.length) { fail(err, "Add at least one condition."); return undefined; }
    if (ruleName.value.trim().length < 3) { fail(err, "Give the rule a name people will understand.", ruleName); return undefined; }
    const slug = ruleName.value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "rule";
    let id = "custom_" + slug;
    for (let i = 2; existing.some((r) => r.id === id); i++) id = "custom_" + slug + "_" + i;
    return { id, type: what.value, reason: ruleName.value.trim(), when: parts.length === 1 ? parts[0] : { [join.value]: parts } };
  });
}

// ---- golden record in plain words ------------------------------------------------------------------------------------------
const GOLDEN_STRATEGIES = [["most_recent", "Newest record"], ["most_trusted_source", "Most trusted source"], ["most_frequent", "Most records agree"],
  ["most_complete", "Most complete"], ["best_quality", "Best quality"]];
const strategyName = (k) => (GOLDEN_STRATEGIES.find(([x]) => x === k) || [k, label(k)])[1];
function goldenSummary(content, guide) {
  const trust = (content.source_trust || []).length ? el("ol", { class: "order plain-order" }, content.source_trust.map((s) => el("li", { class: "mono" }, s)))
    : el("p", {}, "Ranked automatically: the source whose records are most complete comes first.");
  const groups = content.groups || {};
  const grouped = new Set(Object.values(groups).flat());
  const units = [...Object.entries(groups).map(([g, m]) => [g, label(g) + " (" + m.map(label).join(", ") + ")"]), ...guide.fields.filter((f) => !grouped.has(f)).map((f) => [f, label(f)])];
  const def = content.default_strategy || "most_recent";
  const byRule = {};
  for (const [u, text] of units) {
    const rule = (content.fields || {})[u] || def;
    const pref = ((content.field_trust || {})[u] || [])[0];
    (byRule[rule] = byRule[rule] || []).push(text + (pref ? " (prefers " + pref + ")" : ""));
  }
  return el("div", { class: "matching-view" },
    step(1, "Which source is trusted most", "Used when a field's rule is 'Most trusted source', and to break ties.", trust),
    step(2, "How each field is chosen", null, el("div", { class: "rule-group" }, Object.entries(byRule).map(([rule, fields]) =>
      el("div", { class: "rule" }, el("div", { class: "rule-text" }, el("strong", {}, strategyName(rule)), el("p", { class: "muted" }, fields.join(", ")),
        guide.strategies[rule] ? el("details", {}, el("summary", {}, "What this means"), el("p", {}, guide.strategies[rule])) : null))))),
    step(3, "Other choices", null, kv([["Extra columns of the sources", content.carry_extra_columns === false ? "Not carried into the golden record" : "Carried into the golden record"],
      ["Shared phones and emails", "A value shared by more than " + (content.shared_value_limit || 3) + " people is used only when nothing else exists"]])));
}
// The golden-record rules as a form: which source to trust, where each record's date comes from, and how each field is chosen.
function goldenForm(content, guide, propose, cancel) {
  const known = guide.sources.map((s) => s.id);
  // start from the configured order, then the order the latest run actually used, then anything new
  const seen = ((guide.last_run || {}).trust_order || []).filter((s) => known.includes(s));
  let order = [...new Set([...(content.source_trust || []).filter((s) => known.includes(s)), ...seen, ...known])];
  const auto = el("input", { type: "checkbox", checked: !(content.source_trust || []).length });
  const list = el("ol", { class: "order" });
  const move = (i, d) => {
    [order[i + d], order[i]] = [order[i], order[i + d]];
    drawOrder();
    const b = list.children[i + d].querySelectorAll("button");            // keep focus on the moved source
    (b[d < 0 ? 0 : 1].disabled ? b[d < 0 ? 1 : 0] : b[d < 0 ? 0 : 1]).focus();
  };
  const drawOrder = () => {
    list.replaceChildren(...order.map((s, i) => el("li", {}, el("span", { class: "muted", "aria-hidden": "true" }, (i + 1) + "."), el("span", { class: "mono" }, s),
      btn("Up", () => move(i, -1), "small", null, { disabled: auto.checked || i === 0, "aria-label": "Move " + s + " up" }),
      btn("Down", () => move(i, 1), "small", null, { disabled: auto.checked || i === order.length - 1, "aria-label": "Move " + s + " down" }))));
  };
  auto.addEventListener("change", drawOrder);
  drawOrder();
  const last = guide.last_run;
  const lastNote = last && last.trust_order ? el("p", { class: "hint" }, "The latest run (" + last.run_id + ") used this order: " + last.trust_order.join(", ") +
    (last.auto_ranked_sources && last.auto_ranked_sources.length ? ". Ranked automatically: " + last.auto_ranked_sources.join(", ") + "." : ".")) : null;

  // record dates: the mapped record date, or one of the connection's extra columns
  const dateSel = {};
  const dateRows = guide.sources.map((s) => {
    const sel = el("select", { "aria-label": "Record date for " + s.id },
      el("option", { value: "" }, s.record_date_mapped ? "The mapped record date" : "No date (cannot be 'newest')"),
      s.attributes.map((a) => el("option", { value: a, selected: (content.source_dates || {})[s.id] === a }, "Extra column: " + a)));
    dateSel[s.id] = sel;
    return el("tr", {}, el("td", { class: "mono" }, s.id), el("td", {}, sel));
  });
  const undated = last && last.sources_without_dates && last.sources_without_dates.length
    ? el("div", { class: "notice warn", role: "note" }, "In the latest run these sources had no usable record date, so 'Newest record' could not use them: " + last.sources_without_dates.join(", ") +
      ". Map a record date on the connection, or pick a date column here.") : null;

  // one row per field, or per group of fields that must come from the same record
  const groups = content.groups || {};
  const grouped = new Set(Object.values(groups).flat());
  const units = [...Object.entries(groups).map(([g, m]) => [g, label(g) + " (" + m.join(", ") + ")"]), ...guide.fields.filter((f) => !grouped.has(f)).map((f) => [f, label(f)])];
  const ruleSel = {};
  const preferSel = {};
  const fieldRows = units.map(([u, text]) => {
    const current = (content.fields || {})[u] || content.default_strategy || "most_recent";
    ruleSel[u] = el("select", { "aria-label": "Rule for " + text }, GOLDEN_STRATEGIES.map(([k, t]) => el("option", { value: k, selected: k === current }, t)));
    const pref = ((content.field_trust || {})[u] || [])[0] || "";
    preferSel[u] = el("select", { "aria-label": "Preferred source for " + text }, el("option", { value: "" }, "Overall order"),
      known.map((s) => el("option", { value: s, selected: s === pref }, s)));
    return el("tr", {}, el("td", {}, text), el("td", {}, ruleSel[u]), el("td", {}, preferSel[u]));
  });
  const carry = el("input", { type: "checkbox", checked: content.carry_extra_columns !== false });
  const extraSel = {};
  const extraRows = (guide.extra_columns || []).map((x) => {
    const cur = (content.extra_fields || {})[x.name] || content.extra_default_strategy || "most_recent";
    extraSel[x.name] = x.carried ? el("select", { "aria-label": "Rule for extra column " + x.name }, GOLDEN_STRATEGIES.map(([k, t]) => el("option", { value: k, selected: k === cur }, t))) : null;
    return el("tr", {}, el("td", {}, x.name), el("td", { class: "mono" }, x.carried ? x.column : ""), el("td", {}, x.sources.join(", ")),
      el("td", {}, extraSel[x.name] || el("span", { class: "bad" }, "Not carried: rename the column (letters, digits, space, _ . - / ( ) only)")));
  });
  const shared = el("input", { type: "number", min: "1", max: "1000", value: String(content.shared_value_limit || 3) });
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  const save = async () => {
    fail(err, "");
    const fields = {};
    const fieldTrust = {};
    for (const [u] of units) {
      fields[u] = ruleSel[u].value;
      if (preferSel[u].value) fieldTrust[u] = [preferSel[u].value];
    }
    const dates = {};
    for (const [s, sel] of Object.entries(dateSel)) if (sel.value) dates[s] = sel.value;
    const extraFields = {};
    for (const [n, sel] of Object.entries(extraSel)) if (sel && sel.value !== "most_recent") extraFields[n] = sel.value;
    const out = { ...content, source_trust: auto.checked ? [] : order, source_dates: dates, fields, field_trust: fieldTrust, shared_value_limit: Number(shared.value),
      carry_extra_columns: carry.checked, extra_fields: extraFields };
    try { await propose(out, why.value); } catch (e) { fail(err, e.message); }
  };
  return el("div", { class: "matching-view golden-form editing" },
    step(1, "Which source is trusted most", null,
      el("label", { class: "check" }, auto, el("span", {}, "Rank sources automatically (the source whose records are most complete first). Untick to set the order yourself.")),
      known.length ? list : empty("This project has no live connection yet."), lastNote),
    step(2, "How old each record is", "'Newest record' needs a date per record: when it was created or last updated.",
      undated, guide.sources.length ? table(["Source", "Record date"], dateRows) : null),
    step(3, "How each field is chosen", null, table(["Field", "Rule", "Preferred source"], fieldRows),
      el("details", {}, el("summary", {}, "What the rules mean"), el("dl", { class: "help" }, GOLDEN_STRATEGIES.map(([k, tt]) => [el("dt", {}, tt), el("dd", {}, guide.strategies[k] || "")]).flat()))),
    step(4, "Your own columns", null,
      el("label", { class: "check" }, carry, el("span", {}, "Carry the sources' extra columns into the golden record (columns with the same name in different sources become one field)")),
      extraRows.length ? table(["Column", "Golden-record column", "Sources", "Rule"], extraRows) : el("p", { class: "hint" }, "No connection keeps extra columns. Tick them in a connection's field mapping to carry them.")),
    step(5, "Shared values", null,
      field("A phone or email shared by more than this many people (an office line, an agent) is used only when nothing else exists", shared)),
    saveBar(why, err, save, cancel));
}

// ---- connections (they belong to a project) -------------------------------------------------------------------------------
// The most used sources, shown first on the Connections tab; every other one is on the "All sources" page.
const POPULAR = ["upload", "xlsx", "csv", "postgres", "oracle", "sqlserver", "mysql", "sftp", "s3", "salesforce"];
const TESTED = { live: ["Tested here", "success", "Read from real files or a real server in the test suite."],
  simulated: ["Tested with a stand-in", "info", "Checked against a local server that speaks the same protocol, not the vendor's own service."],
  unverified: ["Built from the vendor's documentation", "warn", "Not yet run against the real service. Test it with your administrator before relying on it."] };
let connectorCatalogue = null;
async function catalogue() {
  if (!connectorCatalogue) connectorCatalogue = await api("GET", "/connectors");
  return connectorCatalogue;
}
function sourceTile(c, onpick) {
  const t = TESTED[c.tested];
  const b = el("button", { type: "button", class: "tile source", onclick: onpick, "data-search": (c.title + " " + c.summary + " " + c.type + " " + c.group).toLowerCase() },
    el("img", { class: "logo-chip big", src: "/ui/logos/" + c.logo + ".svg", alt: "", width: "32", height: "32" }),
    el("span", {}, el("strong", {}, c.title), el("span", { class: "muted" }, c.summary),
      el("span", { class: "tile-tags" }, t ? tag(t[0], t[1]) : null, c.installed ? null : tag("Driver not installed", "danger"))));
  return b;
}
async function addDataPage(pid) {
  here(P(pid) + "/add-data");
  const cat = await catalogue();
  const pick = (type) => run(() => connectionWizard(pid, type));
  const q = el("input", { type: "search", placeholder: "Search sources: oracle, excel, s3, salesforce...", "aria-label": "Search sources", class: "source-search" });
  const sections = cat.groups.map((g) => {
    const items = cat.catalogue.filter((c) => c.group === g);
    return el("section", { class: "source-group", "data-group": g }, el("h2", { class: "section" }, g, el("span", { class: "count-badge" }, String(items.length))),
      el("div", { class: "tiles" }, items.map((c) => sourceTile(c, () => pick(c.type)))));
  });
  const none = el("p", { class: "muted", hidden: true }, "No source matches. Try another word, or use REST API or Other database (connection URL) for anything not listed.");
  q.addEventListener("input", () => {
    const words = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const s of sections) {
      let n = 0;
      for (const t of s.querySelectorAll(".tile")) { const ok = words.every((w) => t.dataset.search.includes(w)); t.hidden = !ok; if (ok) n++; }
      s.hidden = !n;
      shown += n;
    }
    none.hidden = shown > 0;
  });
  const legend = el("div", { class: "legend" }, Object.values(TESTED).map(([text, kind, help]) => el("span", {}, tag(text, kind), " ", el("span", { class: "muted" }, help))));
  show(page("Add data", "Choose where the data comes from. " + cat.catalogue.length + " kinds of source: files, databases, warehouses, cloud storage and applications. "
    + "Every source is read-only, and passwords are never typed here: they stay in environment variables on the server.",
  [btn("Back to connections", () => go(() => viewProject(pid, "connections")), "", "prev")],
  crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Connections", P(pid) + "/connections"), "Add data")),
  el("div", { class: "row" }, icon("search"), q), legend, none, sections);
  q.focus();
}
async function projectConnections(p, connections) {
  const pid = p.id;
  const uploads = can("sources.manage") ? await api("GET", P(pid) + "/uploads") : [];
  const manage = can("sources.manage");
  const cat = manage ? await catalogue() : null;
  const rows = connections.map((c) => linkRow(c.id, () => viewConnection(pid, c.id), el("td", {}, typeCell(c.type)),
    el("td", { class: "num" }, c.fields_mapped), el("td", {}, c.live_version ? "Version " + c.live_version : el("span", { class: "muted" }, "Not live")),
    el("td", {}, c.latest_status === "active" ? "" : statusTag(c.latest_status)),
    el("td", {}, c.last_check ? tag(c.last_check.ok ? "OK" : "Failed", c.last_check.ok ? "success" : "danger") : el("span", { class: "muted" }, "never")), el("td", {}, when(c.updated_at)),
    manage ? el("td", { class: "actions-cell" }, btn("Edit", () => run(() => editConnection(pid, c.id)), "small", "edit", { "aria-label": "Edit connection " + c.id })) : null));
  const copy = can("sources.manage") ? btn("Copy from another project", () => copyDialog(pid), "", "folder") : null;
  const removeUpload = async (u) => {
    if (!(await confirmDialog("Delete " + u.filename + "?", "The uploaded file is erased for good. No connection uses it.", "Delete file", true))) return;
    await api("DELETE", P(pid) + "/uploads/" + u.upload_id);
    await go(() => viewProject(pid, "connections"));
    say(u.filename + " was deleted.", "good");
  };
  return el("div", {},
    manage ? el("div", { class: "title-row" }, el("h2", { class: "section" }, "Add data"),
      btn("All " + cat.catalogue.length + " sources", () => go(() => addDataPage(pid)), "", "grid")) : null,
    manage ? el("div", { class: "tiles" }, POPULAR.map((t) => cat.catalogue.find((c) => c.type === t)).filter(Boolean)
      .map((c) => sourceTile(c, () => run(() => connectionWizard(pid, c.type))))) : null,
    el("div", { class: "title-row" }, el("h2", { class: "section" }, "This project's connections"), copy),
    rows.length ? table(["Connection", "Type", ["Fields"], "Live", "Pending", "Last check", "Updated", ...(manage ? [""] : [])], rows)
      : empty(can("sources.manage") ? "No connections yet. Add your first data source above; it belongs to this project only." : "No connections yet. Someone who manages data sources adds them."),
    connections.length ? coverage(connections) : null,
    uploads.length ? el("h2", { class: "section" }, "Uploaded files") : null,
    uploads.length ? table(["File", ["Rows"], "Used by", "Uploaded", "By", ""], uploads.map((u) => el("tr", {}, el("td", {}, u.filename), el("td", { class: "num" }, num(u.rows)),
      el("td", {}, u.used_by.length ? u.used_by.join(", ") : el("span", { class: "muted" }, "nothing")), el("td", {}, when(u.created_at)), el("td", {}, u.uploaded_by),
      el("td", { class: "actions-cell" }, u.used_by.length ? null : btn("Delete", () => removeUpload(u), "small danger"))))) : null);
}
async function copyDialog(pid) {
  const others = cache.projects.filter((x) => x.id !== pid);
  if (!others.length) { say("You have no other project to copy from.", "bad"); return; }
  const from = el("select", {}, others.map((x) => el("option", { value: x.id }, x.name)));
  const which = el("select", {});
  const newId = el("input", { type: "text", maxlength: "40" });
  const err = errBox();
  const load = async () => {
    which.replaceChildren();
    try {
      const list = await api("GET", P(from.value) + "/connections");
      which.append(...list.filter((c) => c.type !== "upload").map((c) => el("option", { value: c.id }, c.id + " (" + label(c.type) + ")")));
      fail(err, which.children.length ? "" : "That project has no connection that can be copied (uploaded files stay with their project).");
    } catch (e) { fail(err, e.message); }
  };
  from.addEventListener("change", load);
  await load();
  const done = await dialog("Copy a connection", el("div", {}, el("p", { class: "hint" }, "The copy is independent: later changes to either one do not affect the other. It starts as a proposal in this project."),
    field("From project", from), field("Connection", which), field("New id here (leave empty to keep the same)", newId), err), "Copy", async () => {
    try {
      return await api("POST", P(pid) + "/connections/copy", { from_project: from.value, connection_id: which.value, new_id: newId.value.trim() || null });
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (done) { await go(() => viewConnection(pid, done.id)); say("Copied as version " + done.version + ". Check the mapping, then make it live.", "good"); }
}
// Which of our fields each connection fills, and from which of its columns: the whole project's data at a glance.
const CANON_ORDER = ["full_name", "father_name", "dob", "age", "gender", "marital_status", "mobile", "mobile_alt", "email", "address", "address_alt", "city", "state", "pincode",
  "customer_number", "pan", "aadhaar", "aadhaar_last4", "aadhaar_reference_key", "passport_no", "dl_no", "voter_id", "record_date", "occupation", "annual_income", "related_name",
  "party_role", "relation_to_proposer"];
function coverage(connections) {
  const used = CANON_ORDER.filter((f) => connections.some((c) => (c.fields || {})[f]));
  if (!used.length) return null;
  const extra = connections.filter((c) => (c.extra_columns || []).length);
  return el("div", {}, el("h2", { class: "section" }, "Which field comes from which source"),
    el("p", { class: "hint" }, "Each column shows the column of that source that fills the field (latest version). Records of all sources are matched with each other on these fields."),
    table(["Field", ...connections.map((c) => c.id)], [
      ...used.map((f) => el("tr", {}, el("td", {}, fieldTitle(f)), connections.map((c) => el("td", {}, (c.fields || {})[f] ? el("span", { class: "mono" }, c.fields[f]) : el("span", { class: "muted" }, "-"))))),
      el("tr", {}, el("td", {}, "Record id"), connections.map((c) => el("td", {}, c.record_id ? el("span", { class: "mono" }, c.record_id) : el("span", { class: "muted" }, "row number")))),
      extra.length ? el("tr", {}, el("td", {}, "Extra columns kept"), connections.map((c) => el("td", {}, (c.extra_columns || []).join(", ") || el("span", { class: "muted" }, "-")))) : null,
    ]));
}
async function editConnection(pid, cid) {
  const c = await api("GET", P(pid) + "/connections/" + cid);
  await connectionWizard(pid, c.latest.connector.type, JSON.parse(JSON.stringify(c.latest)), 2);
}
function mappingTables(content, title) {
  const mapRows = Object.entries(content.fields || {}).map(([f, col]) => el("tr", {}, el("td", {}, label(f)), el("td", {}, typeof col === "string" ? col : JSON.stringify(col))));
  const settings = Object.entries(content.connector).filter(([k]) => k !== "type").map(([k, v]) => [label(k), el("span", { class: "mono" }, String(v))]);
  return el("div", { class: "cols" }, el("div", {}, el("h3", { class: "section" }, "Settings" + title), settings.length ? kv(settings) : empty("None.")),
    el("div", {}, el("h3", { class: "section" }, "Field mapping" + title), table(["Field", "Source column"], mapRows)));
}
async function viewConnection(pid, cid) {
  here(P(pid) + "/connections/" + cid);
  const base = P(pid) + "/connections/" + cid;
  const c = await api("GET", base);
  const latest = c.versions[c.versions.length - 1];
  const live = c.versions.find((v) => v.status === "active");
  const second = c.second_approval;
  const pending = latest.status === "proposed" || latest.status === "approved";
  const act = async (v, what, title, minLength, prompt, danger) => {
    const why = await askReason(title, prompt, minLength, title.split(" ")[0], danger);
    if (why === null) return;
    await api("POST", base + "/versions/" + v.version + "/" + what, { reason: why });
    await go(() => viewConnection(pid, cid));
    say(title + ": done." + (what === "activate" ? " Runs read this version from now on." : ""), "good");
  };
  const mine = (v) => v.proposed_by === me.sub;
  const buttons = (v) => [
    second && can("config.approve") && v.status === "proposed" && !mine(v) ? btn("Approve", () => act(v, "approve", "Approve version " + v.version, 0, "Note"), "small") : null,
    can("config.approve") && (v.status === "proposed" || v.status === "approved") ? btn("Decline", () => act(v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger") : null,
    can("config.activate") && (v.status === "proposed" || v.status === "approved") && (!second || v.approved_by.length) ? btn("Make live", () => act(v, "activate", "Make version " + v.version + " live", 0, "Note"), "small primary") : null,
    second && v.status === "proposed" && mine(v) ? tag("Yours: someone else approves it", "warn") : null,
  ].filter(Boolean);
  const changes = pending && live ? await api("GET", base + "/diff?old=" + live.version + "&new=" + latest.version) : null;
  const shown = c.live || c.latest;
  show(page(cid, typeTitle(shown.connector.type) + " connection. " + (live ? "Live version " + live.version + "." : "Not live yet."),
    can("sources.manage") ? [btn("Edit", () => run(() => editConnection(pid, cid)), "", "edit")] : [],
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Connections", P(pid) + "/connections"), cid), statusTag(latest.status),
    logo(shown.connector.type, "big")),
  pending ? el("div", { class: "notice", role: "note" }, el("strong", {}, "Version " + latest.version + " waits to be made live. "),
    live ? (changes && changes.length ? "Compared with live version " + live.version + ": " + changes.join("; ") + "." : "It reads the same as the live version.") : "There is no live version yet.") : null,
  el("h2", { class: "section" }, "Versions"),
  table(["Version", "Status", "Proposed by", "Reason", "Approved by", ""], c.versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version),
    el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by), el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")), el("td", { class: "actions-cell" }, buttons(v))))),
  pending ? el("h2", { class: "section" }, "Version " + latest.version + " (waiting)") : null,
  pending ? mappingTables(c.latest, "") : null,
  live ? el("h2", { class: "section" }, "Live version " + live.version) : null,
  live ? mappingTables(c.live, "") : null,
  el("div", { class: "title-row" }, el("h2", { class: "section" }, "Health"), can("sources.manage") ? btn("Test now", async () => {
    const t = await api("POST", base + "/test");
    await go(() => viewConnection(pid, cid));
    say(t.ok ? "The source reads fine (" + num(t.rows) + " sample rows). " + driftText(t.drift) : "The source could not be read: " + t.error, t.ok ? "good" : "bad");
  }, "", "refresh") : null),
  c.health.length ? table(["When", "Check", "Version", "Result", ["Rows"], "Changes in the source", "By"], c.health.map((h) => el("tr", {}, el("td", {}, when(h.checked_at)),
    el("td", {}, h.kind === "run" ? "A run" : "Test now"), el("td", {}, h.version == null ? "" : "Version " + h.version), el("td", {}, h.ok ? tag("OK", "success") : tag("Failed", "danger")),
    el("td", { class: "num" }, h.rows == null ? "" : num(h.rows)), el("td", {}, h.ok ? driftText(h.drift) : (h.error || "")), el("td", {}, h.checked_by))))
    : empty("Not checked yet. Press Test now, or start a run."));
}
const driftText = (d) => ((d && ((d.added || []).length || (d.removed || []).length))
  ? [(d.added || []).length ? "Added: " + d.added.join(", ") : "", (d.removed || []).length ? "Removed: " + d.removed.join(", ") : ""].filter(Boolean).join(". ") : "");

// The add-a-connection wizard: 1 connect (or upload), 2 map the columns, 3 preview and save. Every step can be clicked once the source is open.
// Our fields in words, and what each is used for, shown next to it while mapping.
const FIELD_TITLE = { full_name: "Full name", father_name: "Father's or husband's name", dob: "Date of birth", age: "Age", gender: "Gender", marital_status: "Marital status",
  mobile: "Mobile", mobile_alt: "Second phone", email: "Email", address: "Address", address_alt: "Second address", city: "City", state: "State", pincode: "PIN code",
  customer_number: "Customer number (shared)", pan: "PAN", aadhaar: "Aadhaar number", aadhaar_last4: "Aadhaar last 4 digits", aadhaar_reference_key: "Aadhaar vault reference",
  passport_no: "Passport number", dl_no: "Driving licence", voter_id: "Voter ID", record_date: "Record date", occupation: "Occupation", annual_income: "Annual income",
  related_name: "Related person's name", party_role: "Role on the record", relation_to_proposer: "Relation to the proposer" };
const fieldTitle = (f) => FIELD_TITLE[f] || label(f);
const FIELD_ROLE = {
  full_name: ["Matching", "compared on every pair: spelling, word order, initials"], father_name: ["Golden record", "and to tell relatives apart"],
  dob: ["Matching", "a strong sign when it agrees"], age: ["Matching", "used when there is no birth date"], gender: ["Matching", "a difference stops a merge"],
  marital_status: ["Golden record", ""], mobile: ["Matching", ""], mobile_alt: ["Matching", ""], email: ["Matching", ""],
  address: ["Matching", "with the PIN code"], address_alt: ["Households", ""], city: ["Matching", ""], state: ["Golden record", ""], pincode: ["Matching", ""],
  customer_number: ["Identifier", "can decide on its own if you choose so below"], pan: ["Identifier", "decides on its own"],
  aadhaar: ["Identifier", "kept only as a keyed token, never stored"], aadhaar_last4: ["Identifier", "weak: last four digits only"],
  aadhaar_reference_key: ["Identifier", "your Aadhaar Data Vault key"], passport_no: ["Identifier", "strong evidence"], dl_no: ["Identifier", "strong evidence"],
  voter_id: ["Identifier", "strong evidence"], record_date: ["Newest value", "decides which value is the most recent"], occupation: ["Golden record", ""],
  annual_income: ["Golden record", ""], related_name: ["Households", "links family members"], party_role: ["Households", ""], relation_to_proposer: ["Households", ""],
};
const ROLE_KIND = { Matching: "info", Identifier: "success", "Newest value": "warn" };
const SUGGESTED_BY = { name: "Suggested from the column name", values: "Suggested from the values", "name and values": "Suggested: name and values agree" };
// Identifiers a project can let decide on their own: the matching rules they switch on (same value -> same person; different -> never).
const KEY_RULES = {
  pan: ["PAN", "auto_same_pan_compatible_name", "veto_pan_conflict"],
  aadhaar: ["Aadhaar", "auto_same_aadhaar_compatible_name", "veto_aadhaar_conflict"],
  customer_number: ["customer number", "auto_same_customer_number", "veto_customer_number_conflict"],
};
const KEY_FIELDS = { pan: ["pan"], aadhaar: ["aadhaar", "aadhaar_reference_key"], customer_number: ["customer_number"] };
async function decisionStart(pid) {
  if (!can("config.read") || !can("config.propose")) return { rules: [] };      // cannot change the project's rules: the choice is not offered
  const [vs, defaults] = await Promise.all([api("GET", P(pid) + "/config/decision"), api("GET", P(pid) + "/config/decision/default")]);
  const content = vs.length ? (await api("GET", P(pid) + "/config/decision/versions/" + vs[vs.length - 1].version)).content : defaults.content;
  return { ...content, _defaults: defaults.content.rules };
}
const keyOn = (content, k) => (content.rules || []).some((r) => r.id === KEY_RULES[k][1] && r.enabled !== false);
function withKeys(content, keys) {
  const { _defaults, ...out } = JSON.parse(JSON.stringify(content));
  for (const [k, on] of Object.entries(keys)) {
    for (const id of [KEY_RULES[k][1], KEY_RULES[k][2]]) {
      let r = out.rules.find((x) => x.id === id);
      if (!r && on) { r = (_defaults || []).find((x) => x.id === id); if (r) out.rules.push(r); }
      if (r) r.enabled = on;
    }
  }
  return out;
}
// A connector's settings form, built from the catalogue: the right control for each setting, help under it, settings that only apply to another
// choice hidden (show_if), rarely needed ones under "More settings", secrets only as the NAME of an environment variable.
function settingsForm(spec, conn, changed, rerender) {
  const value = (s) => (conn[s.name] !== undefined ? conn[s.name] : s.default);
  const visible = (s) => Object.entries(s.show_if || {}).every(([k, vals]) => vals.includes(value(spec.settings.find((x) => x.name === k)) || ""));
  const control = (s) => {
    let i;
    if (s.kind === "select") {
      i = el("select", {}, s.required || s.default != null ? null : el("option", { value: "" }, "(not set)"),
        s.choices.map((c) => el("option", { value: c, selected: c === value(s) }, c === "\t" ? "Tab" : c)));
    } else if (s.kind === "textarea") {
      i = el("textarea", { rows: "3", placeholder: s.placeholder || "", spellcheck: "false" }, value(s) || "");
    } else {
      i = el("input", { type: s.kind === "number" ? "number" : "text", value: value(s) || "", placeholder: s.placeholder || (s.kind === "env" ? "DEDUPE_SRC_..." : ""),
        spellcheck: "false", autocomplete: "off", class: s.kind === "env" || s.kind === "path" ? "mono" : null });
    }
    const set = () => { changed(); if (i.value !== "") conn[s.name] = i.value; else delete conn[s.name]; };
    i.addEventListener(s.kind === "select" ? "change" : "input", () => {
      set();
      if (s.kind === "select" && spec.settings.some((x) => x.show_if && x.show_if[s.name])) rerender();
    });
    if (s.kind === "env") i.addEventListener("blur", () => { if (i.value && !/^DEDUPE_SRC_[A-Z0-9_]{1,60}$/.test(i.value)) fail(err, "Use a variable name such as DEDUPE_SRC_CRM_PASSWORD.", i); else fail(err, "", i); });
    const err = errBox();
    const box = field(s.label + (s.required ? " *" : ""), i);
    if (s.kind === "env") box.querySelector("label").prepend(icon("key"));
    if (s.help) box.append(el("span", { class: "help" }, s.help));
    box.append(err);
    if (s.kind === "textarea") box.classList.add("full");
    return box;
  };
  for (const s of spec.settings) if (conn[s.name] === undefined && s.default != null && s.name !== "upload_id") conn[s.name] = s.default;
  const shown = spec.settings.filter((s) => s.name !== "upload_id" && visible(s));
  const main = shown.filter((s) => !s.advanced);
  const more = shown.filter((s) => s.advanced);
  const t = TESTED[spec.tested];
  return [
    el("div", { class: "source-facts" }, t ? tag(t[0], t[1]) : null, t ? el("span", { class: "muted" }, t[2]) : null,
      spec.docs ? el("a", { href: spec.docs, target: "_blank", rel: "noopener noreferrer" }, icon("book"), "Driver documentation", icon("external")) : null),
    spec.installed ? null : el("div", { class: "notice bad", role: "note" }, icon("alert"), " This server does not have the driver for " + spec.title + " yet. Ask the administrator to run ",
      el("code", {}, spec.install), ", then restart the service."),
    spec.note ? el("div", { class: "notice warn", role: "note" }, icon("info"), " " + spec.note) : null,
    el("p", { class: "hint" }, icon("lock"), " Read-only. Passwords, keys and tokens are never typed here: give the name of an environment variable on the server "
      + "that holds them (it must start with DEDUPE_SRC_). Fields marked * are required."),
    el("div", { class: "fields settings" }, main.map(control)),
    more.length ? el("details", { class: "advanced" }, el("summary", {}, "More settings (" + more.length + ")"), el("div", { class: "fields settings" }, more.map(control))) : null,
  ];
}
async function connectionWizard(pid, type, preset, startStep) {
  const opts = await catalogue();
  const spec = opts.catalogue.find((c) => c.type === type || c.aliases.includes(type));
  const root = P(pid);
  const state = { source: preset || { source_id: "", connector: { type }, record_id: null, fields: {}, attributes: [] }, info: null, step: 1, uploadName: null,
    sample: null, reason: null, decision: null, keys: null };
  const out = el("div");
  const kind = () => state.source.connector.type;
  focusNext = true;
  const goStep = async (n) => {
    if (n > 1 && !state.info) { await openSource(n); return; }
    state.step = n;
    render();
  };
  const render = () => {
    const bar = steps(["Connect", "Map columns", "Preview and save"], state.step, (n) => run(() => goStep(n)));
    const header = page(preset ? "Edit connection " + state.source.source_id : "Add data: " + (spec ? spec.title : typeTitle(type)),
      spec ? spec.summary : "", [btn("Cancel", () => go(() => (preset ? viewConnection(pid, state.source.source_id) : addDataPage(pid))))],
      crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Connections", P(pid) + "/connections"), preset ? state.source.source_id : "Add data"),
      null, logo(kind(), "big"));
    if (state.step === 1) show(header, bar, connectStep());
    else if (state.step === 2) show(header, bar, mapStep());
    else show(header, bar, previewStep(), out);
  };
  const idField = () => {
    const i = el("input", { type: "text", value: state.source.source_id, maxlength: "40", placeholder: "For example bank_crm", disabled: preset ? true : null });
    i.addEventListener("input", () => { state.source.source_id = i.value.trim(); });
    return field("Connection id (stamped on every record it delivers: letters, digits, _ and -)", i);
  };
  const inspectUrl = () => root + "/connections/inspect" + (state.reason ? "?" + unmaskQuery(state.reason) : "");
  const openSource = async (target) => {
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(state.source.source_id)) throw new Error("Give the connection an id of letters, digits, _ or -.");
    if (kind() === "upload" && !state.source.connector.upload_id) throw new Error("Upload a file first.");
    const [info, decision] = await Promise.all([api("POST", inspectUrl(), { connector: state.source.connector }), state.decision ? state.decision : decisionStart(pid)]);
    state.info = info;
    state.decision = decision;
    if (!state.keys) state.keys = Object.fromEntries(Object.keys(KEY_RULES).map((k) => [k, keyOn(decision, k)]));
    if (!Object.keys(state.source.fields).length) {
      for (const s of state.info.suggestions) state.source.fields[s.field] = s.column;
      if (!state.source.record_id && state.info.record_id) state.source.record_id = state.info.record_id;
    }
    state.step = target || 2;
    render();
  };
  // show the sample as it is (needs pii.unmask; every view is logged); masked otherwise
  const revealButton = (then) => (can("pii.unmask") && !state.reason ? btn("Show actual values", async () => {
    const why = await revealIdentifiers("Show the actual values");
    if (why === null) return;
    state.reason = why;
    await then();
  }, "small", "shield") : null);
  function connectStep() {
    const parts = [el("div", { class: "fields" }, idField())];
    if (kind() === "upload") {
      const input = el("input", { type: "file", accept: ".csv,.tsv,.txt,.xlsx,.json,.jsonl,.ndjson,.parquet", hidden: true });
      const status = el("div", { role: "status" }, state.source.connector.upload_id ? "Uploaded " + (state.uploadName || state.source.connector.upload_id) + "."
        : "CSV, TSV, Excel (.xlsx), JSON, JSON Lines or Parquet. A text or Excel file's first row must hold the column names.");
      const zone = el("div", { class: "dropzone" }, icon("upload"), el("strong", {}, "Drop a file here"), btn("Choose a file", () => input.click(), "small"), status);
      const send = (f) => run(async () => {
        status.textContent = "Uploading and checking " + f.name + "...";
        const r = await api("POST", root + "/uploads?filename=" + encodeURIComponent(f.name), f, { "Content-Type": "application/octet-stream" }, true);
        state.source.connector = { type: "upload", upload_id: r.upload_id };
        state.info = null;
        state.uploadName = r.filename + " (" + num(r.rows) + " rows, " + r.columns.length + " columns, stored encrypted)";
        if (!state.source.source_id) state.source.source_id = r.filename.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "_").slice(0, 40);
        render();
      });
      input.addEventListener("change", () => { if (input.files[0]) send(input.files[0]); });
      zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); if (e.dataTransfer.files[0]) send(e.dataTransfer.files[0]); });
      parts.push(zone, input);
    } else if (spec) {
      parts.push(...settingsForm(spec, state.source.connector, () => { state.info = null; }, () => render()));
    } else {                                     // a type the catalogue does not describe (an old connection): plain text settings
      const grid = el("div", { class: "fields" });
      for (const k of (opts.connectors[kind()] || { settings: [] }).settings) {
        const i = el("input", { type: "text", value: state.source.connector[k] || "" });
        i.addEventListener("input", () => { state.info = null; if (i.value) state.source.connector[k] = i.value; else delete state.source.connector[k]; });
        grid.append(field(label(k), i));
      }
      parts.push(grid);
    }
    parts.push(el("div", { class: "row" }, btn(state.info ? "Next: map columns" : "Open the source and suggest a mapping", () => goStep(2), "primary", "next")));
    return el("div", {}, parts);
  }
  function mapStep() {
    const cols = state.info.columns;
    const sample = state.info.sample;
    const suggested = Object.fromEntries(state.info.suggestions.map((x) => [x.field, x]));
    const types = state.info.types || {};
    const examples = (c) => (c ? [...new Set(sample.map((r) => r[c]).filter((v) => v != null && v !== ""))].slice(0, 3) : []);
    // what the values look like (detected from the whole sample, not from the column's name); a warning when they clearly hold something else
    const looksLike = (c, f) => {
      const t = c && types[c];
      if (!t || !t.type) return null;
      return f && (t.contradicts || []).includes(f) ? el("div", {}, tag("Values look like: " + t.description, "warn"))
        : el("div", { class: "muted small" }, "Values look like: " + t.description + " (" + Math.round(t.share * 100) + "%)");
    };
    const exampleCell = (c, f) => el("span", {}, el("span", { class: "examples" }, examples(c).map((v) => el("span", { class: "ex" }, String(v)))), looksLike(c, f));
    const pick = (value, onchange, text) => {
      const s = el("select", { "aria-label": text }, el("option", { value: "" }, "Not in this source"), cols.map((c) => el("option", { value: c, selected: c === value }, c)));
      s.addEventListener("change", () => onchange(s.value));
      return s;
    };
    const groups = opts.field_groups.map((g) => {
      const rows = g.fields.map((f) => {
        const cur = state.source.fields[f];
        const simple = typeof cur === "string" || cur === undefined;
        const ex = el("td", {}, simple ? exampleCell(cur, f) : "");
        const role = FIELD_ROLE[f] || ["Golden record", ""];
        const sel = simple ? pick(cur || "", (v) => { if (v) state.source.fields[f] = v; else delete state.source.fields[f]; ex.replaceChildren(exampleCell(v, f)); }, fieldTitle(f) + ": your column")
          : el("span", { class: "muted" }, "Several columns combined (kept as is)");
        const sug = suggested[f] && suggested[f].column === cur ? suggested[f] : null;
        return el("tr", {}, el("td", {}, el("strong", {}, fieldTitle(f)), el("div", { class: "muted small" }, opts.field_help[f] || "")),
          el("td", {}, sel, sug ? el("div", {}, tag(SUGGESTED_BY[sug.why] || "Suggested", "info")) : null), ex,
          el("td", {}, tag(role[0], ROLE_KIND[role[0]]), role[1] ? el("div", { class: "muted small" }, role[1]) : null));
      });
      return el("div", {}, el("h3", { class: "section" }, g.title), table(["Our field", "Your column", "Examples from your data", "Used for"], rows));
    });
    const mappedCols = new Set(Object.values(state.source.fields).filter((c) => typeof c === "string"));
    const attrs = el("div", { class: "checks" }, cols.filter((c) => !mappedCols.has(c)).map((c) => {
      const cb = el("input", { type: "checkbox", checked: (state.source.attributes || []).includes(c) });
      cb.addEventListener("change", () => { const set = new Set(state.source.attributes || []); if (cb.checked) set.add(c); else set.delete(c); state.source.attributes = [...set]; });
      return el("label", { class: "check" }, cb, el("span", {}, el("strong", {}, c), el("span", { class: "muted" }, examples(c).join(", "))));
    }));
    // every column of the source: what its values look like and what it is used as now
    function columnTypes() {
      if (!Object.keys(types).length) return null;
      const usedAs = (c) => {
        const fs = Object.entries(state.source.fields).filter(([, col]) => col === c).map(([f]) => fieldTitle(f));
        if (fs.length) return fs.join(", ");
        if (c === state.source.record_id) return "Each row's own id";
        if ((state.source.attributes || []).includes(c)) return "Kept as an extra column";
        const f = (types[c] || {}).field;
        return f && !state.source.fields[f] ? el("span", {}, "Not used. ", tag("Could fill: " + fieldTitle(f), "info")) : "Not used";
      };
      const rows = cols.map((c) => {
        const t = types[c] || {};
        return el("tr", {}, el("td", {}, el("strong", {}, c)), el("td", {}, t.type ? t.description : el("span", { class: "muted" }, "Not recognised")),
          el("td", { class: "num" }, Math.round((t.filled || 0) * 100) + "%"), el("td", { class: "num" }, Math.round((t.distinct || 0) * 100) + "%"), el("td", {}, usedAs(c)));
      });
      return el("details", { class: "advanced", open: true }, el("summary", {}, "What we found in your columns (from the values, not the names)"),
        el("p", { class: "hint" }, "Each column is checked value by value: PAN and GST structure, Aadhaar checksum, Indian mobile rules, dates, names, places. " +
          "\"Different values\" near 100% means almost every row has its own value (an id); a low share means a short list of options."),
        table(["Your column", "Values look like", ["Rows filled"], ["Different values"], "Used as"], rows));
    }
    // which identifiers decide on their own: the project's matching rules, proposed together with the connection
    const present = can("config.propose") ? Object.keys(KEY_RULES).filter((k) => KEY_FIELDS[k].some((f) => state.source.fields[f])) : [];
    const keyBoxes = present.map((k) => {
      const cb = el("input", { type: "checkbox", checked: state.keys[k] });
      cb.addEventListener("change", () => { state.keys[k] = cb.checked; });
      return el("label", { class: "check" }, cb, el("span", {}, el("strong", {}, "Records with the same " + KEY_RULES[k][0] + " are the same person"),
        el("span", { class: "muted" }, "Merged automatically when the names are compatible; two different " + KEY_RULES[k][0] + "s are never merged. Applies to the whole project.")));
    });
    return el("div", {},
      el("div", { class: "title-row" }, el("h3", { class: "section" }, "Your data (" + (state.info.unmasked ? "actual values" : "values shortened") + ")"),
        revealButton(async () => { state.info = { ...state.info, ...(await api("POST", inspectUrl(), { connector: state.source.connector })) }; render(); })),
      wide(cols, sample.map((r) => el("tr", {}, cols.map((c) => el("td", {}, r[c] == null ? "" : r[c]))))),
      columnTypes(),
      el("p", { class: "hint" }, "For each of our fields, pick the column of your data that holds it. Leave a field as 'Not in this source' when your data does not have it: " +
        "every field is optional, but the more of them a source fills, the more duplicates it finds."),
      groups,
      el("h3", { class: "section" }, "Which columns identify the same person?"),
      el("p", { class: "hint" }, "Names, birth dates, phones, emails and addresses are always compared together and weighed by how well they tell people apart in your data. " +
        "An identifier can also decide on its own:"),
      keyBoxes.length ? el("div", { class: "checks" }, keyBoxes) : el("p", { class: "muted" }, can("config.propose") ? "Map a PAN, Aadhaar or customer number column above to choose it here."
        : "Someone who manages the project's rules chooses this in the Rules tab."),
      el("div", { class: "cols" },
        el("div", {}, el("h3", { class: "section" }, "Each row's own id"), el("p", { class: "hint" }, "The column that identifies a row in this source, used to trace a golden record back to its rows. It is not used to match. Without one, rows are numbered."),
          pick(state.source.record_id || "", (v) => { state.source.record_id = v || null; }, "Each row's own id")),
        el("div", {}, el("h3", { class: "section" }, "Other columns to keep"), el("p", { class: "hint" }, "Ticked columns are carried into the golden record as extra columns."), attrs)),
      el("div", { class: "row" }, btn("Back", () => goStep(1), "", "prev"), btn("Next: preview", () => goStep(3), "primary", "next")));
  }
  function previewStep() {
    const doPreview = () => run(async () => {
      const r = await api("POST", root + "/connections/preview" + (state.reason ? "?" + unmaskQuery(state.reason) : ""), state.source);
      const cols = Object.keys(r.rows[0] || {}).filter((c) => c !== "attributes" && r.rows.some((row) => row[c] != null));
      fill(out, el("h3", { class: "section" }, "Fields filled in " + r.sample_size + " sample rows"),
        table(["Field", ["Rows filled"]], Object.entries(r.filled).map(([f, n]) => el("tr", {}, el("td", {}, label(f)), el("td", { class: "num" }, n + " of " + r.sample_size)))),
        r.rows.length ? el("div", { class: "title-row" }, el("h3", { class: "section" }, "Your rows as the engine reads them (" + (r.unmasked ? "actual values" : "values shortened") + ")"),
          revealButton(async () => doPreview())) : null,
        r.rows.length ? wide(cols, r.rows.map((row) => el("tr", {}, cols.map((c) => el("td", {}, row[c] == null ? "" : row[c]))))) : null);
    });
    const keysChanged = () => state.decision && Object.keys(state.keys || {}).some((k) => state.keys[k] !== keyOn(state.decision, k));
    const save = async () => {
      const why = await askReason("Save connection " + state.source.source_id, "Why add or change it?", 10, "Save connection");
      if (why === null) return;
      const res = await api("POST", root + "/connections", { connection: state.source, reason: why });
      let rules = null;
      if (keysChanged()) rules = await api("POST", root + "/config/decision/propose", { content: withKeys(state.decision, state.keys), reason: why + " (identifiers chosen while mapping " + state.source.source_id + ")" });
      await go(() => viewConnection(pid, res.id));
      say("Saved as version " + res.version + ". " + (res.second_approval ? "Another person approves it and makes it live; then runs can use it." : "Make it live when you are ready; then runs can use it.") +
        (rules ? " Your choice of identifiers was proposed as version " + rules.version + " of the project's matching rules (Rules tab): make that live too." : ""), "good");
    };
    setTimeout(doPreview, 0);
    return el("div", {}, keysChanged() ? el("div", { class: "notice", role: "note" }, "Saving also proposes a change to the project's matching rules: " +
      Object.keys(state.keys).filter((k) => state.keys[k] !== keyOn(state.decision, k)).map((k) => (state.keys[k] ? "the same " : "no longer the same ") + KEY_RULES[k][0] + " decides").join("; ") + ".") : null,
    el("div", { class: "row" }, btn("Back", () => goStep(2), "", "prev"), btn("Preview again", doPreview, "", "refresh"), btn("Save connection", save, "primary", "check")));
  }
  render();
  if (preset && startStep && startStep > 1) await goStep(startStep);
}

// ---- results: take a run's output out as a file or into a database ------------------------------------------------------------
async function resultsPanel(p, runs) {
  const done = runs.filter((r) => r.status === "succeeded");
  const refresh = () => go(() => viewProject(p.id, "results"));
  if (!done.length) {
    return el("div", {}, empty("Results appear here after a run has finished. Start a run first.", can("runs.start") ? btn("New run", () => go(() => newRun(p.id)), "primary", "play") : null),
      await destinationsPanel(p, refresh));
  }
  const pick = el("select", {}, done.map((r) => el("option", { value: r.id }, r.id + ", " + when(r.finished_at || r.created_at) + " (" + num(r.summary.people) + " people)")));
  const box = el("div");
  const load = () => run(async () => box.replaceChildren(...(await datasetCards(p, pick.value))));
  pick.addEventListener("change", load);
  const wrap = el("div", {}, el("p", { class: "hint" }, "Results contain personal data. Every download or database write asks for a reason and is recorded in the project's activity."),
    el("div", { class: "row" }, field("Results of run (newest first)", pick)), box, await destinationsPanel(p, refresh));
  await load();
  return wrap;
}
async function datasetCards(p, rid) {
  const info = await api("GET", R(p.id, rid) + "/datasets");
  return info.datasets.map((d) => {
    const box = el("div");
    const open = () => run(async () => box.replaceChildren(await previewTable(p.id, rid, d)));
    const card = el("div", { class: "panel" }, el("h3", {}, d.title + " (" + num(d.rows) + " rows)"),
      el("div", { class: "body" }, el("p", {}, d.description),
        el("div", { class: "row" }, ...["xlsx", "csv", "parquet"].map((f) => btn(info.formats[f], () => downloadResults(p.id, rid, d.name, f), "small", "download")),
          btn("Write to a database", () => databaseDialog(p, rid, d), "small", "database"),
          d.name === "golden_records" ? null : btn("Show a preview", open, "small", "list")), box));
    if (d.name === "golden_records") open();
    return card;
  });
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
  if (!dests.length) { say("This project has no live database destination yet. Add one under Database destinations on this page, then make it live.", "bad"); return; }
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
      can("config.activate") && (!second || v.approved_by.length) ? btn("Make live", () => act(d, v, "activate", "Make version " + v.version + " live", 0, "Note"), "small primary") : null,
      second && mine && v.status === "proposed" ? tag("Yours: someone else approves it", "warn") : null,
    ].filter(Boolean) : [];
    return el("tr", {}, el("td", {}, d.id), el("td", { class: "mono" }, d.url_env), el("td", {}, d.schema_name || ""), el("td", {}, d.live_version ? "Version " + d.live_version : el("span", { class: "muted" }, "Not live")),
      el("td", {}, d.latest_status === "active" ? "" : statusTag(d.latest_status)), el("td", { class: "actions-cell" }, buttons,
        can("sources.manage") ? btn("Test", async () => {
          const t = await api("POST", P(p.id) + "/destinations/" + d.id + "/test");
          say(t.ok ? "The database " + d.id + " can be reached." : "The database " + d.id + " could not be reached: " + t.error, t.ok ? "good" : "bad");
        }, "small") : null));
  });
  return el("div", {}, el("div", { class: "title-row" }, el("h2", { class: "section" }, "Database destinations"),
    can("sources.manage") ? btn("Add a destination", () => destinationDialog(p, refresh), "", "plus") : null),
  el("p", { class: "hint" }, "A destination is the name of an environment variable (starting with DEDUPE_DST_) that holds a database address. The address and password are set on the server by whoever runs it, never typed here."),
  rows.length ? table(["Destination", "Environment variable", "Schema", "Live", "Pending", ""], rows) : empty("No destinations yet. Add one to write results into a database."));
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
  if (res) { await refresh(); say("Saved as version " + res.version + ". " + (res.second_approval ? "Another person approves it, then it can be made live." : "Make it live to use it."), "good"); }
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
const LOAD_MODES = [["incremental", "Incremental", "refresh", "Reads every record, but reuses the latest successful run's work for records that did not change. Much faster; people are still formed from all records."],
  ["full", "Full reload", "layers", "Reads and processes every record from scratch, and learns the scoring again. Slower; use it after big changes to the data or the rules."]];
const loadTag = (mode) => (mode === "full" ? tag("Full reload", "info") : tag("Incremental", "success"));
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
    el("p", { class: "hint" }, "A schedule starts an ordinary run of the chosen connections at the times you set, using each connection's live version at that moment: an incremental load (only new and changed records are processed again) or a full reload. It never starts a new run while its last one is still going, runs once (not once per missed slot) after the server was off, and pauses itself after three failed runs in a row."),
    rows.length ? table(["Name", "Connections", "When", "Load", "Next run", "Last run", "State", ""], rows) : empty("No schedules yet." + (manage ? "" : " Someone who can start runs creates them."), create));
}
async function scheduleDialog(p, connections, existing) {
  const live = connections.filter((c) => c.live_version);
  if (!live.length) { say("This project has no live connection to run yet.", "bad"); return; }
  const s = existing || {};
  const name = el("input", { type: "text", maxlength: "100", placeholder: "for example Nightly sweep", value: s.name || "" });
  const picks = checkList(live.map((c) => ({ name: c.id, description: label(c.type) + ", version " + c.live_version })), new Set(s.connections || live.map((c) => c.id)), () => true, false);
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
  const saved = await dialog(existing ? "Edit schedule " + s.name : "New schedule", el("div", {}, field("Name", name), el("h3", { class: "section" }, "Connections to read"), picks.node,
    el("h3", { class: "section" }, "How to load"), mode.node,
    field("How often", freq), dayField, atField, field("Time zone (a name such as Asia/Kolkata)", zone), zones, err), "Save schedule", async () => {
    try {
      const values = Object.fromEntries(Object.entries(body).map(([k, f]) => [k, f()]));
      return existing ? await api("PATCH", P(p.id) + "/schedules/" + s.id, values) : await api("POST", P(p.id) + "/schedules", values);
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (saved) { await go(() => viewProject(p.id, "schedules")); say("Schedule saved. Next run: " + (saved.next_run_at ? when(saved.next_run_at) : "none while paused") + ".", "good"); }
}

// ---- review: the human in the loop ------------------------------------------------------------------------------------------
// Work is found in the Review inbox (every project's latest run), worked pair by pair on the compare page (decide, and the next pair opens),
// and every decision can be seen and undone under Decisions. Decisions belong to the project and change people at the project's next run.
const BANDS = [["all", "Any score"], ["high", "90% and above"], ["mid", "50% to 90%"], ["low", "Below 50%"]];
const bandQuery = (b) => ({ all: "", high: "&min_probability=0.9", mid: "&min_probability=0.5&max_probability=0.9", low: "&max_probability=0.5" })[b] || "";
const QUEUE_PAGE = 25;
let reviewCtx = null;              // where the compare page sits in its queue: {pid, rid, status, band, index, l, r}
const queueUrl = (ctx, limit, offset) => R(ctx.pid, ctx.rid) + "/queue?limit=" + limit + "&offset=" + offset + "&status=" + ctx.status + bandQuery(ctx.band);

async function openQueueItem(ctx, index, doneText) {
  const items = index >= 0 ? await api("GET", queueUrl(ctx, 1, index)) : [];
  if (!items.length) {
    await go(() => viewRun(ctx.pid, ctx.rid, "review"));
    say(doneText || "Nothing else waits in this queue.", "good");
    return;
  }
  const it = items[0];
  reviewCtx = { ...ctx, index, l: it.person_l, r: it.person_r };
  await go(() => viewCompare(ctx.pid, ctx.rid, it.person_l, it.person_r));
}

async function viewInbox() {
  here("/review");
  const [rows] = await Promise.all([api("GET", "/review/inbox"), refreshCache()]);
  const total = (k) => rows.reduce((n, x) => n + (x[k] || 0), 0);
  const start = (x, status) => openQueueItem({ pid: x.project_id, rid: x.run_id, status, band: "all" }, 0);
  const lines = rows.map((x) => el("tr", {},
    el("td", {}, el("button", { type: "button", class: "link", onclick: () => go(() => viewProject(x.project_id)) }, x.project_name)),
    el("td", {}, x.run_id ? el("button", { type: "button", class: "link mono", onclick: () => go(() => viewRun(x.project_id, x.run_id)) }, x.run_id) : el("span", { class: "muted" }, "no finished run")),
    el("td", { class: "num" }, x.open == null ? (x.run_id ? "many" : "") : num(x.open)), el("td", { class: "num" }, num(x.unsure)),
    el("td", { class: "num" }, num(x.pending_approval)), el("td", { class: "num" }, num(x.waiting_for_run)),
    el("td", {}, x.scoring_mode && x.scoring_mode !== "id_anchored" ? tag(x.scoring_mode === "unsupervised_em" ? "Scores learned from the data" : "Scores not meaningful", x.scoring_mode === "unsupervised_em" ? "info" : "danger") : ""),
    el("td", { class: "actions-cell" },
      x.run_id && x.open ? btn("Start reviewing", () => start(x, "open"), "small primary", "next") : null,
      x.run_id && x.unsure ? btn("Revisit not sure", () => start(x, "unsure"), "small") : null,
      x.waiting_for_run && can("runs.start") && x.run_id ? btn("Run again", () => startRun(x.project_id, [], x.run_id, "apply the latest review decisions"), "small", "play") : null)));
  show(page("Review inbox", "Pairs that need a person, in each project's newest finished run. Older runs' queues are out of date and are not counted here."),
    el("div", { class: "stats" }, stat("Pairs to review", num(total("open"))), stat("Marked not sure", num(total("unsure"))),
      stat("Waiting for a second person", num(total("pending_approval")), link("/approvals")), stat("Decisions waiting for the next run", num(total("waiting_for_run")), link("/review/decisions"))),
    rows.length ? table(["Project", "Newest run", ["To review"], ["Not sure"], ["Second person"], ["Next run"], "Scores", ""], lines)
      : empty("You are not in any project yet."),
    el("p", { class: "hint" }, "How it works: open a pair, decide Same person, Different people or Not sure, and the next pair opens. Keys on the compare page: ",
      el("kbd", {}, "S"), " same, ", el("kbd", {}, "D"), " different, ", el("kbd", {}, "U"), " not sure, ", el("kbd", {}, "N"), " next, ", el("kbd", {}, "P"), " previous. ",
      "Decisions change the people and golden records at the project's next run."));
}

async function reviewQueue(pid, rid, ctx) {
  ctx = { pid, rid, ...ctx };
  const [summary, items] = await Promise.all([api("GET", R(pid, rid) + "/queue/summary"), api("GET", queueUrl(ctx, QUEUE_PAGE, ctx.offset))]);
  const box = el("div");
  const reload = async (patch) => box.replaceWith(await reviewQueue(pid, rid, { ...ctx, ...patch }));
  const status = el("select", {}, [["open", "Open"], ["unsure", "Marked not sure"], ["all", "All, including decided"]].map(([v, t]) => el("option", { value: v, selected: v === ctx.status }, t)));
  const band = el("select", {}, BANDS.map(([v, t]) => el("option", { value: v, selected: v === ctx.band }, t)));
  status.addEventListener("change", () => run(() => reload({ status: status.value, offset: 0 })));
  band.addEventListener("change", () => run(() => reload({ band: band.value, offset: 0 })));
  const writer = can("judgment.write");
  const chosen = new Set();
  const open = (i, it) => go(() => { reviewCtx = { ...ctx, index: ctx.offset + i, l: it.person_l, r: it.person_r }; return viewCompare(pid, rid, it.person_l, it.person_r); });
  const rows = items.map((it, i) => {
    const cb = writer && it.state !== "decided" ? el("input", { type: "checkbox", "aria-label": "Select " + it.person_l + " and " + it.person_r }) : null;
    if (cb) cb.addEventListener("change", () => { if (cb.checked) chosen.add(it); else chosen.delete(it); });
    return el("tr", { class: "click", onclick: (e) => { if (!e.target.closest("button, input, label")) open(i, it); } },
      writer ? el("td", { class: "check" }, cb) : null,
      el("td", {}, el("button", { type: "button", class: "link mono", onclick: () => open(i, it) }, it.person_l + " and " + it.person_r)),
      el("td", { class: "num" }, it.size_l + " and " + it.size_r), el("td", { class: "num" }, pct(it.top_probability)),
      el("td", {}, it.forced ? tag("Flagged by a rule", "info") : ""), el("td", {}, statusTag(it.state)));
  });
  const bulk = async (decision) => {
    if (!chosen.size) { say("Tick the pairs first.", "bad"); return; }
    const what = decision === "approve" ? "Same person" : "Different people";
    const why = await askReason(what + " for " + plural(chosen.size, "pair", "pairs"), "Why? It is recorded with every one of these decisions", decision === "approve" ? 10 : 0, "Decide " + plural(chosen.size, "pair", "pairs"));
    if (why === null) return;
    const out = await api("POST", R(pid, rid) + "/decisions/bulk", { decision, reason: why, items: [...chosen].map((it) => ({ person_l: it.person_l, person_r: it.person_r })) }, { "Idempotency-Key": uuid() });
    await reload({});
    say(plural(out.done.length, "pair", "pairs") + " decided." + (out.failed.length ? " Not done: " + out.failed.map((f) => f.pair + " (" + f.error + ")").join("; ") : ""), out.failed.length ? "bad" : "good");
  };
  const first = items.length && ctx.status !== "all" ? btn(ctx.status === "unsure" ? "Revisit the first" : "Start reviewing", () => openQueueItem(ctx, ctx.offset), "primary", "next") : null;
  const pager = el("div", { class: "pager" }, "Showing " + (items.length ? (ctx.offset + 1) + " to " + (ctx.offset + items.length) : "none"),
    btn("Previous", () => reload({ offset: Math.max(0, ctx.offset - QUEUE_PAGE) }), "small", "prev", { disabled: ctx.offset === 0 }),
    btn("Next", () => reload({ offset: ctx.offset + QUEUE_PAGE }), "small", "next", { disabled: items.length < QUEUE_PAGE }));
  add(box,
    el("div", { class: "row" }, field("Show", status), field("Match score", band), first),
    el("p", { class: "hint" }, (summary.open_now == null ? "Many" : num(summary.open_now)) + " pairs are open in this run. " + plural(summary.pending_approval, "decision waits", "decisions wait") +
      " for a second person. Decisions take effect at the project's next run."),
    items.length ? table([...(writer ? [""] : []), "People", ["Records"], ["Match score"], "Why it is here", "State"], rows)
      : empty(ctx.status === "open" ? "Nothing to review here." + (ctx.band !== "all" ? " Try another score band." : "") : "No pairs in this view."),
    writer && items.length ? el("div", { class: "row" }, el("span", { class: "muted" }, "With the ticked pairs:"),
      btn("Different people", () => bulk("reject"), "small"), btn("Same person", () => bulk("approve"), "small")) : null,
    items.length ? pager : null);
  return box;
}

// ---- comparing two people ----------------------------------------------------------------------------------------------------
const FIELD_ORDER = ["full_name", "father_name", "dob", "gender", "marital_status", "mobile", "email", "address", "city", "state", "pincode", "pan", "aadhaar_last4", "passport_no",
  "dl_no", "voter_id", "occupation", "annual_income"];
function fieldDiff(a, b) {
  const keys = [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])].sort((x, y) => (FIELD_ORDER.indexOf(x) + 99) % 199 - (FIELD_ORDER.indexOf(y) + 99) % 199);
  const norm = (v) => String(v).trim().toLowerCase().replace(/[\s.,-]+/g, " ");
  return el("div", { class: "scroll" }, el("table", { class: "diff" }, head(["Field", "Person A", "Person B", "Compared"]), el("tbody", {}, keys.map((k) => {
    const x = a[k]; const y = b[k];
    const one = x == null || x === "" || y == null || y === "";
    const same = !one && norm(x) === norm(y);
    const masked = String(x).includes("*") || String(y).includes("*");
    const verdict = one ? ["Only on one side", "muted"] : same ? [masked ? "Looks the same (masked)" : "Same", "same"] : ["Differs", "differs"];
    return el("tr", {}, el("th", { scope: "row" }, label(k)), el("td", {}, x == null ? "" : String(x)), el("td", {}, y == null ? "" : String(y)), el("td", { class: "flag " + verdict[1] }, verdict[0]));
  }))));
}
function recordsTable(p) {
  return table(["Source", "Record", "Name as written", "Date of birth", "Mobile", "Email", "City"], p.records.map((r) => el("tr", {}, el("td", {}, r.source_id),
    el("td", { class: "mono" }, r.source_record_id), el("td", {}, r.name_as_written || ""), el("td", {}, r.dob || ""), el("td", {}, r.mobile || ""), el("td", {}, r.email || ""), el("td", {}, r.city || ""))));
}
async function viewCompare(pid, rid, l, r, reveal) {
  here(R(pid, rid) + "/compare/" + l + "/" + r);
  const ctx = reviewCtx && reviewCtx.pid === pid && reviewCtx.rid === rid && reviewCtx.l === l && reviewCtx.r === r ? reviewCtx : { pid, rid, l, r, status: "open", band: "all", index: null };
  reviewCtx = ctx;
  const base = R(pid, rid);
  let path = base + "/people/" + l + "/compare/" + r;
  if (reveal) path += "?" + unmaskQuery(reveal);
  const [c, runInfo, summary] = await Promise.all([api("GET", path), api("GET", R(pid, rid)), can("queue.read") ? api("GET", base + "/queue/summary") : null, refreshCache()]);
  const writer = can("judgment.write");
  const note = el("textarea", { class: "plain short", maxlength: "300", placeholder: "Optional, kept with the decision." });
  const conflict = c.hard_conflicts.length ? el("input", { type: "checkbox" }) : null;
  const keys = {};
  const decide = async (kind) => {
    if (kind === "approve" && conflict && !conflict.checked) { say("These two people have conflicting identifiers. Tick the confirmation box first, or decide Different people.", "bad"); conflict.focus(); return; }
    if (kind === "approve" && conflict && note.value.trim().length < 10) { say("Merging people with conflicting identifiers needs a reason of at least 10 characters.", "bad"); note.focus(); return; }
    keys[kind] = keys[kind] || uuid();
    const body = { person_l: l, person_r: r, reason: note.value.trim() };
    if (kind === "approve" && conflict) body.confirm_id_conflict = true;
    const out = await api("POST", base + "/decisions/" + kind, body, { "Idempotency-Key": keys[kind] });
    const words = { approve: "Same person", reject: "Different people", unsure: "Not sure" }[kind];
    const msg = words + " recorded for " + l + " and " + r + (out.pending_approval ? ". It waits for a second person." : ". It applies at the project's next run.");
    const undo = { label: "Undo", icon: "undo", fn: async () => {
      await api("POST", P(pid) + "/decisions/" + out.action_id + "/undo", { reason: "undone right after deciding" });
      reviewCtx = null;
      await go(() => viewCompare(pid, rid, l, r));
      say("The decision on " + l + " and " + r + " was undone.", "good");
    } };
    const stays = ctx.status === "all" || (ctx.status === "unsure" && kind === "unsure");
    await openQueueItem(ctx, ctx.index == null ? 0 : ctx.index + (stays ? 1 : 0), msg + " Nothing else waits in this queue.");
    if (reviewCtx && reviewCtx.l !== l) say(msg, "good", undo);
    else say(msg + " Nothing else waits in this queue.", "good", undo);
  };
  const B = {
    same: writer ? btn("Same person", () => decide("approve"), "primary", "check", { "aria-keyshortcuts": "S" }) : null,
    diff: writer ? btn("Different people", () => decide("reject"), "", null, { "aria-keyshortcuts": "D" }) : null,
    unsure: writer ? btn("Not sure", () => decide("unsure"), "", null, { "aria-keyshortcuts": "U" }) : null,
    prev: ctx.index ? btn("Previous", () => openQueueItem(ctx, ctx.index - 1), "", "prev", { "aria-keyshortcuts": "P" }) : null,
    next: btn("Skip", () => openQueueItem(ctx, ctx.index == null ? 0 : ctx.index + 1), "", "next", { "aria-keyshortcuts": "N" }),
  };
  pageKeys = (e) => {
    const k = { s: B.same, d: B.diff, u: B.unsure, n: B.next, j: B.next, p: B.prev, k: B.prev }[e.key.toLowerCase()];
    if (k && !k.disabled) { e.preventDefault(); k.click(); }
  };
  const actions = [B.prev, B.next];
  if (can("pii.unmask") && !c.unmasked) {
    actions.unshift(btn("Show identifiers", async () => {
      const why = await revealIdentifiers("Show identifiers");
      if (why) await go(() => viewCompare(pid, rid, l, r, why));
    }));
  }
  const position = ctx.index == null ? "" : "Pair " + num(ctx.index + 1) + (ctx.status === "open" && ctx.band === "all" && summary && summary.open_now != null ? " of " + num(summary.open_now) : "") + ". ";
  const [A, Bp] = c.persons;
  const why = (list) => (list.length ? el("ul", { class: "plain" }, list.map((x) => el("li", {}, label(x.comparison) + ": " + x.level + " (" + x.bits.toFixed(1) + " bits)")))
    : el("p", { class: "muted" }, "None."));
  const stale = runInfo.latest_run_id && runInfo.latest_run_id !== rid ? el("div", { class: "notice warn", role: "note" },
    "This pair comes from an older run. Your decision still counts, but the newest run may already show these people differently.",
    el("div", { class: "row" }, btn("Open the newest run's queue", () => go(() => viewRun(pid, runInfo.latest_run_id, "review")), "small"))) : null;
  show(page("Compare people", position + "Match score " + pct(c.probability) + (c.decided_by ? ". Decided by rule: " + c.decided_by : "") + ".", actions,
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Run " + rid, R(pid, rid)), crumb("Review queue", R(pid, rid) + "/review"), l + " and " + r)),
  stale, scoringNotice(runInfo.summary),
  c.unmasked ? el("p", { class: "hint" }, "Identifiers are shown in full. This view is logged.") : el("p", { class: "hint" }, "Identifiers are masked. Masked values that look the same may still differ."),
  c.hard_conflicts.length ? el("div", { class: "notice bad", role: "note" }, el("strong", {}, "Conflicting identifiers: "), c.hard_conflicts.map(label).join(", ") + ". These are probably two different people.") : null,
  writer ? el("div", { class: "panel" }, el("h3", {}, "Your decision"), el("div", { class: "body" },
    conflict ? el("label", { class: "check" }, conflict, el("span", {}, "I confirm they are one person despite the conflicting identifiers (a reason is required)")) : null,
    field("Note", note),
    el("div", { class: "row" }, B.same, B.diff, B.unsure),
    el("p", { class: "hint" }, "Keys: ", el("kbd", {}, "S"), " same person, ", el("kbd", {}, "D"), " different people, ", el("kbd", {}, "U"), " not sure, ", el("kbd", {}, "N"), " skip, ",
      el("kbd", {}, "P"), " previous. After a decision the next pair opens; Undo is offered in the message."))) : el("p", { class: "hint" }, "Your role can look but not decide."),
  el("h2", { class: "section" }, A.person_id + " (person A, " + plural(A.record_count, "record", "records") + ") and " + Bp.person_id + " (person B, " + plural(Bp.record_count, "record", "records") + ")"),
  fieldDiff(A.golden, Bp.golden),
  el("h2", { class: "section" }, "Evidence from the best-matching pair of records"),
  el("div", { class: "cols" }, el("div", { class: "panel" }, el("h3", {}, "For the same person"), el("div", { class: "body" }, why(c.why_match))),
    el("div", { class: "panel" }, el("h3", {}, "For different people"), el("div", { class: "body" }, why(c.why_not)))),
  c.existing_judgments.length ? el("h2", { class: "section" }, "Earlier decisions on these records") : null,
  c.existing_judgments.length ? table(["Decision", "By", "Status", "Reason"], c.existing_judgments.map((j) => el("tr", {}, el("td", {}, KIND_LABEL[j.kind] || label(j.kind)), el("td", {}, j.actor),
    el("td", {}, statusTag(j.status === "active" ? "in_force" : j.status === "revoked" ? "undone" : j.status)), el("td", {}, j.reason)))) : null,
  el("h2", { class: "section" }, "Source records"),
  el("div", { class: "cols" }, [A, Bp].map((x, i) => el("div", {}, el("div", { class: "title-row" }, el("h3", { class: "section" }, (i ? "Person B: " : "Person A: ") + x.person_id),
    can("person.read") ? btn("Open person", () => go(() => viewPerson(pid, rid, x.person_id)), "small") : null), recordsTable(x)))));
}

// ---- one person: their records, fixing a wrong merge (split) and correcting a golden value (override) ----------------------------------
function peopleLookup(pid, rid) {
  const id = el("input", { type: "text", placeholder: "for example P000000123", maxlength: "10" });
  const openIt = () => go(() => viewPerson(pid, rid, id.value.trim().toUpperCase()));
  id.addEventListener("keydown", (e) => { if (e.key === "Enter") openIt(); });
  return el("div", {}, el("p", { class: "hint" }, "Open a person by their ID to see their records, split a wrong merge, or correct a value. Person IDs appear in the review queue and in the results files."),
    el("div", { class: "row" }, field("Person ID", id), btn("Open", openIt, "primary")));
}
async function viewPerson(pid, rid, person, reveal) {
  here(R(pid, rid) + "/people/" + person);
  const base = R(pid, rid);
  const v = await api("GET", base + "/people/" + encodeURIComponent(person) + (reveal ? "?" + unmaskQuery(reveal) : ""));
  await refreshCache();
  const writer = can("judgment.write");
  const correct = async (fieldName, current) => {
    const value = el("input", { type: "text", maxlength: "200", value: current == null || String(current).includes("*") ? "" : String(current) });
    const why = el("textarea", { class: "plain", maxlength: "300" });
    const err = errBox();
    const out = await dialog("Correct " + label(fieldName), el("div", {}, el("p", { class: "hint" }, "The golden record of " + person + " will show this value from the project's next run on, whatever the sources say. Leave it empty to clear the field."),
      field("New value", value), field("Why? (at least 10 characters; it is recorded)", why), err), "Save correction", async () => {
      try {
        return await api("POST", base + "/people/" + person + "/override", { field: fieldName, value: value.value.trim() || null, reason: why.value.trim() }, { "Idempotency-Key": uuid() });
      } catch (e) { fail(err, e.message); return undefined; }
    });
    if (out) say("Correction recorded" + (out.pending_approval ? ". It waits for a second person." : ". It applies at the project's next run."), "good");
  };
  const picks = [];
  const split = async () => {
    const chosen = picks.filter(([cb]) => cb.checked).map(([, r]) => r);
    if (!chosen.length || chosen.length === v.records.length) { say("Tick the records that belong to someone else (some, not all).", "bad"); return; }
    const rest = v.records.filter((r) => !chosen.includes(r));
    const why = await askReason("Split " + person, "Why are these " + plural(chosen.length, "record", "records") + " someone else? It is recorded", 10, "Split", true);
    if (why === null) return;
    const key = (r) => ({ source_id: r.source_id, source_record_id: r.source_record_id });
    const out = await api("POST", base + "/people/" + person + "/split", { parts: [chosen.map(key), rest.map(key)], reason: why }, { "Idempotency-Key": uuid() });
    say("Split recorded" + (out.pending_approval ? ". It waits for a second person." : ". The records become separate people at the project's next run."), "good");
  };
  const golden = Object.entries(v.golden || {}).map(([k, val]) => [label(k), el("span", { class: "title-row" }, String(val),
    writer ? btn("Correct", () => correct(k, val), "small ghost", "edit", { "aria-label": "Correct " + label(k) }) : null)]);
  const rows = v.records.map((r) => {
    const cb = writer && v.records.length > 1 ? el("input", { type: "checkbox", "aria-label": "Select record " + r.source_id + " " + r.source_record_id }) : null;
    if (cb) picks.push([cb, r]);
    return el("tr", {}, writer && v.records.length > 1 ? el("td", { class: "check" }, cb) : null, el("td", {}, r.source_id), el("td", { class: "mono" }, r.source_record_id),
      el("td", {}, r.name_as_written || ""), el("td", {}, r.dob || ""), el("td", {}, r.mobile || ""), el("td", {}, r.email || ""), el("td", {}, r.city || ""));
  });
  const actions = can("pii.unmask") && !reveal ? [btn("Show identifiers", async () => {
    const why = await revealIdentifiers("Show identifiers");
    if (why) await go(() => viewPerson(pid, rid, person, why));
  })] : [];
  show(page("Person " + person, plural(v.record_count, "record", "records") + " from " + (v.sources || []).join(", ") + ".", actions,
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Run " + rid, R(pid, rid)), crumb("People", R(pid, rid) + "/people"), person)),
  v.conflict_fields && v.conflict_fields.length ? el("div", { class: "notice warn", role: "note" }, "The sources disagree on: " + v.conflict_fields.map(label).join(", ") + ". The golden record shows the best-supported value.") : null,
  el("h2", { class: "section" }, "Golden record"), golden.length ? kv(golden) : empty("No values."),
  Object.keys(v.extra || {}).length ? [el("h3", { class: "section" }, "Other columns from the sources"), kv(Object.entries(v.extra).map(([k, val]) => [label(k), String(val)]))] : null,
  el("h2", { class: "section" }, "Source records"),
  table([...(writer && v.records.length > 1 ? [""] : []), "Source", "Record", "Name as written", "Date of birth", "Mobile", "Email", "City"], rows),
  writer && v.records.length > 1 ? el("div", { class: "row" }, btn("Split the ticked records into a new person", split, "", "split"),
    el("span", { class: "muted" }, "Use this when records of two different people were merged.")) : null);
}

// ---- decisions: what was decided, what became of it, and undo -------------------------------------------------------------------
async function viewDecisions() {
  here("/review/decisions");
  await refreshCache();
  show(page("Decisions", "Every review decision in your projects, newest first, with what became of it. A decision changes people at the project's next run."),
    await decisionsPanel(cache.projects, { project: false }));
}
async function decisionsPanel(projects, opts, state) {
  state = state || { mine: !opts.project && can("judgment.write"), status: "" };
  const mine = el("input", { type: "checkbox", checked: state.mine });
  const status = el("select", {}, [["", "Any status"], ["waiting_for_run", "Waits for the next run"], ["pending_approval", "Waits for a second person"], ["in_force", "In force"],
    ["declined", "Declined"], ["undone", "Undone"]].map(([v, t]) => el("option", { value: v, selected: v === state.status }, t)));
  const box = el("div");
  const reload = async () => box.replaceWith(await decisionsPanel(projects, opts, { mine: mine.checked, status: status.value }));
  mine.addEventListener("change", () => run(reload));
  status.addEventListener("change", () => run(reload));
  const q = "?limit=200" + (state.mine ? "&mine=true" : "") + (state.status ? "&status=" + state.status : "");
  const lists = await Promise.all(projects.map((p) => api("GET", P(p.id) + "/decisions" + q).then((rows) => rows.map((d) => ({ ...d, project: p }))).catch(() => [])));
  const rows = lists.flat().sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  const undo = async (d) => {
    const why = await askReason("Undo this decision?", "Why undo it? The pair goes back to the review queue", 0, "Undo decision", true);
    if (why === null) return;
    await api("POST", P(d.project.id) + "/decisions/" + d.action_id + "/undo", { reason: why || "undone from the decisions list" });
    await reload();
    say("Decision undone. The pair is open for review again.", "good");
  };
  const who = (d) => {
    if (!d.run_id || !d.persons.length) return el("span", { class: "mono" }, d.persons.join(", "));
    const go2 = d.persons.length === 2 ? () => viewCompare(d.project.id, d.run_id, d.persons[0], d.persons[1]) : () => viewPerson(d.project.id, d.run_id, d.persons[0]);
    return el("button", { type: "button", class: "link mono", onclick: () => go(go2) }, d.persons.join(" and "));
  };
  const lines = rows.map((d) => el("tr", {}, el("td", {}, when(d.created_at)), opts.project ? null : el("td", {}, d.project.name),
    el("td", {}, (KIND_LABEL[d.kind] || label(d.kind)) + (d.field ? ": " + label(d.field) : "")), el("td", {}, who(d)), el("td", {}, d.yours ? tag("You", "info") : d.actor),
    el("td", {}, d.reason || el("span", { class: "muted" }, "none")), el("td", {}, statusTag(d.status)),
    el("td", { class: "actions-cell" }, can("judgment.write") && d.action_id && ["in_force", "waiting_for_run", "pending_approval"].includes(d.status) ? btn("Undo", () => undo(d), "small", "undo") : null)));
  add(box, el("div", { class: "row" }, el("label", { class: "check" }, mine, "Only my decisions"), field("Status", status)),
    lines.length ? table(["When", ...(opts.project ? [] : ["Project"]), "Decision", "People", "By", "Reason", "Status", ""], lines)
      : empty(state.mine || state.status ? "No decisions match." : "No decisions yet. They appear here as soon as someone decides a pair.",
        can("queue.read") ? btn("Open the review inbox", link("/review"), "primary", "inbox") : null));
  return box;
}

// ---- approvals: everything waiting for someone ------------------------------------------------------------------------------------
async function viewApprovals() {
  here("/approvals");
  await refreshCache();
  const per = await Promise.all(cache.projects.map(async (p) => {
    const [decisions, connections, rules, dests] = await Promise.all([
      can("queue.read") ? api("GET", P(p.id) + "/approvals").catch(() => []) : [],
      can("config.read") ? api("GET", P(p.id) + "/connections").catch(() => []) : [],
      can("config.read") ? Promise.all(CONFIGS.map(([n, t]) => api("GET", P(p.id) + "/config/" + n).then((vs) => vs.filter((v) => v.status === "proposed" || v.status === "approved")
        .map((v) => ({ ...v, name: n, title: t }))).catch(() => []))).then((x) => x.flat()) : [],
      can("results.export") ? api("GET", P(p.id) + "/destinations").catch(() => []) : []]);
    const waitingC = connections.filter((c) => c.latest_status === "proposed" || c.latest_status === "approved");
    const waitingD = dests.filter((d) => d.latest_status === "proposed" || d.latest_status === "approved");
    return { p, decisions, connections: waitingC, rules, dests: waitingD };
  }));
  const sections = [];
  const decisionRows = per.flatMap(({ p, decisions }) => decisions.map((i) => ({ p, i })));
  const act = async (p, i, what) => {
    const why = what === "decline" ? await askReason("Decline this decision", "Why is it declined?", 3, "Decline", true) : await askReason("Approve this decision", "Note", 0, "Approve");
    if (why === null) return;
    await api("POST", P(p.id) + "/approvals/" + i.id + "/" + what, { reason: why });
    await go(viewApprovals);
    say(what === "approve" ? "Approved. It applies at the project's next run." : "Declined. The decision has no effect.", "good");
  };
  const people = (p, i) => (i.run_id && i.persons && i.persons.length ? el("button", { type: "button", class: "link mono", onclick: () => go(i.persons.length === 2
    ? () => viewCompare(p.id, i.run_id, i.persons[0], i.persons[1]) : () => viewPerson(p.id, i.run_id, i.persons[0])) }, i.persons.join(" and ")) : el("span", { class: "mono" }, (i.persons || []).join(", ")));
  if (decisionRows.length) {
    sections.push(el("h2", { class: "section" }, "Review decisions waiting for a second person"),
      table(["Project", "Decision", "People", ["Score"], "Main evidence", "Made by", "Reason", ""], decisionRows.map(({ p, i }) => el("tr", {}, el("td", {}, p.name),
        el("td", {}, (KIND_LABEL[i.kind] || label(i.kind)) + (i.field ? ": " + label(i.field) : "")), el("td", {}, people(p, i)), el("td", { class: "num" }, pct(i.probability)),
        el("td", {}, (i.top_evidence || []).map((x) => label(x.comparison) + ": " + x.level).join("; ")), el("td", {}, i.yours ? tag("You", "info") : i.actor), el("td", {}, i.reason),
        el("td", { class: "actions-cell" }, i.yours ? tag("Someone else approves it", "warn") : can("judgment.approve")
          ? [btn("Approve", () => act(p, i, "approve"), "small primary"), btn("Decline", () => act(p, i, "decline"), "small danger")] : null)))));
  }
  const conn = per.flatMap(({ p, connections }) => connections.map((c) => ({ p, c })));
  if (conn.length) {
    sections.push(el("h2", { class: "section" }, "Connection changes"), table(["Connection", "Project", "Type", "Version", "Status", "Proposed by"], conn.map(({ p, c }) =>
      linkRow(c.id, () => viewConnection(p.id, c.id), el("td", {}, p.name), el("td", {}, label(c.type)), el("td", {}, "Version " + c.latest_version), el("td", {}, statusTag(c.latest_status)),
        el("td", {}, c.updated_by)))));
  }
  const rules = per.flatMap(({ p, rules: rs }) => rs.map((v) => ({ p, v })));
  if (rules.length) {
    sections.push(el("h2", { class: "section" }, "Rule changes"), table(["Rules", "Project", "Version", "Status", "Proposed by", "Reason"], rules.map(({ p, v }) =>
      linkRow(v.title, () => viewProject(p.id, "rules"), el("td", {}, p.name), el("td", {}, "Version " + v.version), el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by), el("td", {}, v.reason)))));
  }
  const dests = per.flatMap(({ p, dests: ds }) => ds.map((d) => ({ p, d })));
  if (dests.length) {
    sections.push(el("h2", { class: "section" }, "Database destinations"), table(["Destination", "Project", "Version", "Status", "Proposed by"], dests.map(({ p, d }) =>
      linkRow(d.id, () => viewProject(p.id, "results"), el("td", {}, p.name), el("td", {}, "Version " + d.latest_version), el("td", {}, statusTag(d.latest_status)), el("td", {}, d.updated_by)))));
  }
  show(page("Approvals", "Everything waiting for someone across your projects: review decisions that need a second person, and changes to connections, rules and destinations."),
    ...(sections.length ? sections : [empty("Nothing waits for approval.")]));
}

// ---- audit log ------------------------------------------------------------------------------------------------------------
const AUDIT_EVENTS = ["auth_failed", "login", "permission_denied", "rate_limited", "request_rejected", "pii_unmasked", "run_started", "run_finished", "run_cancelled", "export",
  "config_change", "judgment_change", "data_deleted", "server_error", "startup"];
async function viewAudit(event) {
  here("/audit");
  const q = "/security/events?newest_first=true&limit=100" + (event ? "&event=" + event : "");
  const [rows] = await Promise.all([api("GET", q), refreshCache()]);
  const filter = el("select", {}, el("option", { value: "" }, "All events"), AUDIT_EVENTS.map((e) => el("option", { value: e, selected: e === event }, label(e))));
  filter.addEventListener("change", () => go(() => viewAudit(filter.value)));
  const verify = async () => {
    const v = await api("GET", "/security/verify");
    say(v.ok ? "The log is intact: the hash chain verifies." : "The log chain is BROKEN. Investigate before trusting it.", v.ok ? "good" : "bad");
  };
  const target = (e) => {
    const known = e.project_id && cache.projects.some((p) => p.id === e.project_id);
    const m = /^([a-z0-9-]+)\/(R[0-9a-f]{12})$/.exec(e.object_ref || "");
    if (known && m && m[1] === e.project_id) return el("button", { type: "button", class: "link mono", onclick: () => go(() => viewRun(m[1], m[2])) }, e.object_ref);
    if (known) return el("button", { type: "button", class: "link mono", onclick: () => go(() => viewProject(e.project_id)) }, e.object_ref || e.project_id);
    return el("span", { class: "mono" }, e.object_ref);
  };
  const rowOf = (e) => el("tr", {}, el("td", {}, when(e.created_at)), el("td", {}, label(e.event)), el("td", {}, e.actor),
    el("td", {}, (e.method ? e.method + " " : "") + (e.route || (e.detail && e.detail.action ? label(e.detail.action) : ""))), el("td", {}, target(e)),
    el("td", { class: "num" }, e.status == null ? "" : e.status));
  const t = table(["Time", "Event", "Who", "What", "Object", ["Status"]], rows.map(rowOf));
  const body = t.querySelector("tbody");
  let last = rows.length ? rows[rows.length - 1].id : null;
  const more = rows.length === 100 ? btn("Show older events", async () => {
    const older = await api("GET", q + "&before_id=" + last);
    body.append(...older.map(rowOf));
    if (older.length) last = older[older.length - 1].id;
    if (older.length < 100) more.remove();
  }, "small") : null;
  show(page("Audit log", "Security-relevant events, newest first. Append-only and hash-chained; personal data values are never written here.",
    [field("Show", filter), btn("Verify integrity", verify, "", "shield")]),
  rows.length ? t : empty("No events."), more ? el("div", { class: "row" }, more) : null);
}

// ---- password, users and roles ---------------------------------------------------------------------------------------------
async function viewPassword(forced) {
  if (!forced) here("/password");
  const cur = el("input", { type: "password", autocomplete: "current-password" });
  const nw = el("input", { type: "password", autocomplete: "new-password" });
  const again = el("input", { type: "password", autocomplete: "new-password" });
  const err = errBox();
  const save = async () => {
    fail(err, "");
    if (nw.value !== again.value) { fail(err, "The two new passwords differ.", again); return; }
    try {
      const r = await api("POST", "/auth/password", { current_password: cur.value, new_password: nw.value });
      token = r.token;
      me = await api("GET", "/me");
    } catch (e) { fail(err, e.message, nw); return; }
    cur.value = ""; nw.value = ""; again.value = "";
    buildNav();
    await go(() => dispatch(forced ? "/" : "/password"));
    say("Your password was changed.", "good");
  };
  focusNext = true;
  show(page(forced ? "Choose a new password" : "Change password",
    forced ? "You signed in with a temporary password. Choose your own before you continue." : "Changing it signs you out of any other session."),
  el("div", { class: "fields" }, field("Current password", cur), field("New password (12 to 128 characters, not your username)", nw), field("Repeat the new password", again)), err,
  el("div", { class: "row" }, btn("Save password", save, "primary")));
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
  here("/users");
  const [users, roles] = await Promise.all([api("GET", "/users"), api("GET", "/roles")]);
  const state = (u) => (u.disabled ? tag("Disabled", "danger") : u.locked ? tag("Locked", "danger") : u.must_change_password ? tag("Temporary password", "warn") : tag("Active", "success"));
  const rows = users.map((u) => el("tr", {}, el("td", {}, u.username), el("td", {}, u.display_name), el("td", {}, roleTags(u.roles)), el("td", {}, state(u)),
    el("td", {}, u.last_login_at ? when(u.last_login_at) : "Never"),
    el("td", { class: "actions-cell" }, btn("Edit", () => userDialog(u, roles), "small"), u.username === me.sub ? null : btn("Reset password", () => resetDialog(u), "small"))));
  show(page("Users", "People who can sign in with a username and password. Roles decide what each person may do.", [btn("New user", () => userDialog(null, roles), "primary", "plus")]),
    rows.length ? table(["Username", "Name", "Roles", "Status", "Last sign-in", ""], rows) : empty("No users yet."));
}
async function userDialog(u, catalogue) {
  const roles = catalogue.roles.filter((r) => heldBy(r.permissions) || (u && u.roles.includes(r.name)));
  const self = !!u && u.username === me.sub;
  const name = el("input", { type: "text", maxlength: "64", value: u ? u.username : "", disabled: !!u });
  const full = el("input", { type: "text", maxlength: "100", value: u ? u.display_name : "" });
  const pass = el("input", { type: "password", autocomplete: "new-password" });
  const off = el("input", { type: "checkbox", checked: !!(u && u.disabled), disabled: self });
  const picks = checkList(roles.map((r) => ({ name: r.name, description: r.description })), new Set(u ? u.roles : []),
    (it) => heldBy(roles.find((r) => r.name === it.name).permissions), self);
  const err = errBox();
  const body = el("div", {}, field("Username (lower-case letters, digits, . - _)", name), field("Name (optional)", full),
    u ? null : field("Temporary password (12 to 128 characters; the person must change it at first sign-in)", pass),
    el("h3", { class: "section" }, "Roles"), self ? el("p", { class: "hint" }, "You cannot change your own roles.") : null, picks.node,
    u ? el("label", { class: "check" }, off, el("span", {}, "Disabled: this person cannot sign in")) : null, err);
  const done = await dialog(u ? "Edit " + u.username : "New user", body, u ? "Save user" : "Create user", async () => {
    try {
      if (u) {
        const patch = { display_name: full.value.trim() };
        if (!self) { patch.roles = picks.values(); patch.disabled = off.checked; }
        await api("PATCH", "/users/" + u.username, patch);
      } else {
        await api("POST", "/users", { username: name.value.trim().toLowerCase(), display_name: full.value.trim(), password: pass.value, roles: picks.values() });
      }
      return true;
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (done) { await go(viewUsers); say(u ? "User saved." : "User created. Give them the temporary password in person; they must change it at first sign-in.", "good"); }
}
async function resetDialog(u) {
  const pass = el("input", { type: "password", autocomplete: "new-password" });
  const err = errBox();
  const body = el("div", {}, el("p", { class: "hint" }, "Their current sessions end and they must choose a new password at the next sign-in."),
    field("New temporary password (12 to 128 characters)", pass), err);
  const done = await dialog("Reset password for " + u.username, body, "Reset password", async () => {
    try { await api("POST", "/users/" + u.username + "/password", { password: pass.value }); return true; } catch (e) { fail(err, e.message, pass); return undefined; }
  });
  if (done) { await go(viewUsers); say("Password reset for " + u.username + ".", "good"); }
}
async function viewRoles() {
  here("/roles");
  const data = await api("GET", "/roles");
  const rows = data.roles.map((r) => el("tr", {}, el("td", {}, label(r.name)), el("td", {}, r.builtin ? tag("Built-in") : tag("Custom", "info")), el("td", {}, r.description),
    el("td", { class: "num" }, r.permissions.length), el("td", { class: "num" }, r.users),
    el("td", { class: "actions-cell" }, btn(r.builtin ? "View" : "Edit", () => roleDialog(r, data.permissions), "small"),
      r.builtin ? null : btn("Delete", () => deleteRole(r), "small danger"))));
  show(page("Roles", "A role is a named set of permissions. Built-in roles are fixed; create your own to give people exactly the access they need. Nobody can give a role more than they hold themselves.",
    [btn("New role", () => roleDialog(null, data.permissions), "primary", "plus")]), table(["Role", "Type", "Description", ["Permissions"], ["People"], ""], rows));
}
async function roleDialog(role, catalogue) {
  const fixed = !!(role && role.builtin);
  const name = el("input", { type: "text", maxlength: "40" });
  const desc = el("input", { type: "text", maxlength: "300", value: role ? role.description : "", disabled: fixed });
  const picks = checkList(catalogue, new Set(role ? role.permissions : []), (it) => heldBy([it.name]), fixed);
  const err = errBox();
  const body = el("div", {}, role ? null : field("Name (lower-case letters, digits, - or _)", name), field("Description", desc),
    el("h3", { class: "section" }, "What this role may do"), picks.node, err);
  const title = role ? (fixed ? label(role.name) + " (built-in, read only)" : "Edit " + role.name) : "New role";
  const done = await dialog(title, body, fixed ? "Close" : "Save role", async () => {
    if (fixed) return true;
    try {
      if (role) await api("PUT", "/roles/" + role.name, { description: desc.value.trim(), permissions: picks.values() });
      else await api("POST", "/roles", { name: name.value.trim().toLowerCase(), description: desc.value.trim(), permissions: picks.values() });
      return true;
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (done && !fixed) { await go(viewRoles); say("Role saved. It applies to everyone holding it from their next request.", "good"); }
}
async function deleteRole(r) {
  if (!(await confirmDialog("Delete role " + r.name + "?", "This cannot be undone. A role that people or projects still use cannot be deleted.", "Delete role", true))) return;
  await api("DELETE", "/roles/" + r.name);
  await go(viewRoles);
  say("Role deleted.", "good");
}

// ---- start ------------------------------------------------------------------------------------------------------------------
initTheme();
initSearch();
initLogin();
$("theme").addEventListener("click", () => setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark", true));
$("login-form").addEventListener("submit", signIn);
$("signout").addEventListener("click", () => signOut(""));
$("password-btn").addEventListener("click", () => go(() => viewPassword(false)));
$("skip").addEventListener("click", (e) => { e.preventDefault(); $("content").focus(); });
window.addEventListener("hashchange", () => {
  const p = readAddress();
  if (p === quietAddress) { quietAddress = null; return; }
  if (me && p.startsWith("/")) go(() => dispatch(p));
});
// Single-key shortcuts of the current screen; never while typing, never with a dialog open, never with Ctrl/Alt/Cmd.
document.addEventListener("keydown", (e) => {
  if (!pageKeys || !$("overlay").hidden || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
  pageKeys(e);
});
