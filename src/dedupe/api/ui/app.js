"use strict";
// Thin client for the Dedupe API. Safety rules for this file: text is only ever put on the page with textContent (never innerHTML),
// the access token lives in a variable (never in storage or the URL), and every request goes to this same origin.
let token = null;
let me = null;
const $ = (id) => document.getElementById(id);

function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(2)) if (k != null) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
function say(msg, kind) { const n = $("notice"); n.textContent = msg || ""; n.className = kind || ""; }
const can = (perm) => !!me && me.permissions.includes(perm);
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g, "");

async function api(method, path, body, extra) {
  const headers = { Authorization: "Bearer " + token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (extra) Object.assign(headers, extra);
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "omit", cache: "no-store" });
  let data = null;
  try { data = await r.json(); } catch (_) { /* no body */ }
  if (r.status === 401) { signOut("Your session ended: sign in again."); throw new Error("unauthorised"); }
  if (!r.ok) {
    const e = (data && data.error) || {};
    const fields = (e.fields || []).map((f) => f.field + " (" + f.problem + ")").join(", ");
    throw new Error((e.message || "Request failed") + (fields ? ": " + fields : ""));
  }
  return data;
}
async function run(fn) { try { say(""); await fn(); } catch (e) { if (e.message !== "unauthorised") say(e.message, "bad"); } }

// ---- sign in / out ---------------------------------------------------------------------------------------------------
async function signIn() {
  token = $("token").value.trim();
  $("token").value = "";
  try {
    me = await api("GET", "/me");
  } catch (e) { token = null; say(e.message === "unauthorised" ? "That token was not accepted." : e.message, "bad"); return; }
  $("login").hidden = true; $("tabs").hidden = false; $("signout").hidden = false;
  $("who").textContent = me.username + " (" + me.roles.join(", ") + ")";
  buildTabs();
}
function signOut(msg) {
  token = null; me = null;
  $("login").hidden = false; $("tabs").hidden = true; $("signout").hidden = true; $("who").textContent = "";
  $("view").replaceChildren(); $("tabs").replaceChildren(); say(msg || "", "");
}

// ---- tabs ------------------------------------------------------------------------------------------------------------
const TABS = [
  ["Review queue", "queue.read", () => viewQueue()], ["People", "person.read", () => viewPeople()], ["Approvals", "judgment.approve", () => viewApprovals()],
  ["Rules", "config.read", () => viewRules()], ["Runs", "runs.read", () => viewRuns()],
];
function buildTabs() {
  const nav = $("tabs"); nav.replaceChildren();
  for (const [name, perm, fn] of TABS) {
    if (!can(perm)) continue;
    const b = el("button", { onclick: () => { for (const x of nav.children) x.classList.remove("on"); b.classList.add("on"); say(""); run(fn); } }, name);
    nav.append(b);
  }
  if (nav.firstChild) nav.firstChild.click(); else $("view").replaceChildren(el("p", { class: "muted" }, "Your roles give no access to any screen."));
}
const show = (...nodes) => $("view").replaceChildren(...nodes);
const pct = (x) => (x == null ? "" : (x * 100).toFixed(1) + "%");
const head = (cols) => el("thead", {}, el("tr", {}, cols.map((h) => el("th", {}, h))));

// ---- review queue ----------------------------------------------------------------------------------------------------
async function viewQueue() {
  const [summary, items] = await Promise.all([api("GET", "/queue/summary"), api("GET", "/queue?limit=50")]);
  const table = el("table", {}, head(["Person A", "Person B", "Records", "Best match", "Priority", ""]),
    el("tbody", {}, items.map((i) => el("tr", { class: "click", onclick: () => run(() => viewCompare(i.person_l, i.person_r)) },
      el("td", {}, i.person_l), el("td", {}, i.person_r), el("td", {}, i.size_l + " + " + i.size_r), el("td", {}, pct(i.top_probability)),
      el("td", {}, i.priority.toFixed(2)), el("td", {}, i.forced ? el("span", { class: "pill" }, "flagged by rule") : "")))));
  show(el("h2", {}, "Review queue"), el("p", { class: "muted" }, summary.open_now + " open now, " + summary.pending_approval + " waiting for a second person. Click a row to compare."),
    items.length ? table : el("p", {}, "Nothing to review."));
}
function personCard(p) {
  const g = p.golden || {};
  const rows = Object.entries(g).filter(([, v]) => v != null && v !== "").map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", {}, String(v))));
  return el("div", { class: "card" }, el("strong", {}, p.person_id), " ", el("span", { class: "muted" }, p.record_count + " record(s)"),
    el("table", {}, el("tbody", {}, rows)), p.unmasked ? el("p", { class: "bad" }, "Identifiers are shown unmasked; this view is logged.") : null);
}
async function viewCompare(l, r, reveal) {
  let path = "/people/" + l + "/compare/" + r;
  if (reveal) path += "?unmask=true&reason=" + encodeURIComponent(reveal);
  const c = await api("GET", path);
  const why = (list) => (list.length ? el("ul", {}, list.map((x) => el("li", {}, x.comparison + ": " + x.level + " (" + x.bits.toFixed(1) + " bits)"))) : el("p", { class: "muted" }, "None."));
  const reason = el("input", { type: "text", placeholder: "Reason (needed to see identifiers; optional for decisions)", maxlength: "300" });
  const decide = (kind) => run(async () => {
    const out = await api("POST", "/decisions/" + kind, { person_l: l, person_r: r, reason: reason.value }, { "Idempotency-Key": uuid() });
    say("Recorded (action " + out.action_id + (out.pending_approval ? ", waiting for a second person" : "") + ").", "good");
    await viewQueue();
  });
  const buttons = [];
  if (can("judgment.write")) {
    buttons.push(el("button", { class: "primary", onclick: () => decide("approve") }, "Same person"),
      el("button", { class: "danger", onclick: () => decide("reject") }, "Different people"), el("button", { onclick: () => decide("unsure") }, "Not sure"));
  }
  if (can("pii.unmask") && !c.unmasked) {
    buttons.push(el("button", { onclick: () => {
      if (reason.value.trim().length < 10) { say("Give a reason of at least 10 characters to see identifiers.", "bad"); return; }
      run(() => viewCompare(l, r, reason.value.trim())); } }, "Show identifiers"));
  }
  show(el("p", {}, el("button", { onclick: () => run(() => viewQueue()) }, "Back to queue")),
    el("h2", {}, "Compare " + l + " and " + r),
    el("p", {}, "Match score " + pct(c.probability) + (c.decided_by ? " (decided by: " + c.decided_by + ")" : "") +
      (c.hard_conflicts.length ? ". Conflicting identifiers: " + c.hard_conflicts.join(", ") : "")),
    el("div", { class: "cols" }, c.persons.map(personCard)),
    el("div", { class: "cols" }, el("div", {}, el("h3", {}, "Why they may be the same"), why(c.why_match)), el("div", {}, el("h3", {}, "Why they may differ"), why(c.why_not))),
    el("div", { class: "row" }, reason, buttons));
}

// ---- people ----------------------------------------------------------------------------------------------------------
async function viewPeople() {
  const id = el("input", { type: "text", placeholder: "Person id, e.g. P000000123", maxlength: "10" });
  const out = el("div");
  const open = () => run(async () => {
    const v = await api("GET", "/people/" + encodeURIComponent(id.value.trim()));
    out.replaceChildren(personCard(v), el("h3", {}, "Source records"), el("table", {}, head(["Source", "Record", "Name as written", "City"]),
      el("tbody", {}, v.records.map((r) => el("tr", {}, el("td", {}, r.source_id), el("td", {}, r.source_record_id), el("td", {}, r.name_as_written || ""), el("td", {}, r.city || ""))))));
  });
  show(el("h2", {}, "People"), el("div", { class: "row" }, id, el("button", { onclick: open }, "Open")), out);
}

// ---- approvals (second person) ---------------------------------------------------------------------------------------
async function viewApprovals() {
  const list = await api("GET", "/approvals");
  const act = (i, what) => run(async () => {
    const reason = prompt(what === "decline" ? "Reason for declining (required):" : "Note (optional):") || "";
    await api("POST", "/approvals/" + i.id + "/" + what, { reason });
    say("Done.", "good"); await viewApprovals();
  });
  show(el("h2", {}, "Waiting for a second person"), list.length ? el("table", {}, head(["#", "What", "Made by", "Reason", ""]),
    el("tbody", {}, list.map((i) => el("tr", {}, el("td", {}, i.id), el("td", {}, i.kind + (i.persons ? " " + i.persons.join(", ") : "")), el("td", {}, i.actor), el("td", {}, i.reason),
      el("td", {}, el("button", { class: "primary", onclick: () => act(i, "approve") }, "Approve"), " ", el("button", { class: "danger", onclick: () => act(i, "decline") }, "Decline"))))))
    : el("p", {}, "Nothing is waiting."));
}

// ---- rules -----------------------------------------------------------------------------------------------------------
async function viewRules(name) {
  name = name || "decision";
  const versions = await api("GET", "/config/" + name);
  const picker = el("select", { "aria-label": "Which configuration", onchange: () => run(() => viewRules(picker.value)) },
    ["decision", "survivorship", "settings"].map((n) => el("option", { value: n, selected: n === name }, n)));
  const result = el("pre", {}, "");
  const act = (v, what) => run(async () => {
    const reason = prompt("Reason (required):") || "";
    if (!reason) return;
    const out = await api("POST", "/config/" + name + "/versions/" + v.version + "/" + what, { reason });
    say(what + " done" + (out.applies ? " (" + out.applies + ")" : "") + ".", "good"); await viewRules(name);
  });
  const dry = (v) => run(async () => {
    const r = await api("POST", "/config/" + name + "/versions/" + v.version + "/dry-run", {});
    result.textContent = JSON.stringify({ pairs: r.pairs, zones_before: r.zones_before, zones_after: r.zones_after, moved: r.moved, changed_by_rule: r.changed_by_rule }, null, 2);
  });
  const back = (v) => run(async () => {
    const reason = prompt("Reason for going back to v" + v.version + " (required):") || "";
    if (!reason) return;
    await api("POST", "/config/" + name + "/rollback", { to_version: v.version, reason });
    say("Rolled back.", "good"); await viewRules(name);
  });
  const buttons = (v) => [
    name === "decision" && can("config.propose") ? el("button", { onclick: () => dry(v) }, "Dry run") : null,
    can("config.approve") && v.status === "proposed" ? el("button", { onclick: () => act(v, "approve") }, "Approve") : null,
    can("config.approve") && v.status === "proposed" ? el("button", { class: "danger", onclick: () => act(v, "decline") }, "Decline") : null,
    can("config.activate") && v.status === "proposed" && v.approved_by.length ? el("button", { class: "primary", onclick: () => act(v, "activate") }, "Make live") : null,
    can("config.activate") && v.status === "superseded" ? el("button", { onclick: () => back(v) }, "Roll back to this") : null,
  ].filter(Boolean).flatMap((b) => [b, " "]);
  const rows = versions.map((v) => el("tr", {}, el("td", {}, "v" + v.version), el("td", {}, el("span", { class: "pill" }, v.status)), el("td", {}, v.proposed_by),
    el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")), el("td", {}, buttons(v))));
  const parts = [el("h2", {}, "Rules and settings"), el("div", { class: "row" }, picker),
    versions.length ? el("table", {}, head(["Version", "Status", "Proposed by", "Reason", "Approved by", ""]), el("tbody", {}, rows)) : el("p", {}, "No versions yet."), result];
  if (can("config.propose")) {
    const editor = el("textarea", { "aria-label": "Rule content as JSON", spellcheck: "false" });
    const why = el("input", { type: "text", placeholder: "Why this change (at least 10 characters)", maxlength: "300" });
    const propose = () => run(async () => {
      let content;
      try { content = JSON.parse(editor.value); } catch (_) { throw new Error("That is not valid JSON."); }
      const out = await api("POST", "/config/" + name + "/propose", { content, reason: why.value });
      say("Proposed as version " + out.version + ".", "good"); await viewRules(name);
    });
    parts.push(el("h3", {}, "Propose a new version (JSON; checked on the server; nothing changes until it is approved and made live)"), editor,
      el("div", { class: "row" }, why, el("button", { class: "primary", onclick: propose }, "Propose")));
    if (versions.length) {
      api("GET", "/config/" + name + "/versions/" + versions[versions.length - 1].version).then((v) => { editor.value = JSON.stringify(v.content, null, 2); }).catch(() => {});
    }
  }
  show(...parts);
}

// ---- runs ------------------------------------------------------------------------------------------------------------
async function viewRuns() {
  const jobs = await api("GET", "/runs");
  const start = () => run(async () => {
    const reason = prompt("Why start a run? (optional)") || "";
    await api("POST", "/runs", { reason }, { "Idempotency-Key": uuid() });
    say("Run started. Reads are paused until it finishes.", "good"); await viewRuns();
  });
  show(el("h2", {}, "Pipeline runs"),
    el("div", { class: "row" }, can("runs.start") ? el("button", { class: "primary", onclick: start }, "Start a run") : null, el("button", { onclick: () => run(() => viewRuns()) }, "Refresh")),
    jobs.length ? el("table", {}, head(["Run", "Status", "Started by", "Started", "Finished", "Exit"]),
      el("tbody", {}, jobs.map((j) => el("tr", {}, el("td", {}, j.id), el("td", {}, el("span", { class: "pill" }, j.status)), el("td", {}, j.requested_by),
        el("td", {}, j.started_at || ""), el("td", {}, j.finished_at || ""), el("td", {}, j.exit_code == null ? "" : String(j.exit_code))))))
      : el("p", {}, "No runs yet."));
}

$("signin").addEventListener("click", signIn);
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") signIn(); });
$("signout").addEventListener("click", () => signOut(""));
