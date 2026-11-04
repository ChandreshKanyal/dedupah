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
  down: "m6 9 6 6 6-6",
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
let arrivedAt = null;           // the address a person arrived by (Back, a link, a reload): a screen that shows another address replaces it instead of adding a step to Back
const PART = /^[A-Za-z0-9_.-]{1,64}$/;
function readAddress() { return window.location.hash.replace(/^#/, ""); }
function writeAddress(path) {
  const arrived = arrivedAt;
  arrivedAt = null;
  if (readAddress() === path) return;
  if (arrived !== null && arrived !== path) { window.history.replaceState(null, "", "#" + path); return; }      // the portal rewrote the address it was given (an old link, a shortcut)
  quietAddress = path;
  window.location.hash = path;
}
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
    if (c === "map" && d) return mapUpload(b, d);
    if (c === "new-run") return newRun(b);
    return viewProject(b, c, d);
  }
  // runs and settings were pages of their own before the four-item menu: their old links open where those things are now
  const top = { runs: viewProjects, "new-run": () => newRun(), "new-project": newProject, review: () => (b === "decisions" ? viewDecisions() : viewInbox(b, c, d)), approvals: viewApprovals,
    audit: () => viewAudit(), users: viewUsers, roles: viewRoles, password: () => viewPassword(false), account: viewAccount, admin: viewAdmin,
    settings: () => (canNav("admin") ? viewAdmin() : viewAccount()) }[a];
  return top ? top() : viewHome();
}
const SECTIONS = [["/review", "Review"], ["/new-run", "Projects"], ["/new-project", "Projects"], ["/projects", "Projects"], ["/approvals", "Admin"],
  ["/audit", "Admin"], ["/users", "Admin"], ["/roles", "Admin"], ["/admin", "Admin"]];
// Every screen says where it is: the address (so Back works), the highlighted menu item and the window title (set by show()).
function here(path) {
  writeAddress(path);
  const sec = path === "/" ? "Home" : (SECTIONS.find(([p]) => path.startsWith(p)) || [null, ""])[1];
  sectionIcon = (NAV.find(([name]) => name === sec) || [null, null, "home"])[2];
  if (/\/connections|\/add-data|\/map\//.test(path)) sectionIcon = "plug";
  if (/^\/(account|password)/.test(path)) sectionIcon = "user";
  for (const a of $("nav").querySelectorAll("a")) {
    if (a.dataset.name === sec) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
}
const link = (path) => () => go(() => dispatch(path));
const P = (pid) => "/projects/" + pid;
const R = (pid, rid) => P(pid) + "/runs/" + rid;
