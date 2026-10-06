"use strict";
// Thin client for the Dedupe API. Safety rules for this file: text is only ever put on the page with textContent (never innerHTML),
// the access token lives in a variable (never in storage or the URL), and every request goes to this same origin.
let token = null;
let me = null;
let timer = null;
const $ = (id) => document.getElementById(id);

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
const can = (perm) => !!me && me.permissions.includes(perm);
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g, "");
const pct = (x) => (x == null ? "" : (x * 100).toFixed(1) + "%");
const when = (t) => (t ? new Date(t).toLocaleString() : "");
const num = (n) => (n == null ? "" : Number(n).toLocaleString());

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
    const ok = el("button", { class: danger ? "danger" : "primary", onclick: () => { const v = onConfirm(); if (v !== undefined) close(v); } }, confirmLabel);
    box.replaceChildren(el("h2", { id: "dialog-title" }, title), body,
      el("div", { class: "buttons" }, el("button", { onclick: () => close(null) }, "Cancel"), ok));
    $("overlay").hidden = false;
    document.addEventListener("keydown", onKey);
    const first = box.querySelector("textarea, input");
    (first || ok).focus();
  });
}
// Asks for a written reason. Resolves to the text, or null when cancelled.
function askReason(title, label, minLength, confirmLabel, danger) {
  const area = el("textarea", { class: "plain", maxlength: "300", "aria-label": label });
  const err = el("p", { class: "bad", hidden: true }, "A reason of at least " + minLength + " characters is needed.");
  const body = el("div", {}, el("p", { class: "hint" }, label + (minLength ? " (at least " + minLength + " characters)" : " (optional)")), area, err);
  return dialog(title, body, confirmLabel || "Confirm", () => {
    const v = area.value.trim();
    if (v.length < minLength) { err.hidden = false; return undefined; }
    return v;
  }, danger);
}

// ---- requests ----------------------------------------------------------------------------------------------------------
async function api(method, path, body, extra) {
  const headers = { Authorization: "Bearer " + token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (extra) Object.assign(headers, extra);
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "omit", cache: "no-store" });
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
  $("who").replaceChildren(el("strong", {}, me.username), " (" + me.roles.join(", ") + ")");
  buildNav();
}
function signOut(msg) {
  token = null; me = null;
  if (timer) { clearInterval(timer); timer = null; }
  $("app").hidden = true;
  $("login").hidden = false;
  $("nav").replaceChildren(); $("view").replaceChildren(); say("");
  const fail = $("login-error");
  fail.hidden = !msg;
  fail.textContent = msg || "";
}

// ---- navigation --------------------------------------------------------------------------------------------------------
const NAV = [
  ["Review", [["Review queue", "queue.read", () => viewQueue()], ["People", "person.read", () => viewPeople()], ["Approvals", "judgment.approve", () => viewApprovals()]]],
  ["Configure", [["Sources", "sources.manage", () => viewSources()], ["Rules and settings", "config.read", () => viewRules()]]],
  ["Operate", [["Pipeline runs", "runs.read", () => viewRuns()]]],
];
function go(link, fn) {
  if (timer) { clearInterval(timer); timer = null; }
  for (const a of $("nav").querySelectorAll("a")) a.removeAttribute("aria-current");
  if (link) link.setAttribute("aria-current", "page");
  run(fn);
}
function buildNav() {
  const nav = $("nav");
  nav.replaceChildren();
  let first = null;
  for (const [group, items] of NAV) {
    const allowed = items.filter((i) => can(i[1]));
    if (!allowed.length) continue;
    nav.append(el("h2", {}, group));
    for (const [name, , fn] of allowed) {
      const a = el("a", { href: "#", role: "link", onclick: (e) => { e.preventDefault(); go(a, fn); } }, name);
      nav.append(a);
      first = first || a;
    }
  }
  if (first) go(first, NAV.flatMap((g) => g[1]).find((i) => can(i[1]))[2]);
  else $("view").replaceChildren(el("div", { class: "empty" }, "Your roles give no access to any screen."));
}

// ---- page building blocks ------------------------------------------------------------------------------------------------
const show = (...nodes) => $("view").replaceChildren(...nodes.filter((n) => n != null && n !== false));
function page(title, subtitle, actions, crumbs) {
  return el("div", { class: "page-head" }, el("div", {},
    crumbs ? el("p", { class: "crumbs" }, crumbs) : null, el("h1", {}, title), subtitle ? el("p", {}, subtitle) : null),
  el("div", { class: "actions" }, actions || []));
}
const head = (cols) => el("thead", {}, el("tr", {}, cols.map((h) => el("th", { class: Array.isArray(h) ? "num" : "" }, Array.isArray(h) ? h[0] : h))));
const table = (cols, rows, cls) => el("table", { class: cls || "" }, head(cols), el("tbody", {}, rows));
const empty = (text) => el("div", { class: "empty" }, text);
const tag = (text, kind) => el("span", { class: "tag" + (kind ? " " + kind : "") }, text);
const statusTag = (s) => tag(s, { active: "success", succeeded: "success", failed: "danger", declined: "danger", interrupted: "danger", proposed: "info", running: "info", queued: "info" }[s]);
const field = (label, input) => el("div", { class: "field" }, el("label", {}, label), input);
const btn = (text, onclick, cls) => el("button", { class: cls || "", onclick }, text);
const tabs = (names, current, pick) => el("div", { class: "tabs", role: "tablist" }, names.map((n) =>
  el("button", { role: "tab", "aria-selected": n[0] === current ? "true" : "false", onclick: () => run(() => pick(n[0])) }, n[1])));

// ---- review queue ------------------------------------------------------------------------------------------------------
async function viewQueue(status) {
  status = status || "open";
  const [summary, items] = await Promise.all([api("GET", "/queue/summary"), api("GET", "/queue?limit=50&status=" + status)]);
  const filter = el("select", { "aria-label": "Show", onchange: () => run(() => viewQueue(filter.value)) },
    [["open", "Open"], ["unsure", "Marked not sure"], ["all", "All"]].map(([v, t]) => el("option", { value: v, selected: v === status }, t)));
  const rows = items.map((i) => el("tr", { class: "click", onclick: () => run(() => viewCompare(i.person_l, i.person_r)) },
    el("td", {}, i.person_l), el("td", {}, i.person_r), el("td", { class: "num" }, i.size_l + " and " + i.size_r), el("td", { class: "num" }, pct(i.top_probability)),
    el("td", {}, i.forced ? tag("Flagged by a rule", "info") : ""), el("td", {}, i.state === "open" ? "" : tag(i.state))));
  show(page("Review queue", num(summary.open_now) + " open. " + num(summary.pending_approval) + " waiting for a second person.", [filter]),
    items.length ? table(["Person A", "Person B", ["Records"], ["Match score"], "Reason", "State"], rows) : empty("Nothing to review here."));
}

function personBlock(p) {
  const g = p.golden || {};
  const rows = Object.entries(g).filter(([, v]) => v != null && v !== "").map(([k, v]) => el("tr", {}, el("th", {}, k.replace(/_/g, " ")), el("td", {}, String(v))));
  return el("div", {}, el("h3", { class: "section" }, p.person_id + " (" + p.record_count + (p.record_count === 1 ? " record)" : " records)")),
    rows.length ? el("table", { class: "kv" }, el("tbody", {}, rows)) : empty("No values."));
}
async function viewCompare(l, r, reveal) {
  let path = "/people/" + l + "/compare/" + r;
  if (reveal) path += "?unmask=true&reason=" + encodeURIComponent(reveal);
  const c = await api("GET", path);
  const note = el("textarea", { class: "plain", maxlength: "300", "aria-label": "Decision note", placeholder: "Optional note, kept with the decision." });
  const decide = (kind) => run(async () => {
    const out = await api("POST", "/decisions/" + kind, { person_l: l, person_r: r, reason: note.value }, { "Idempotency-Key": uuid() });
    await viewQueue();
    say("Decision recorded" + (out.pending_approval ? ". It waits for a second person to approve it." : "."), "good");
  });
  const actions = [];
  if (can("pii.unmask") && !c.unmasked) {
    actions.push(btn("Show identifiers", () => run(async () => {
      const why = await askReason("Show identifiers", "Why do you need to see them? This is logged", 10, "Show");
      if (why) await viewCompare(l, r, why);
    })));
  }
  if (can("judgment.write")) {
    actions.push(btn("Not sure", () => decide("unsure")), btn("Different people", () => decide("reject"), "danger"), btn("Same person", () => decide("approve"), "primary"));
  }
  const why = (list) => (list.length ? el("ul", { class: "plain" }, list.map((x) => el("li", {}, x.comparison.replace(/_/g, " ") + ": " + x.level + " (" + x.bits.toFixed(1) + " bits)")))
    : el("p", { class: "muted" }, "None."));
  show(page("Compare people", "Match score " + pct(c.probability) + (c.decided_by ? ". Decided by rule: " + c.decided_by : "") +
      (c.hard_conflicts.length ? ". Conflicting identifiers: " + c.hard_conflicts.join(", ") : ""), actions,
    el("span", {}, btn("Review queue", () => run(() => viewQueue()), "link"), " / " + l + " and " + r)),
  c.unmasked ? el("p", { class: "hint" }, "Identifiers are shown in full. This view is logged.") : null,
  el("div", { class: "cols" }, c.persons.map(personBlock)),
  el("div", { class: "cols" }, el("div", {}, el("h3", { class: "section" }, "Evidence for the same person"), why(c.why_match)),
    el("div", {}, el("h3", { class: "section" }, "Evidence for different people"), why(c.why_not))),
  can("judgment.write") ? el("div", {}, el("h3", { class: "section" }, "Decision note"), note) : null);
}

// ---- people ------------------------------------------------------------------------------------------------------------
async function viewPeople() {
  const id = el("input", { type: "text", placeholder: "Person id, for example P000000123", maxlength: "10", "aria-label": "Person id" });
  const out = el("div");
  const open = () => run(async () => {
    const v = await api("GET", "/people/" + encodeURIComponent(id.value.trim()));
    out.replaceChildren(personBlock(v), el("h3", { class: "section" }, "Source records"),
      table(["Source", "Record", "Name as written", "City"], v.records.map((r) => el("tr", {}, el("td", {}, r.source_id), el("td", {}, r.source_record_id),
        el("td", {}, r.name_as_written || ""), el("td", {}, r.city || "")))));
  });
  id.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  show(page("People", "Look up a person and the source records behind them."), el("div", { class: "row" }, id, btn("Open", open, "primary")), out);
}

// ---- approvals ---------------------------------------------------------------------------------------------------------
async function viewApprovals() {
  const list = await api("GET", "/approvals");
  const act = (i, what) => run(async () => {
    const why = what === "decline" ? await askReason("Decline decision " + i.id, "Why is it declined?", 3, "Decline", true)
      : await askReason("Approve decision " + i.id, "Note", 0, "Approve");
    if (why === null) return;
    await api("POST", "/approvals/" + i.id + "/" + what, { reason: why });
    await viewApprovals();
    say("Done.", "good");
  });
  show(page("Approvals", "Decisions that need a second person before they take effect."),
    list.length ? table(["Id", "Decision", "Made by", "Reason", ""], list.map((i) => el("tr", {}, el("td", {}, i.id),
      el("td", {}, i.kind.replace(/_/g, " ") + (i.persons ? " " + i.persons.join(", ") : "")), el("td", {}, i.actor), el("td", {}, i.reason),
      el("td", { class: "actions-cell" }, btn("Approve", () => act(i, "approve"), "small"), btn("Decline", () => act(i, "decline"), "small danger")))))
      : empty("Nothing is waiting for approval."));
}

// ---- rules and settings ------------------------------------------------------------------------------------------------
const CONFIGS = [["decision", "Matching rules"], ["survivorship", "Golden record"], ["settings", "Run settings"], ["sources", "Sources"]];
async function viewRules(name) {
  name = name || "decision";
  const versions = await api("GET", "/config/" + name);
  const result = el("div");
  const act = (v, what, title, minLength, label, danger) => run(async () => {
    const why = await askReason(title, label, minLength, title.split(" ")[0], danger);
    if (why === null) return;
    const out = await api("POST", "/config/" + name + "/versions/" + v.version + "/" + what, { reason: why });
    await viewRules(name);
    say(title + ": done" + (out.applies ? ". Applies " + out.applies + "." : "."), "good");
  });
  const dry = (v) => run(async () => {
    const r = await api("POST", "/config/" + name + "/versions/" + v.version + "/dry-run", {});
    const zones = ["auto_merge", "review", "reject"];
    result.replaceChildren(el("h2", { class: "section" }, "Dry run of version " + v.version + " on " + num(r.pairs) + " scored pairs from the last run"),
      table(["Decision", ["Before"], ["After"], ["Change"]], zones.map((z) => el("tr", {}, el("td", {}, z.replace("_", " ")), el("td", { class: "num" }, num(r.zones_before[z])),
        el("td", { class: "num" }, num(r.zones_after[z])), el("td", { class: "num" }, (r.zones_after[z] - r.zones_before[z] > 0 ? "+" : "") + num(r.zones_after[z] - r.zones_before[z]))))),
      Object.keys(r.moved).length ? el("p", { class: "hint" }, "Moved: " + Object.entries(r.moved).map(([k, n]) => k + " " + num(n)).join(", ")) : el("p", { class: "hint" }, "No pair changes decision."),
      el("p", { class: "hint" }, r.note));
  });
  const back = (v) => run(async () => {
    const why = await askReason("Roll back to version " + v.version, "Why go back?", 10, "Roll back", true);
    if (why === null) return;
    await api("POST", "/config/" + name + "/rollback", { to_version: v.version, reason: why });
    await viewRules(name);
    say("Rolled back. It applies from the next pipeline run.", "good");
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
  const parts = [page("Rules and settings", "Every change is proposed, approved by a different person, and then made live. Runs use the live version."),
    tabs(CONFIGS, name, viewRules),
    versions.length ? table(["Version", "Status", "Proposed by", "Reason", "Approved by", ""], rows) : empty("No versions yet."), result];
  if (can("config.propose") && name !== "sources") {
    const editor = el("textarea", { "aria-label": "Content as JSON", spellcheck: "false" });
    const why = el("input", { type: "text", placeholder: "Reason for this change (at least 10 characters)", maxlength: "300", "aria-label": "Reason" });
    const propose = () => run(async () => {
      let content;
      try { content = JSON.parse(editor.value); } catch (_) { throw new Error("That is not valid JSON."); }
      const out = await api("POST", "/config/" + name + "/propose", { content, reason: why.value });
      await viewRules(name);
      say("Proposed as version " + out.version + ". Another person must approve it.", "good");
    });
    parts.push(el("h2", { class: "section" }, "Propose a new version"),
      el("p", { class: "hint" }, "Edit a copy of the latest version. The server checks it; nothing changes until it is approved and made live."), editor,
      el("div", { class: "row" }, why, btn("Propose", propose, "primary")));
    if (versions.length) {
      api("GET", "/config/" + name + "/versions/" + versions[versions.length - 1].version).then((v) => { editor.value = JSON.stringify(v.content, null, 2); }).catch(() => {});
    }
  } else if (name === "sources") {
    parts.push(el("p", { class: "hint" }, "Sources are proposed from the Sources screen, where they are checked against the real source."));
  }
  show(...parts);
}

// ---- sources and mapping -----------------------------------------------------------------------------------------------
async function viewSources() {
  const [opts, versions] = await Promise.all([api("GET", "/sources/options"), api("GET", "/config/sources")]);
  const live = versions.find((v) => v.status === "active");
  const current = live ? (await api("GET", "/config/sources/versions/" + live.version)).content : { sources: [] };
  const state = { source: { source_id: "", connector: { type: "csv" }, record_id: null, fields: {}, attributes: [] } };
  const out = el("div");
  const json = el("textarea", { "aria-label": "Source as JSON", spellcheck: "false" });
  const sync = () => { json.value = JSON.stringify(state.source, null, 2); };
  const fromJson = () => {
    try { state.source = JSON.parse(json.value); return true; } catch (_) { say("The advanced box is not valid JSON.", "bad"); return false; }
  };
  const typeSel = el("select", { "aria-label": "Source type" }, Object.keys(opts.connectors).map((t) => el("option", { value: t }, t)));
  const idIn = el("input", { type: "text", placeholder: "For example bank_crm", maxlength: "40", "aria-label": "Source id" });
  const settings = el("div");
  const mapping = el("div");

  function buildForm() {
    idIn.value = state.source.source_id || "";
    typeSel.value = state.source.connector.type in opts.connectors ? state.source.connector.type : "csv";
    settings.replaceChildren(el("p", { class: "hint" }, opts.connectors[typeSel.value].help));
    for (const k of opts.connectors[typeSel.value].settings) {
      const i = el("input", { type: "text", "aria-label": k, value: state.source.connector[k] || "" });
      i.addEventListener("input", () => { state.source.connector[k] = i.value; if (!i.value) delete state.source.connector[k]; sync(); });
      settings.append(field(k.replace(/_/g, " "), i));
    }
    mapping.replaceChildren();
  }
  typeSel.addEventListener("change", () => { state.source.connector = { type: typeSel.value }; sync(); buildForm(); });
  idIn.addEventListener("input", () => { state.source.source_id = idIn.value; sync(); });
  json.addEventListener("change", () => { if (fromJson()) buildForm(); });

  const mappingForm = (info) => {
    const cols = info.columns;
    const pick = (value, onchange, label) => {
      const s = el("select", { "aria-label": label }, el("option", { value: "" }, "Not mapped"), cols.map((c) => el("option", { value: c, selected: c === value }, c)));
      s.addEventListener("change", () => { onchange(s.value); sync(); });
      return s;
    };
    const suggested = Object.fromEntries(info.suggestions.map((x) => [x.field, x.column]));
    if (!Object.keys(state.source.fields).length) for (const [f, c] of Object.entries(suggested)) state.source.fields[f] = c;
    const rows = opts.canonical_fields.map((f) => {
      const cur = state.source.fields[f];
      const simple = typeof cur === "string" || cur === undefined;
      return el("tr", {}, el("td", {}, f.replace(/_/g, " ")),
        el("td", {}, simple ? pick(cur || "", (v) => { if (v) state.source.fields[f] = v; else delete state.source.fields[f]; }, f)
          : el("span", { class: "muted" }, "Combined columns. Edit in the advanced box.")),
        el("td", {}, suggested[f] && suggested[f] === cur ? tag("Suggested") : ""));
    });
    const attrs = el("div", {}, cols.map((c) => {
      const cb = el("input", { type: "checkbox", checked: (state.source.attributes || []).includes(c) });
      cb.addEventListener("change", () => {
        const set = new Set(state.source.attributes || []);
        if (cb.checked) set.add(c); else set.delete(c);
        state.source.attributes = [...set]; sync();
      });
      return el("label", { class: "check" }, cb, c);
    }));
    sync();
    return [el("h3", { class: "section" }, "Sample rows (values are shortened)"), table(cols, info.sample.map((r) => el("tr", {}, cols.map((c) => el("td", {}, r[c] == null ? "" : r[c]))))),
      el("h3", { class: "section" }, "Map columns to fields"), table(["Field", "Source column", ""], rows),
      el("h3", { class: "section" }, "Record identifier"), el("p", { class: "hint" }, "The column that identifies a record in the source."),
      pick(state.source.record_id || "", (v) => { state.source.record_id = v || null; }, "Record identifier"),
      el("h3", { class: "section" }, "Other columns to keep"), attrs];
  };
  const openIt = () => run(async () => {
    if (!fromJson()) return;
    const info = await api("POST", "/sources/inspect", { connector: state.source.connector });
    mapping.replaceChildren(...mappingForm(info));
  });
  const previewIt = () => run(async () => {
    if (!fromJson()) return;
    const r = await api("POST", "/sources/preview", state.source);
    const cols = Object.keys(r.rows[0] || {}).filter((c) => c !== "attributes");
    out.replaceChildren(el("h3", { class: "section" }, "Preview on " + r.sample_size + " sample rows"),
      table(["Field", ["Rows filled"]], Object.entries(r.filled).map(([f, n]) => el("tr", {}, el("td", {}, f.replace(/_/g, " ")), el("td", { class: "num" }, n + " of " + r.sample_size)))),
      r.rows.length ? el("div", {}, el("h3", { class: "section" }, "Standardised sample (values are shortened)"),
        table(cols, r.rows.map((row) => el("tr", {}, cols.map((c) => el("td", {}, row[c] == null ? "" : row[c]))))) ) : null);
  });
  const proposeIt = () => run(async () => {
    if (!fromJson()) return;
    const why = await askReason("Propose sources", "Why this change?", 10, "Propose");
    if (why === null) return;
    const rest = current.sources.filter((s) => s.source_id !== state.source.source_id);
    const res = await api("POST", "/sources/propose", { content: { sources: [...rest, state.source] }, reason: why });
    await viewSources();
    say("Proposed as version " + res.version + ". Another person approves it under Rules and settings, then makes it live.", "good");
  });
  const liveRows = current.sources.map((s) => el("tr", {}, el("td", {}, s.source_id), el("td", {}, s.connector.type), el("td", { class: "num" }, Object.keys(s.fields).length),
    el("td", { class: "actions-cell" }, btn("Edit", () => {
      state.source = JSON.parse(JSON.stringify(s)); sync(); buildForm();
      say("Loaded " + s.source_id + ". Open the source again to change its mapping, or edit the advanced box.", "");
    }, "small"))));
  buildForm(); sync();
  show(page("Sources", live ? "Live version " + live.version + ". A run reads all sources listed here." : "No live version yet."),
    el("p", { class: "hint" }, "Passwords and tokens are never entered here. Give the name of an environment variable that starts with DEDUPE_SRC_."),
    liveRows.length ? table(["Source", "Type", ["Fields mapped"], ""], liveRows) : empty("No sources yet."),
    el("h2", { class: "section" }, "1. Connect"), field("Source id", idIn), field("Type", typeSel), settings,
    el("div", { class: "row" }, btn("Open the source and suggest a mapping", openIt)), mapping,
    el("h2", { class: "section" }, "2. Advanced (optional)"), el("p", { class: "hint" }, "The source as JSON. Use it for combined columns and conditions."), json,
    el("h2", { class: "section" }, "3. Preview and propose"), el("div", { class: "row" }, btn("Preview", previewIt), btn("Propose", proposeIt, "primary")), out);
}

// ---- pipeline runs -----------------------------------------------------------------------------------------------------
async function viewRuns() {
  if (timer) { clearInterval(timer); timer = null; }
  const jobs = await api("GET", "/runs");
  const start = () => run(async () => {
    const why = await askReason("Start a pipeline run", "Why start a run?", 0, "Start run");
    if (why === null) return;
    await api("POST", "/runs", { reason: why }, { "Idempotency-Key": uuid() });
    await viewRuns();
    say("Run started. Reading data is paused until it finishes.", "good");
  });
  const rows = jobs.map((j) => el("tr", {}, el("td", {}, j.id), el("td", {}, statusTag(j.status)), el("td", {}, j.requested_by), el("td", {}, when(j.started_at)),
    el("td", {}, when(j.finished_at)), el("td", { class: "num" }, j.exit_code == null ? "" : String(j.exit_code))));
  show(page("Pipeline runs", "Each run reads every live source and rebuilds the matches, people and households.",
    [btn("Refresh", () => run(() => viewRuns())), can("runs.start") ? btn("Start a run", start, "primary") : null]),
  jobs.length ? table(["Run", "Status", "Started by", "Started", "Finished", ["Exit code"]], rows) : empty("No runs yet."));
  if (jobs.some((j) => j.status === "running" || j.status === "queued")) {
    timer = setInterval(() => run(() => viewRuns()), 5000);          // keeps the list current while a run is in progress
  }
}

$("login-form").addEventListener("submit", signIn);
$("signout").addEventListener("click", () => signOut(""));
