"use strict";
// Rules of a project: matching rules and golden-record rules. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- rules of a project --------------------------------------------------------------------------------------------------
// One page per kind of rule. It reads top to bottom: what is live now (in plain words), changes waiting to be made live, and the version
// history folded away. The editor opens only when someone presses "Change the rules".
const CONFIGS = [["decision", "Matching rules"], ["survivorship", "Golden record"], ["settings", "Technical settings"]];
const CONFIG_INTRO = {
  decision: "How the engine decides whether two records belong to the same person.",
  survivorship: "Which value a person's golden record keeps when their records disagree.",
  settings: "Technical settings of a run. Change them together with your administrator.",
};
const CONFIG_HEAD = { decision: ["What the matching rules in use do", "Change the matching rules"], survivorship: ["What the golden-record rules in use do", "Change the golden-record rules"],
  settings: ["The technical settings in use", "Change the technical settings"] };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
// the same content whatever the key order (a saved version comes back from the server in its own order)
const sorted = (x) => (Array.isArray(x) ? x.map(sorted) : x && typeof x === "object" ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, sorted(x[k])])) : x);
const sameJson = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
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
    can("config.activate") && (!second || v.approved_by.length) ? btn("Put in use", () => act(v, "activate", "Put version " + v.version + " in use", 0, "Note"), "small primary", "zap") : null,
    can("config.approve") ? btn("Decline", () => act(v, "decline", "Decline version " + v.version, 3, "Why is it declined?", true), "small danger", "x") : null,
    second && v.status === "proposed" && mine(v) ? tag("Yours: someone else approves it", "warn") : null,
  ];

  // ---- what is in use, and what waits. Matching and golden-record rules are never assumed: until a version is in use, runs cannot start.
  const canEdit = can("config.propose");
  const optional = name === "settings";
  const now = !second && can("config.activate");          // one-person project: what is saved is in use at once
  const what = name === "decision" ? "matching rules" : name === "survivorship" ? "golden-record rules" : "technical settings";
  const proposed = async (content, reason) => {
    reason = reason || (live ? "Changed the " : "Chose the ") + what;      // one-person project: nobody else reads the reason, it is not asked
    if (latest && startContent && sameJson(content, startContent)) {
      say(latest.status === "active" ? "Nothing changed: these " + what + " are already in use." : "These " + what + " are already saved and wait to be put in use.", "good");
      return;
    }
    let out;
    try { out = await api("POST", base + "/propose", { content, reason }); }
    catch (e) {
      if (!/identical/i.test(e.message)) throw e;
      say(live ? "Nothing changed: these " + what + " are already in use." : "These " + what + " are already saved and wait to be put in use.", "good");
      return;
    }
    if (now) await api("POST", base + "/versions/" + out.version + "/activate", { reason });
    await again(false);
    say("Saved as version " + out.version + (now ? " and in use: the project's next run uses it." : second ? ". Another person approves it, then it can be put in use."
      : ". Someone who can put rules in use does that next."), "good");
  };
  const form = canEdit && !optional;                       // matching and golden-record rules: always the form, never a separate view mode
  const status = el("div", { class: "rules-status" },
    live ? el("div", { class: "live-line on" }, el("strong", {}, second ? "In use: version " + live.version : "In use"),
      el("span", { class: "muted" }, (live.activated_by ? "put in use by " + live.activated_by + ", " : "") + when(live.activated_at)))
      : optional ? el("div", { class: "live-line" }, el("strong", {}, "Standard values"), el("span", { class: "muted" }, "nothing to choose unless your administrator asks"))
        : el("div", { class: "notice warn", role: "note" }, el("strong", {}, "Not chosen yet. "), "Runs cannot start until this project has " + what + " in use. " +
          (form ? "Below are the standard " + what + ": check them, change anything you want, and press " + (now ? "Use these rules" : "Send for approval") + "."
            : "Someone who manages the project's rules chooses them.")),
    pending.map((v) => el("div", { class: "pending-card" },
      el("div", { class: "grow" }, el("strong", {}, "Version " + v.version + (v.status === "approved" ? " is approved and waits to be put in use" : second ? " waits for a second person to approve it" : " waits to be put in use")),
        el("div", { class: "muted" }, "Proposed by " + v.proposed_by + ", " + when(v.proposed_at) + (v.reason ? ": “" + v.reason + "”" : ""))),
      el("div", { class: "row" }, decide(v)))),
    second ? el("p", { class: "hint" }, "Second approval is on: a saved change waits until someone else approves it, then it is put in use and the next run uses it.") : null);

  // ---- the body: a plain summary of the rules in use (or of the standard ones), or the editor
  const start = async () => (latest ? api("GET", base + "/versions/" + latest.version) : api("GET", base + "/default"));
  const shown = live ? api("GET", base + "/versions/" + live.version) : api("GET", base + "/default");
  const cancel = () => run(() => again(false));
  if (form) editing = true;
  const headText = editing ? CONFIG_HEAD[name][1] : live ? CONFIG_HEAD[name][0] : optional ? "The standard values" : "What the standard rules would do";
  const head = form ? el("p", { class: "hint" }, CONFIG_INTRO[name]) : el("div", { class: "rules-head" }, el("div", {}, el("h2", { class: "section" }, headText), el("p", { class: "hint" }, CONFIG_INTRO[name])),
    !editing && canEdit && (live || optional) ? btn(optional ? "Change the settings" : "Change the rules", () => run(() => again(true)), "primary", "edit") : null);
  const saving = { solo: now, live: !!live };
  let startContent = null;
  let body;
  if (editing && canEdit) {
    const startFrom = await start();
    startContent = startFrom.content;
    const after = now ? " Saving puts the new version in use." : " Nothing changes until the new version is approved and put in use.";
    const note = form ? null : latest ? el("div", { class: "notice", role: "note" }, "You are editing a copy of version " + latest.version +
      (latest.status === "active" ? " (in use)" : waiting(latest) ? " (waiting, not in use yet)" : "") + "." + after)
      : el("div", { class: "notice", role: "note" }, "You are starting from the standard " + (optional ? "values" : "rules") + "." + after);
    if (name === "decision") {
      const [cat, conns] = await Promise.all([api("GET", "/rules-catalogue"), api("GET", P(pid) + "/connections")]);
      const present = new Set(conns.flatMap((c) => Object.keys(c.fields || {})));
      body = [note, matchingEditor(startFrom.content, cat, proposed, null, { ...saving, present })];
    } else if (name === "survivorship") {
      const [guide, conns] = await Promise.all([api("GET", P(pid) + "/golden-record-guide"), api("GET", P(pid) + "/connections")]);
      const present = new Set(conns.flatMap((c) => Object.keys(c.fields || {})));
      body = [note, goldenForm(startFrom.content, guide, proposed, null, { ...saving, present })];
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
  const history = versions.length > 1 || second ? el("details", { class: "history" }, el("summary", {}, icon("clock"), "Earlier versions (" + versions.length + ")"),
    table(["Version", "Status", "Proposed by", "When", "Reason", "Approved by", ""], rows)) : null;

  const bodyEl = el("div", { class: "rules-body" }, body);
  if (editing && canEdit) {
    // leaving with changes not saved asks first: any edit in the form counts (the save bar's own fields and buttons do not)
    let dirty = false;
    const mark = (e) => { if (!e.target.closest(".save-bar") && (e.type !== "click" || e.target.closest("button"))) dirty = true; };
    for (const t of ["input", "change", "click"]) bodyEl.addEventListener(t, mark);
    leaveGuard = () => dirty && bodyEl.isConnected;
  }
  add(wrap, form ? head : null, declinedNotice(versions, live), status, result, form ? null : head, bodyEl, history);
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
// `opts.solo`: one-person project, saving puts the version in use and no reason is asked; `opts.live`: a version is in use already.
function saveBar(why, err, save, cancel, opts) {
  opts = opts || {};
  why.placeholder = "For example: stricter merging after the March audit";
  const text = opts.solo == null ? "Save as a new version" : opts.solo ? (opts.live ? "Save changes" : "Use these rules") : "Send for approval";
  return el("div", { class: "save-bar" }, opts.solo ? null : field("Why this change? (at least 10 characters; kept in the history)", why), err,
    el("div", { class: "row" }, btn(text, save, "primary", "check"), cancel ? btn("Cancel", cancel) : null));
}

// ---- matching rules in plain words ----------------------------------------------------------------------------------------
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
// ---- matching rules as columns that must agree ------------------------------------------------------------------------------
// Each column a rule can use: its name, the data fields behind it, and the ways two records can agree on it. Each way is a set of the
// engine's comparison levels, so a rule made here is an ordinary rule the engine runs.
const NAME_ALIKE = ["identical cleaned name", "same name sounds (any order)", "first+last sound equal"];
const NAME_SIMILAR = [...NAME_ALIKE, "initial + same surname", "one side first-name only, first names agree", "fuzzy full-name match (JW>=0.90)"];
const DOB_SAME = ["exact date", "exact date, but a 1-Jan default", "day and month swapped"];
const PIN_SAME = ["same PIN + house number + similar text", "same PIN + similar text", "same PIN"];
const MATCH_COLUMNS = {
  name: ["Name", ["full_name"], [["is exactly the same", ["identical cleaned name"]], ["sounds the same", NAME_ALIKE],
    ["is similar (spelling, initials, word order)", NAME_SIMILAR]]],
  dob: ["Date of birth", ["dob"], [["is exactly the same", ["exact date"]], ["is the same (1 January defaults and swapped day and month allowed)", DOB_SAME],
    ["has the same year", [...DOB_SAME, "same year (partial or differing day/month)"]]]],
  phone: ["Phone", ["mobile", "mobile_alt"], [["has the same main number", ["same primary number"]], ["shares any number", ["same primary number", "number matches an alternate number"]]]],
  email: ["Email", ["email"], [["is the same", ["same email"]]]],
  pan: ["PAN", ["pan"], [["is the same", ["same PAN"]]]],
  aadhaar: ["Aadhaar", ["aadhaar", "aadhaar_reference_key", "aadhaar_last4"], [["is the same", ["same Aadhaar token"]],
    ["is the same, or the last 4 digits agree", ["same Aadhaar token", "same last-4 (no full number on one side)"]]]],
  gov_id: ["Passport, licence or voter ID", ["passport_no", "dl_no", "voter_id"], [["is the same", ["same passport / licence / voter ID"]]]],
  customer_number: ["Customer number", ["customer_number"], [["is the same", ["same customer number"]]]],
  address: ["Address", ["address", "pincode", "city"], [["is the same house (PIN, house number and street)", ["same PIN + house number + similar text"]],
    ["has the same PIN and a similar street", PIN_SAME.slice(0, 2)], ["has the same PIN", PIN_SAME],
    ["is in the same city", [...PIN_SAME, "same city, PIN missing on one side", "same city or PIN area, different PINs"]]]],
  gender: ["Gender", ["gender"], [["is the same", ["same gender"]]]],
};
// Records are only compared when they share one of these, or two of name, date of birth and address (see the blocking rules)
const STRONG_COLUMNS = new Set(["pan", "aadhaar", "gov_id", "customer_number", "phone", "email"]);
const findable = (ps) => ps.some((p) => STRONG_COLUMNS.has(p.col)) || new Set(ps.map((p) => p.col).filter((c) => ["name", "dob", "address"].includes(c))).size >= 2;
// "Never the same when ... differ": columns where two different values mean two different people
const NEVER_COLUMNS = { pan: ["different PANs", "PANs"], aadhaar: ["different Aadhaar", "Aadhaar numbers"], gov_id: ["different document numbers", "passport, licence or voter IDs"],
  customer_number: ["different customer numbers", "customer numbers"], gender: ["different gender", "genders"] };
const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
// The columns of a rule the form can show (one condition, or all of several, each a column with one of its ways to agree); null for anything else.
function matchParts(when) {
  const parts = (when.all || [when]).map((c) => {
    const col = MATCH_COLUMNS[c.comparison];
    const levels = c.is ? [c.is] : c.in;
    if (!col || !levels || Object.keys(c).length !== 2) return null;
    const how = col[2].findIndex(([, ls]) => sameSet(ls, levels));
    return how < 0 ? null : { col: c.comparison, how };
  });
  return parts.length && parts.every(Boolean) ? parts : null;
}
const matchWhen = (ps) => {
  const conds = ps.map((p) => { const ls = MATCH_COLUMNS[p.col][2][p.how][1]; return ls.length === 1 ? { comparison: p.col, is: ls[0] } : { comparison: p.col, in: ls }; });
  return conds.length === 1 ? conds[0] : { all: conds };
};
const colWord = (k, first) => (first || k === "pan" || k === "aadhaar" ? MATCH_COLUMNS[k][0] : MATCH_COLUMNS[k][0].toLowerCase());
const matchWords = (ps) => ps.map((p, i) => colWord(p.col, !i) + " " + MATCH_COLUMNS[p.col][2][p.how][0]).join(", and ");
function ruleId(prefix, words, rules) {
  const slug = words.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "rule";
  let id = prefix + slug;
  for (let i = 2; rules.some((r) => r.id === id); i++) id = prefix + slug + "_" + i;
  return id;
}
// What happens to a pair no rule catches, in words
const leftoverWords = (c) => (c.use_score === false ? "Pairs no rule catches stay apart. The match score is not used."
  : "The match score decides the pairs no rule catches: merged from " + percent(c.auto_merge_at || 0.9) + ", sent to a person from " + percent(c.review_at || 0.1) + ", apart below that.");
function decisionWords(c) {
  const on = (t) => (c.rules || []).filter((r) => r.type === t && r.enabled !== false).length;
  return plural(on("auto_merge"), "rule says", "rules say") + " when records are the same, " + plural(on("force_review"), "rule asks", "rules ask") + " a person, " +
    plural(on("never_merge"), "rule keeps", "rules keep") + " records apart. " + leftoverWords(c);
}
// The order of the four steps on the page; the hints say which step wins when several apply.
const MATCH_STEPS = {
  auto_merge: ["The same when", "Any one rule is enough. In each rule, all the columns listed must agree."],
  force_review: ["Ask a person when", "Wins over step 1: a pair caught here is never merged automatically, even if a rule in step 1 fits too."],
  never_merge: ["Never the same when", "Wins over everything: the two records always stay apart."],
};
// Read-only: what the matching rules do (for people who can see the rules but not change them).
function matchingSummary(content, cat) {
  const rules = content.rules || [];
  const part = (type) => {
    const on = rules.filter((r) => r.type === type && r.enabled !== false);
    const off = rules.filter((r) => r.type === type && r.enabled === false);
    return [on.length ? el("div", { class: "rule-group" }, on.map((r) => ruleRow(r, cat))) : el("p", { class: "muted" }, "No rule."),
      off.length ? el("p", { class: "muted off-list" }, "Switched off: " + off.map(ruleTitle).join("; ") + ".") : null];
  };
  return el("div", { class: "matching-view" }, ["auto_merge", "force_review", "never_merge"].map((t, i) => step(i + 1, MATCH_STEPS[t][0], MATCH_STEPS[t][1], part(t))),
    step(4, "Everything else", null, el("p", {}, leftoverWords(content))));
}
// The editor: 1 the same when (columns that must agree), 2 ask a person when, 3 never the same when, 4 what happens to everything else.
function matchingEditor(content, cat, propose, cancel, opts) {
  opts = opts || {};
  const state = JSON.parse(JSON.stringify(content));
  state.rules = state.rules || [];
  const was = new Map(state.rules.map((r) => [r, JSON.stringify(r.when)]));
  const parts = new Map();
  for (const r of state.rules) if (r.type === "auto_merge" || r.type === "force_review") { const ps = matchParts(r.when); if (ps) parts.set(r, ps); }
  // the columns this project's data has (all of them while it has no data yet)
  const known = opts.present && opts.present.size;
  const inData = (k) => !known || MATCH_COLUMNS[k][1].some((f) => opts.present.has(f));
  const pool = Object.keys(MATCH_COLUMNS).filter(inData);
  const colName = (k) => MATCH_COLUMNS[k][0] + (inData(k) ? "" : " (not in your data)");
  const lists = { auto_merge: el("div", { class: "rule-group" }), force_review: el("div", { class: "rule-group" }), never_merge: el("div", { class: "rule-group" }) };
  const onOff = (r, box, text) => {
    const cb = el("input", { type: "checkbox", checked: r.enabled !== false, "aria-label": text + ": on" });
    cb.addEventListener("change", () => { r.enabled = cb.checked; box.classList.toggle("off", !cb.checked); });
    return cb;
  };
  const removeBtn = (r, text) => btn("Remove", () => { state.rules = state.rules.filter((x) => x !== r); parts.delete(r); draw(); }, "small ghost", "trash", { "aria-label": "Remove " + text });
  // a rule made of columns: one line per column, "and" between them
  const card = (r, n, type) => {
    const ps = parts.get(r);
    const name = (type === "auto_merge" ? "Rule " : "Ask rule ") + n;
    const box = el("div", { class: "match-card" + (r.enabled === false ? " off" : "") });
    const rows = el("div", { class: "match-conds" });
    const warn = el("p", { class: "warn-line" });
    const check = () => {
      warn.textContent = findable(ps) ? "" : "On its own this finds nothing: records are only compared when they share an identifier, a phone or an email, " +
        "or two of name, date of birth and address. Add one of these.";
      warn.hidden = !warn.textContent;
    };
    const changed = () => { r.when = matchWhen(ps); r.reason = matchWords(ps); check(); };
    const drawRows = () => fill(rows, ps.map((p, i) => {
      const col = el("select", { "aria-label": name + ": column " + (i + 1) }, [...new Set([...pool, p.col])].map((k) => el("option", { value: k, selected: k === p.col }, colName(k))));
      const how = el("select", { "aria-label": name + ": how " + MATCH_COLUMNS[p.col][0] + " must agree" },
        MATCH_COLUMNS[p.col][2].map(([t], j) => el("option", { value: String(j), selected: j === p.how }, t)));
      col.addEventListener("change", () => { p.col = col.value; p.how = 0; changed(); drawRows(); });
      how.addEventListener("change", () => { p.how = Number(how.value); changed(); });
      return el("div", { class: "match-cond" }, el("span", { class: "and muted" }, i ? "and" : ""), col, how,
        ps.length > 1 ? btn("", () => { ps.splice(i, 1); changed(); drawRows(); }, "small ghost", "x", { "aria-label": "Remove " + MATCH_COLUMNS[p.col][0] + " from " + name }) : null);
    }));
    const more = btn("Column", () => { ps.push({ col: pool.find((k) => !ps.some((p) => p.col === k)) || pool[0], how: 0 }); changed(); drawRows(); }, "small", "plus",
      { "aria-label": "Add a column to " + name });
    check();
    drawRows();
    add(box, el("label", { class: "match-name" }, onOff(r, box, name), el("strong", {}, name)), rows, more, removeBtn(r, name), warn);
    return box;
  };
  // a rule the form cannot show as columns (a built-in safety rule with exceptions, or an expert's rule): on or off, its details, remove
  const fixed = (r) => {
    const row = ruleRow(r, cat, null, removeBtn(r, ruleTitle(r)));
    row.prepend(onOff(r, row, ruleTitle(r)));
    return row;
  };
  const EMPTY = { auto_merge: "No rule yet: nothing is merged.", force_review: "No rule: nothing is sent to a person by a rule.", never_merge: "No rule." };
  const draw = () => {
    for (const type of Object.keys(lists)) {
      let n = 0;
      const rs = state.rules.filter((r) => r.type === type);
      fill(lists[type], rs.length ? rs.map((r) => (parts.has(r) ? card(r, ++n, type) : fixed(r))) : el("p", { class: "muted" }, EMPTY[type]));
    }
    fill(evidence, state.rules.filter((r) => r.type === "not_enough_evidence").map(fixed));
  };
  const addRule = (type) => {
    const ps = pool.includes("name") && pool.includes("dob") ? [{ col: "name", how: 2 }, { col: "dob", how: 1 }] : [{ col: pool[0], how: 0 }];
    const words = matchWords(ps);
    const r = { id: ruleId(type === "auto_merge" ? "match_" : "ask_", words, state.rules), type, reason: words, when: matchWhen(ps) };
    state.rules.push(r);
    parts.set(r, ps);
    draw();
    const cards = lists[type].querySelectorAll(".match-card");
    if (cards.length) cards[cards.length - 1].querySelector("select").focus();
  };
  // step 3: add "never the same when <column> differ"
  const neverCols = Object.keys(NEVER_COLUMNS).filter(inData);
  const neverSel = el("select", { "aria-label": "Column" }, neverCols.map((k) => el("option", { value: k }, MATCH_COLUMNS[k][0])));
  const addNever = () => {
    const k = neverSel.value;
    state.rules.push({ id: ruleId("never_", k, state.rules), type: "never_merge", reason: "Two different " + NEVER_COLUMNS[k][1] + ": never the same person",
      when: { comparison: k, is: NEVER_COLUMNS[k][0] } });
    draw();
  };
  // step 4: everything else
  const keep = el("input", { type: "radio", name: "leftover", value: "keep", checked: state.use_score === false });
  const byScore = el("input", { type: "radio", name: "leftover", value: "score", checked: state.use_score !== false });
  const pct = (x, d) => String(Math.round((x || d) * 100));
  const reviewAt = el("input", { type: "number", min: "1", max: "99", step: "1", value: pct(state.review_at, 0.1), class: "pct" });
  const autoAt = el("input", { type: "number", min: "1", max: "100", step: "1", value: pct(state.auto_merge_at, 0.9), class: "pct" });
  const scale = scoreScale(Number(reviewAt.value) / 100, Number(autoAt.value) / 100);
  const redraw = () => { const r = Number(reviewAt.value) / 100; const a = Number(autoAt.value) / 100; if (r > 0 && r < a && a <= 1) scale.set(r, a); };
  reviewAt.addEventListener("input", redraw);
  autoAt.addEventListener("input", redraw);
  const evidence = el("div", { class: "rule-group" });
  const scoreBox = el("div", { class: "score-box" }, el("div", { class: "fields" }, field("Merge automatically from (%)", autoAt), field("Ask a person from (%)", reviewAt)),
    scale.node, evidence);
  const showScore = () => { scoreBox.hidden = !byScore.checked; };
  keep.addEventListener("change", showScore);
  byScore.addEventListener("change", showScore);
  showScore();
  draw();
  // for experts: a rule with other conditions, and the whole content as JSON
  const expertRule = async () => {
    const rule = await ruleBuilder(cat, state.rules);
    if (!rule) return;
    state.rules.push(rule);
    draw();
    say("Rule added. Save the rules to keep it.", "good");
  };
  const editor = el("textarea", { spellcheck: "false", rows: "14", class: "code" });
  const json = el("details", { class: "advanced" }, el("summary", {}, "For experts: other conditions, or the rules as JSON"),
    el("div", { class: "row" }, btn("Add a rule with other conditions", expertRule, "small", "plus")),
    el("p", { class: "hint" }, "Saving while this is open uses the JSON below as it is, not the choices above."), field("Rules (JSON)", editor));
  const current = () => ({ ...state, use_score: byScore.checked, auto_merge_at: Number(autoAt.value) / 100, review_at: Number(reviewAt.value) / 100 });
  json.addEventListener("toggle", () => { if (json.open) editor.value = JSON.stringify(current(), null, 2); });
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  const save = async () => {
    fail(err, "");
    let out;
    if (json.open) {
      try { out = JSON.parse(editor.value); } catch (_) { fail(err, "That is not valid JSON.", editor); return; }
    } else {
      out = current();
      if (byScore.checked && !(out.review_at > 0 && out.review_at < out.auto_merge_at && out.auto_merge_at <= 1)) {
        fail(err, "'Ask a person' must start above 0% and below 'Merge automatically', which is at most 100%.", reviewAt);
        return;
      }
      if (!byScore.checked) Object.assign(out, { auto_merge_at: content.auto_merge_at || 0.9, review_at: content.review_at || 0.1 });
      for (const [r, ps] of parts) {
        if (!state.rules.includes(r) || r.enabled === false || findable(ps)) continue;
        fail(err, "The rule '" + r.reason + "' finds nothing on its own. Add an identifier, a phone or an email to it, or two of name, date of birth and address.");
        return;
      }
      // a built-in rule whose columns were changed gets its own id, so its old name is not shown for it any more
      for (const [r] of parts) if (RULE_TITLE[r.id] && JSON.stringify(r.when) !== was.get(r)) r.id = ruleId(r.type === "auto_merge" ? "match_" : "ask_", r.reason, state.rules);
      out.rules = state.rules;
    }
    try { await propose(out, why.value); } catch (e) { fail(err, e.message); }
  };
  return el("div", { class: "matching-view editing" },
    step(1, MATCH_STEPS.auto_merge[0], MATCH_STEPS.auto_merge[1], lists.auto_merge, el("div", { class: "row" }, btn("Add a rule", () => addRule("auto_merge"), "", "plus"))),
    step(2, MATCH_STEPS.force_review[0], MATCH_STEPS.force_review[1], lists.force_review, el("div", { class: "row" }, btn("Add an ask rule", () => addRule("force_review"), "", "plus"))),
    step(3, MATCH_STEPS.never_merge[0], MATCH_STEPS.never_merge[1], lists.never_merge,
      neverCols.length ? el("div", { class: "row" }, el("span", {}, "Never the same when"), neverSel, el("span", {}, "differs"), btn("Add", addNever, "small", "plus")) : null),
    step(4, "Everything else", "Pairs that share something but that no rule above catches.",
      el("label", { class: "check" }, keep, el("span", {}, "Keep them apart", el("span", { class: "muted" }, "Only your rules decide. Easy to predict, but a duplicate your rules do not describe is missed."))),
      el("label", { class: "check" }, byScore, el("span", {}, "Let the match score decide", el("span", { class: "muted" }, "Every pair gets a score from all the columns the two records share. Finds more, but is harder to predict."))),
      scoreBox),
    json, saveBar(why, err, save, cancel, opts));
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
function goldenForm(content, guide, propose, cancel, opts) {
  const sourceIds = guide.sources.map((s) => s.id);
  // start from the configured order, then the order the latest run actually used, then anything new
  const seen = ((guide.last_run || {}).trust_order || []).filter((s) => sourceIds.includes(s));
  let order = [...new Set([...(content.source_trust || []).filter((s) => sourceIds.includes(s)), ...seen, ...sourceIds])];
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
  auto.addEventListener("change", () => { drawOrder(); list.hidden = auto.checked; });
  drawOrder();
  list.hidden = auto.checked;
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

  // one row per column (or per group of columns that must come from the same record), each with its own explicit choice
  const groups = content.groups || {};
  const grouped = new Set(Object.values(groups).flat());
  const def = content.default_strategy || "most_recent";
  const known = opts && opts.present && opts.present.size;
  const has = (f) => !known || opts.present.has(f) || (f === "aadhaar_token" && (opts.present.has("aadhaar") || opts.present.has("aadhaar_reference_key")));
  const units = [...Object.entries(groups).filter(([, m]) => m.some(has)).map(([g, m]) => [g, g === "address_block" ? "Address, city, state and PIN code (kept together)" : label(g) + " (" + m.map(fieldTitle).join(", ") + ", kept together)"]),
    ...guide.fields.filter((f) => !grouped.has(f) && has(f)).map((f) => [f, fieldTitle(f)])];
  const ruleSel = {};
  const preferSel = {};
  const fieldRows = units.map(([u, text]) => {
    const current = (content.fields || {})[u] || def;
    ruleSel[u] = el("select", { "aria-label": "Keep for " + text }, GOLDEN_STRATEGIES.map(([k, t]) => el("option", { value: k, selected: k === current }, t)));
    const pref = ((content.field_trust || {})[u] || [])[0] || "";
    preferSel[u] = el("select", { "aria-label": "Preferred source for " + text }, el("option", { value: "" }, "The trust order below"),
      sourceIds.map((s) => el("option", { value: s, selected: s === pref }, s)));
    return el("tr", {}, el("td", {}, text), el("td", {}, ruleSel[u]), el("td", {}, preferSel[u]));
  });
  const allSel = el("select", { "aria-label": "Keep for every column" }, GOLDEN_STRATEGIES.map(([k, t]) => el("option", { value: k }, t)));
  const setAll = () => { for (const sel of Object.values(ruleSel)) sel.value = allSel.value; };
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
    const fields = { ...(content.fields || {}) };
    const fieldTrust = { ...(content.field_trust || {}) };
    for (const [u] of units) {
      if (ruleSel[u].value === def) delete fields[u]; else fields[u] = ruleSel[u].value;
      if (preferSel[u].value) fieldTrust[u] = [preferSel[u].value]; else delete fieldTrust[u];
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
    step(1, "For each column, keep the value from", "When one person's records disagree on a column, this decides which value goes into the golden record.",
      undated, units.length ? table(["Column", "Keep the value from", "Preferred source"], fieldRows) : el("p", { class: "muted" }, "This project has no data in use yet."),
      el("div", { class: "row" }, el("span", {}, "Set every column to"), allSel, btn("Apply", setAll, "small")),
      el("details", {}, el("summary", {}, "What the choices mean"), el("dl", { class: "help" }, GOLDEN_STRATEGIES.map(([k, tt]) => [el("dt", {}, tt), el("dd", {}, guide.strategies[k] || "")]).flat()))),
    step(2, "Which source to trust most", "Used by 'Most trusted source', and to break ties.",
      el("label", { class: "check" }, auto, el("span", {}, "Decide automatically: the source whose records are most complete comes first. Untick to set the order yourself.")),
      sourceIds.length ? list : el("p", { class: "muted" }, "This project has no data in use yet."), lastNote),
    el("details", { class: "advanced" }, el("summary", {}, "More choices: record dates, your own columns, shared phones and emails"),
      el("h3", { class: "section" }, "Record dates"), el("p", { class: "hint" }, "'Newest record' needs a date per record: when it was created or last updated."),
      guide.sources.length ? table(["Source", "Record date"], dateRows) : null,
      el("h3", { class: "section" }, "Your own columns"),
      el("label", { class: "check" }, carry, el("span", {}, "Carry the sources' extra columns into the golden record (columns with the same name in different sources become one field)")),
      extraRows.length ? table(["Column", "Golden-record column", "Sources", "Rule"], extraRows) : el("p", { class: "hint" }, "No connection keeps extra columns."),
      el("h3", { class: "section" }, "Shared phones and emails"),
      field("A phone or email shared by more than this many people (an office line, an agent) is used only when nothing else exists", shared)),
    saveBar(why, err, save, cancel, opts));
}
