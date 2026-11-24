"use strict";
// Rules of a project: matching rules and golden-record rules. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- rules of a project --------------------------------------------------------------------------------------------------
// rulesPanel: the Fields and search page (the matching and golden-record rules are one page, rulespage.js). It reads top to bottom: what is
// in use now (in plain words), changes waiting to be put in use, and the version history folded away; the editor opens on "Change the settings".
// Below it, the pieces the Rules page uses: version buttons, the matching rules as cards, the golden-record form.
const CONFIGS = [["decision", "Matching rules"], ["survivorship", "Golden record"], ["settings", "Fields and search"]];
const CONFIG_INTRO = {
  decision: "How the engine decides whether two records are the same.",
  survivorship: "Which value a person's golden record keeps when their records disagree.",
  settings: "What is compared beside the built-in customer fields, and how the engine finds the pairs it compares.",
};
const CONFIG_HEAD = { decision: ["What the matching rules in use do", "Change the matching rules"], survivorship: ["What the golden-record rules in use do", "Change the golden-record rules"],
  settings: ["The fields and search in use", "Change the fields and search"] };
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
  const { view, back, decide } = versionTools(p, name, live, result, again);

  // ---- what is in use, and what waits. Matching and golden-record rules are never assumed: until a version is in use, runs cannot start.
  const canEdit = can("config.propose");
  const optional = name === "settings";
  const now = !second && can("config.activate");          // one-person project: what is saved is in use at once
  const what = name === "decision" ? "matching rules" : name === "survivorship" ? "golden-record rules" : "fields and search settings";
  const proposed = async (content, reason) => {
    if (second && (reason || "").trim().length < 10) throw new Error("Say why in at least 10 characters: the person who approves it reads this.");   // U1-06
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
    {
      // the matching rules as they will be: removing a field they use asks first
      const decVs = (await api("GET", P(pid) + "/config/decision")).filter((v) => ["proposed", "approved", "active"].includes(v.status));
      const decision = decVs.length ? (await api("GET", P(pid) + "/config/decision/versions/" + decVs[decVs.length - 1].version)).content : null;
      body = [note, setupForm(startFrom.content, await api("GET", P(pid) + "/rules-catalogue"), proposed, cancel, { ...saving, pid, decision })];
    }
  } else {
    const content = (await shown).content;
    body = setupSummary(content, await api("GET", P(pid) + "/rules-catalogue"));
  }

  // ---- history, folded away
  const rows = versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version), el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by),
    el("td", {}, when(v.proposed_at)), el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")),
    el("td", { class: "actions-cell" }, btn("View", () => view(v), "small"),
      can("config.activate") && v.status === "superseded" && (v.reviewed || !second) ? btn("Go back to this", () => back(v), "small", "undo") : null)));
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

// The buttons of one rule set's versions: what changes, try it on the latest run (matching rules), approve, put in use, decline, go back.
// `result`: where a version's changes or try-out are shown; `again()`: draws the page again after an action.
function versionTools(p, name, live, result, again) {
  const pid = p.id;
  const base = P(pid) + "/config/" + name;
  const second = p.second_approval;
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
    fill(result, el("div", { class: "title-row" }, el("h3", { class: "section" }, "Try-out on the " + num(r.pairs) + " scored pairs of the latest run"), closeResult()),
      el("p", { class: "hint" }, "Nothing is changed: this shows how the pairs of the latest run would have been decided with this version."),
      table(["Decision", ["Now"], ["With this version"], ["Change"]], zones.map((z) => el("tr", {}, el("td", {}, { auto_merge: "Merged automatically", review: "Sent to a person", reject: "Kept apart" }[z]), el("td", { class: "num" }, num(r.zones_before[z])),
        el("td", { class: "num" }, num(r.zones_after[z])), el("td", { class: "num" }, (r.zones_after[z] - r.zones_before[z] > 0 ? "+" : "") + num(r.zones_after[z] - r.zones_before[z]))))),
      el("p", { class: "hint" }, Object.keys(r.moved).length ? "Moved: " + Object.entries(r.moved).map(([k, n]) => k + " " + num(n)).join(", ") : "No pair changes decision."),
      (r.rules_not_applied || []).length ? el("p", { class: "warn-line" }, "Not tried: " + r.rules_not_applied.map(label).join(", ") + ". The latest run did not compare the " +
        "field these rules use (it was added after that run). Run again to see their effect.") : null);
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

  return { view, back, decide };
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
  match_name_dob_contact: "Similar name, same birth date, and a phone, email or house agrees",
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
    if (node.missing) return t + " is missing on one or both sides";
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
  if (node.own_fields) return "one of your own fields agrees";
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
// The comparisons and own fields a rule's condition names that this project does not have: such a rule is skipped by every run.
function missingComparisons(node, cat) {
  const names = new Set((cat.comparisons || []).map((c) => c.name));
  const out = new Set();
  const own = (c) => typeof c === "string" && c.startsWith("x_") && !names.has(c);
  const walk = (n) => {
    if (!n || typeof n !== "object") return;
    if (n.comparison && !names.has(n.comparison)) out.add(n.comparison);
    if (own(n.field)) out.add(n.field);
    for (const c of Array.isArray(n.cross) ? n.cross : []) if (own(c)) out.add(c);
    for (const k of ["all", "any"]) (n[k] || []).forEach(walk);
    walk(n.not);
  };
  walk(node);
  return [...out];
}
function ruleRow(r, cat, control, extra) {
  const gone = missingComparisons(r.when, cat);
  return el("div", { class: "rule" + (r.enabled === false ? " off" : "") }, control || null,
    el("div", { class: "rule-text" }, el("strong", {}, ruleTitle(r)),
      gone.length && r.enabled !== false ? el("p", { class: "warn-line" }, "Not applied by runs: it uses " + gone.map((c) => label(c.replace(/^x_/, ""))).join(", ") +
        ", which the project does not compare. Add it as a field of your own (Fields and search) or remove the rule.") : null,
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
// "Never the same when ... differ": columns where two different values mean two different people
const NEVER_COLUMNS = { pan: ["different PANs", "PANs"], aadhaar: ["different Aadhaar", "Aadhaar numbers"], gov_id: ["different document numbers", "passport, licence or voter IDs"],
  customer_number: ["different customer numbers", "customer numbers"], gender: ["different gender", "genders"] };
// A project's own fields join the columns a rule can use (from the project's catalogue); returns the data-field keys they stand for.
function useOwnFields(cat) {
  for (const k of Object.keys(MATCH_COLUMNS)) if (k.startsWith("x_")) { delete MATCH_COLUMNS[k]; delete NEVER_COLUMNS[k]; STRONG_COLUMNS.delete(k); }
  for (const f of cat.own_fields || []) {
    MATCH_COLUMNS[f.comparison] = [f.title, ["own:" + f.name], f.ways.map((w) => [w.words, w.levels])];
    NEVER_COLUMNS[f.comparison] = [f.differs, f.title.toLowerCase() + " values"];
    if (f.finds_pairs) STRONG_COLUMNS.add(f.comparison);
  }
  return (cat.own_fields || []).map((f) => "own:" + f.name);
}
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
// Ready-made settings for the pairs no rule catches. Careful is how a project starts (its rules alone decide); Balanced and Catch more let
// the match score decide the rest. Measured on synthetic data (1,200 households, PROGRESS section 104): Careful found about 4 in 10
// duplicates, Balanced and Catch more about 9 in 10, all three with almost no wrong merges; hence Balanced is the one recommended.
const PRESETS = [
  ["careful", "Careful", "Only your rules merge; a pair no rule catches stays apart. On test data: about 4 in 10 duplicates found, almost never a wrong merge.", { use_score: false }],
  ["balanced", "Balanced (recommended)", "Your rules, then the match score: sure pairs merge, doubtful ones go to a person. On test data: about 9 in 10 found, about 1 wrong merge in 1,000.",
    { use_score: true, auto_merge_at: 0.9, review_at: 0.1 }],
  ["more", "Catch more", "Like Balanced with lower thresholds: merges more on its own and asks less. On test data: slightly more found, about 4 wrong merges in 1,000.",
    { use_score: true, auto_merge_at: 0.8, review_at: 0.05 }],
];
const presetOf = (c) => {
  const hit = PRESETS.find(([, , , v]) => (v.use_score === false ? c.use_score === false
    : c.use_score !== false && Math.abs((c.auto_merge_at || 0.9) - v.auto_merge_at) < 1e-6 && Math.abs((c.review_at || 0.1) - v.review_at) < 1e-6));
  return hit ? hit[0] : "custom";
};
// A side panel at the right of the screen (not modal): a title, a body, and buttons. Escape or Done closes it; focus goes back to `from`.
function sidePanel(title, body, buttons, from, onClose) {
  const old = $("side-panel");
  if (old) old.closeIt();
  const panel = el("aside", { id: "side-panel", class: "side-panel", role: "dialog", "aria-label": title });
  const close = () => { panel.remove(); document.removeEventListener("keydown", esc); if (onClose) onClose(); if (from && from.isConnected) from.focus(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  panel.closeIt = close;
  add(panel, el("div", { class: "title-row" }, el("h2", { class: "section" }, title), btn("", close, "small ghost icon", "x", { "aria-label": "Close" })), body,
    el("div", { class: "row" }, buttons(close)));
  document.addEventListener("keydown", esc);
  document.body.append(panel);
  const f = panel.querySelector("select, input, textarea, button.primary");
  if (f) f.focus();
  return close;
}
// The matching rules as cards in plain words: 1 how careful (the ready-made settings for what no rule catches), 2 merge when, 3 ask a person
// when, 4 never merge when. Each rule has an on/off switch; a rule made of columns opens a side panel to change them. Returns { node, collect }:
// collect() gives the content to save or throws an Error saying what to fix. `opts.present`: the data fields the project's data has;
// `opts.pid`; `opts.onChange()`: called after any change (the page tries the rules on the latest run).
function matchingEditor(content, cat, opts) {
  opts = opts || {};
  const changed = () => { if (opts.onChange) opts.onChange(); };
  const state = JSON.parse(JSON.stringify(content));
  state.rules = state.rules || [];
  const was = new Map(state.rules.map((r) => [r, JSON.stringify(r.when) + "|" + r.type]));
  const unchanged = (r) => was.get(r) === JSON.stringify(r.when) + "|" + r.type;
  const named = new Set();                                  // rules a person gave a name in the builder: their title is that name
  const known = opts.present && opts.present.size;
  const inData = (k) => !known || MATCH_COLUMNS[k][1].some((f) => opts.present.has(f));
  // columns the sources keep as they are: a rule may use one; it then becomes a field of the project's own (the engine has to compare it)
  let keptCols = (cat.kept_columns || []).map((c) => c.name);
  const keptGroup = () => (keptCols.length && opts.pid && can("config.propose") ? el("optgroup", { label: "Columns kept as they are (become a field when chosen)" },
    keptCols.map((c) => el("option", { value: "kept:" + c }, c))) : null);
  const useKept = async (column) => {
    const f = await newOwnField(opts.pid, column, [], { kept: true, kind: "code", why: "to use it in a matching rule" });
    if (!f) return null;
    const fresh = await api("GET", P(opts.pid) + "/rules-catalogue");
    useOwnFields(fresh);
    cat.own_fields = fresh.own_fields;
    cat.comparisons = fresh.comparisons;
    keptCols = (fresh.kept_columns || []).map((c) => c.name);
    if (opts.present) opts.present.add("own:" + f.name);
    say("The column " + column + " is now a field of your own (" + f.title + "): rules can use it." + (f.pending ? " The field waits for approval: until it is in use, " +
      "runs skip rules on it." : " Its options are under Fields and search."), "good");
    return { key: "x_" + f.name, kind: f.kind };
  };
  const lists = { auto_merge: el("div", { class: "rule-cards" }), force_review: el("div", { class: "rule-cards" }), never_merge: el("div", { class: "rule-cards" }) };
  const onOff = (r, box, text) => {
    const cb = el("input", { type: "checkbox", role: "switch", checked: r.enabled !== false, "aria-label": text + ": on" });
    const word = el("span", { class: "switch-word" }, r.enabled !== false ? "On" : "Off");
    cb.addEventListener("change", () => { r.enabled = cb.checked; box.classList.toggle("off", !cb.checked); word.textContent = cb.checked ? "On" : "Off"; changed(); });
    return el("label", { class: "switch" }, cb, word);
  };
  const remove = (r) => { state.rules = state.rules.filter((x) => x !== r); draw(); changed(); };
  // a rule's title: a built-in rule's own name while it is unchanged, the name a person gave it, else its condition in a few words
  // ("Name is similar, date of birth is the same, phone shares any number")
  const shortWords = (ps) => cap(ps.map((x, i) => colWord(x.col, !i) + " " + MATCH_COLUMNS[x.col][2][x.how][0].replace(/ \(.*\)$/, "")).join(", "));
  const autoTitle = (r) => { const ps = matchParts(r.when); return ps ? shortWords(ps) : cap(conditionWords(r.when, cat)); };
  const titleOf = (r) => (RULE_TITLE[r.id] && unchanged(r) ? RULE_TITLE[r.id] : named.has(r) || (r.reason && unchanged(r) && !matchParts(r.when)) ? r.reason : autoTitle(r));
  const findsNothing = "On its own this finds no new pairs: records are compared only when they share an identifier, a phone or an email, two of name, " +
    "date of birth and address, or one column is the same as another. It still applies to the pairs other rules find.";
  const builderCtx = { cat, present: opts.present, pid: opts.pid, sources: opts.sources || [], named, titleOf, autoTitle, keptCols: () => keptCols, useKept };
  const edit = (r, from) => openRuleBuilder(r, builderCtx, from, draw, changed, remove);
  const card = (r) => {
    const box = el("div", { class: "rule-card" + (r.enabled === false ? " off" : "") });
    const gone = missingComparisons(r.when, cat);
    const editBtn = btn("Edit", () => edit(r, editBtn), "small", "edit", { "aria-label": "Edit " + titleOf(r) });
    add(box, onOff(r, box, titleOf(r)), el("div", { class: "grow" }, el("strong", {}, titleOf(r)),
      (r.type === "auto_merge" || r.type === "force_review") && r.enabled !== false && !conditionFinds(r.when, cat) ? el("p", { class: "warn-line" }, findsNothing) : null,
      gone.length && r.enabled !== false ? el("p", { class: "warn-line" }, "Not applied by runs: it uses " + gone.map((c) => label(c.replace(/^x_/, ""))).join(", ") +
        ", which the project does not compare. Add it as a field of your own (Fields and search) or remove the rule.") : null,
      matchParts(r.when) && !RULE_TITLE[r.id] ? null : el("details", {}, el("summary", {}, "Details"), RULE_TITLE[r.id] && r.reason && unchanged(r) ? el("p", {}, r.reason) : null,
        el("p", { class: "muted" }, "Applies when " + conditionWords(r.when, cat) + "."))),
    editBtn);
    return box;
  };
  const EMPTY = { auto_merge: "No rule: only the match score merges records.", force_review: "No rule: only the match score sends pairs to a person.", never_merge: "No rule." };
  const draw = () => {
    for (const type of Object.keys(lists)) {
      const rs = state.rules.filter((r) => r.type === type || (type === "never_merge" && r.type === "not_enough_evidence"));
      fill(lists[type], rs.length ? rs.map(card) : el("p", { class: "muted" }, EMPTY[type]));
    }
  };
  // a new rule opens in the builder: merge and ask rules start from "name is similar and date of birth is the same", a never-merge rule
  // from the first column whose difference keeps records apart
  const addRule = (type) => {
    const pool = Object.keys(MATCH_COLUMNS).filter(inData);
    let when;
    if (type === "never_merge") {
      const k = Object.keys(NEVER_COLUMNS).find(inData) || pool[0];
      when = NEVER_COLUMNS[k] ? { comparison: k, is: NEVER_COLUMNS[k][0] } : { comparison: k, missing: true };
    } else {
      when = matchWhen(pool.includes("name") && pool.includes("dob") ? [{ col: "name", how: 2 }, { col: "dob", how: 1 }] : [{ col: pool[0], how: 0 }]);
    }
    const r = { id: ruleId(type === "auto_merge" ? "match_" : type === "force_review" ? "ask_" : "never_", conditionWords(when, cat), state.rules), type, reason: "", when };
    r.reason = autoTitle(r);
    was.set(r, "");                                          // new: never shown as a built-in rule
    state.rules.push(r);
    draw();
    changed();
    const b = [...lists[type].querySelectorAll(".rule-card button")].pop();
    edit(r, b);
  };
  // "never merge when <column> differs": one click
  const neverCols = () => Object.keys(NEVER_COLUMNS).filter(inData);
  const neverSel = el("select", { "aria-label": "Never merge when this differs" });
  const drawNever = () => fill(neverSel, neverCols().map((k) => el("option", { value: k }, MATCH_COLUMNS[k][0])), keptGroup());
  drawNever();
  const addNever = async () => {
    const k = neverSel.value.startsWith("kept:") ? ((await useKept(neverSel.value.slice(5))) || {}).key : neverSel.value;
    drawNever();
    if (!k) return;
    const r = { id: ruleId("never_", k, state.rules), type: "never_merge", reason: "Two different " + NEVER_COLUMNS[k][1] + ": never the same " + (k.startsWith("x_") ? "record" : "person"),
      when: { comparison: k, is: NEVER_COLUMNS[k][0] } };
    was.set(r, "");
    state.rules.push(r);
    draw();
    changed();
  };
  // how careful: the ready-made settings as choice cards; the thresholds themselves under "Adjust"
  const pctOf = (x, d) => String(Math.round((x || d) * 1000) / 10);         // to a tenth of a percent: 0.875 shows 87.5 and is saved back unchanged
  const reviewAt = el("input", { type: "number", min: "1", max: "99", step: "0.1", value: pctOf(state.review_at, 0.1), class: "pct" });
  const autoAt = el("input", { type: "number", min: "1", max: "100", step: "0.1", value: pctOf(state.auto_merge_at, 0.9), class: "pct" });
  const scale = scoreScale(Number(reviewAt.value) / 100, Number(autoAt.value) / 100);
  let useScore = state.use_score !== false;
  const share = (input) => Math.round(Number(input.value) * 10) / 1000;      // 87.3 -> 0.873 exactly, so an unchanged form stays unchanged
  const current = () => ({ ...state, use_score: useScore, auto_merge_at: share(autoAt), review_at: share(reviewAt) });
  const radios = {};
  const custom = el("label", { class: "choice-card" }, radios.custom = el("input", { type: "radio", name: "preset", value: "custom" }),
    el("span", {}, el("strong", {}, "Your own thresholds"), el("span", { class: "muted" }, "Set under Adjust the thresholds.")));
  const adjust = el("details", { class: "advanced" }, el("summary", {}, "Adjust the thresholds"),
    el("div", { class: "fields" }, field("Merge automatically from (%)", autoAt), field("Ask a person from (%)", reviewAt)), scale.node);
  const syncPreset = () => {
    const k = presetOf(current());
    for (const [key, r] of Object.entries(radios)) r.checked = key === k;
    custom.hidden = k !== "custom";
    adjust.hidden = !useScore;
  };
  const presetCards = el("div", { class: "choice-cards presets" }, PRESETS.map(([k, title, text, v]) => {
    radios[k] = el("input", { type: "radio", name: "preset", value: k });
    radios[k].addEventListener("change", () => {
      useScore = v.use_score !== false;
      if (useScore) { autoAt.value = pctOf(v.auto_merge_at); reviewAt.value = pctOf(v.review_at); scale.set(v.review_at, v.auto_merge_at); }
      syncPreset();
      changed();
    });
    return el("label", { class: "choice-card" }, radios[k], el("span", {}, el("strong", {}, title), el("span", { class: "muted" }, text)));
  }), custom);
  const onThreshold = () => {
    const r = Number(reviewAt.value) / 100; const a = Number(autoAt.value) / 100;
    if (r > 0 && r < a && a <= 1) scale.set(r, a);
    syncPreset();
    changed();
  };
  reviewAt.addEventListener("input", onThreshold);
  autoAt.addEventListener("input", onThreshold);
  syncPreset();
  draw();
  // for experts: the whole content as JSON
  const editor = el("textarea", { spellcheck: "false", rows: "14", class: "code" });
  const json = el("details", { class: "advanced" }, el("summary", {}, "For experts: the matching rules as JSON"),
    el("p", { class: "hint" }, "Saving while this is open uses the JSON below as it is, not the choices above."), field("Matching rules (JSON)", editor));
  json.addEventListener("toggle", () => { if (json.open) editor.value = JSON.stringify(current(), null, 2); });
  const collect = () => {
    if (json.open) {
      try { return JSON.parse(editor.value); } catch (_) { throw new Error("The matching rules JSON is not valid JSON."); }
    }
    const out = current();
    if (useScore && !(out.review_at > 0 && out.review_at < out.auto_merge_at && out.auto_merge_at <= 1)) {
      throw new Error("'Ask a person' must start above 0% and below 'Merge automatically', which is at most 100%.");
    }
    if (!useScore) Object.assign(out, { auto_merge_at: content.auto_merge_at || 0.9, review_at: content.review_at || 0.1 });
    for (const r of state.rules) {
      const t = condTree(r.when);
      const p = conditionProblem(t.leaf ? { g: "all", items: [t] } : t, cat);
      if (p) throw new Error("The rule “" + titleOf(r) + "” is not complete: " + p);
    }
    // a new rule, and a built-in rule that was changed, get an id from their title (a built-in rule's old name is not shown for it any more)
    for (const r of state.rules) {
      if (was.get(r) === "") r.id = ruleId({ auto_merge: "match_", force_review: "ask_", never_merge: "never_", not_enough_evidence: "evidence_" }[r.type],
        titleOf(r), state.rules.filter((x) => x !== r));
      else if (RULE_TITLE[r.id] && !unchanged(r)) {
        r.reason = titleOf(r);
        r.id = ruleId({ auto_merge: "match_", force_review: "ask_", never_merge: "never_", not_enough_evidence: "evidence_" }[r.type], r.reason, state.rules);
      }
    }
    out.rules = state.rules;
    return out;
  };
  const node = el("div", { class: "matching-view editing" },
    step(1, "How careful should merging be?", "For pairs no rule below catches: the match score compares every column two records share.", presetCards, adjust),
    step(2, "Merge when", "Any one rule is enough. Never-merge rules still win.", lists.auto_merge, el("div", { class: "row" }, btn("Add a merge rule", () => addRule("auto_merge"), "", "plus"))),
    step(3, "Ask a person when", "Wins over merging: a pair caught here is never merged automatically.", lists.force_review,
      el("div", { class: "row" }, btn("Add an ask rule", () => addRule("force_review"), "", "plus"))),
    step(4, "Never merge when", "Wins over everything: the two records always stay apart.", lists.never_merge,
      el("div", { class: "row" }, btn("Add a never-merge rule", () => addRule("never_merge"), "", "plus"),
        neverCols().length || keptCols.length ? el("span", { class: "row tight quick-never" }, el("span", { class: "muted" }, "or in one click: never merge when"), neverSel,
          el("span", { class: "muted" }, "differs"), btn("Add", addNever, "small", "plus")) : null)),
    json);
  return { node, collect };
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
  const units = [...Object.entries(groups).map(([g, m]) => [g, g === "address_block" ? "Address, city, state and PIN code (kept together)" : label(g) + " (" + m.map(fieldTitle).join(", ") + ", kept together)"]),
    ...guide.fields.filter((f) => !grouped.has(f)).map((f) => [f, fieldTitle(f)])];
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
      ". Map a record date on the data source, or pick a date column here.") : null;

  // one row per column (or per group of columns that must come from the same record), each with its own explicit choice
  const groups = content.groups || {};
  const grouped = new Set(Object.values(groups).flat());
  const def = content.default_strategy || "most_recent";
  const known = opts && opts.present && opts.present.size;
  const has = (f) => !known || opts.present.has(f) || (f === "aadhaar_token" && (opts.present.has("aadhaar") || opts.present.has("aadhaar_reference_key")));
  const units = [...Object.entries(groups).filter(([, m]) => m.some(has)).map(([g, m]) => [g, g === "address_block" ? "Address, city, state and PIN code (kept together)" : label(g) + " (" + m.map(fieldTitle).join(", ") + ", kept together)"]),
    ...guide.fields.filter((f) => !grouped.has(f) && has(f)).map((f) => [f, fieldTitle(f)])];
  // one choice for every column (the default), and the columns that are chosen differently (the exceptions), each with its own rule and
  // optionally a preferred source
  const ruleSel = {};
  const preferSel = {};
  const defSel = el("select", { "aria-label": "Usually keep the value from" }, GOLDEN_STRATEGIES.map(([k, t]) => el("option", { value: k, selected: k === def }, t)));
  const exceptions = new Set(units.map(([u]) => u).filter((u) => ((content.fields || {})[u] && content.fields[u] !== def) || ((content.field_trust || {})[u] || []).length));
  for (const [u, text] of units) {
    const current = (content.fields || {})[u] || def;
    ruleSel[u] = el("select", { "aria-label": "Keep for " + text }, GOLDEN_STRATEGIES.map(([k, t]) => el("option", { value: k, selected: k === current }, t)));
    const pref = ((content.field_trust || {})[u] || [])[0] || "";
    preferSel[u] = el("select", { "aria-label": "Preferred source for " + text }, el("option", { value: "" }, "The trust order below"),
      sourceIds.map((s) => el("option", { value: s, selected: s === pref }, s)));
    // a preferred source only wins under "Most trusted source" (otherwise it breaks ties): choosing one switches the rule to it
    preferSel[u].addEventListener("change", () => { if (preferSel[u].value) ruleSel[u].value = "most_trusted_source"; });
  }
  const exceptionBox = el("div");
  const addSel = el("select", { "aria-label": "Column to choose differently" });
  const drawExceptions = () => {
    const rows = units.filter(([u]) => exceptions.has(u)).map(([u, text]) => el("tr", {}, el("td", {}, text), el("td", {}, ruleSel[u]), el("td", {}, preferSel[u]),
      el("td", { class: "actions-cell" }, btn("", () => { exceptions.delete(u); preferSel[u].value = ""; drawExceptions(); }, "small ghost icon", "x",
        { "aria-label": "Use the usual choice for " + text }))));
    const rest = units.filter(([u]) => !exceptions.has(u));
    fill(addSel, rest.map(([u, text]) => el("option", { value: u }, text)));
    fill(exceptionBox, el("h3", { class: "section" }, "Exceptions", el("span", { class: "count-badge" }, String(rows.length))),
      rows.length ? table(["Column", "Keep the value from", "Preferred source", ""], rows) : el("p", { class: "muted" }, "None: every column uses the usual choice."),
      rest.length ? el("div", { class: "row" }, el("span", {}, "Choose differently for"), addSel, btn("Add", () => {
        const u = addSel.value;
        exceptions.add(u);
        ruleSel[u].value = defSel.value === "most_recent" ? "most_frequent" : "most_recent";
        drawExceptions();
        ruleSel[u].focus();
      }, "small", "plus")) : null);
  };
  drawExceptions();
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
  const collect = () => {
    const fields = { ...(content.fields || {}) };
    const fieldTrust = { ...(content.field_trust || {}) };
    const usual = defSel.value;
    for (const [u] of units) {
      if (!exceptions.has(u) || ruleSel[u].value === usual) delete fields[u]; else fields[u] = ruleSel[u].value;
      if (exceptions.has(u) && preferSel[u].value) fieldTrust[u] = [preferSel[u].value]; else delete fieldTrust[u];
    }
    const dates = {};
    for (const [s, sel] of Object.entries(dateSel)) if (sel.value) dates[s] = sel.value;
    const extraFields = {};
    const extraDef = content.extra_default_strategy || "most_recent";
    for (const [n, sel] of Object.entries(extraSel)) if (sel && sel.value !== extraDef) extraFields[n] = sel.value;
    return { ...content, default_strategy: usual, source_trust: auto.checked ? [] : order, source_dates: dates, fields, field_trust: fieldTrust, shared_value_limit: Number(shared.value),
      carry_extra_columns: carry.checked, extra_fields: extraFields };
  };
  const save = async () => {
    fail(err, "");
    try { await propose(collect(), why.value); } catch (e) { fail(err, e.message); }
  };
  const node = el("div", { class: "matching-view golden-form editing" },
    step(1, "Keep the value from", "When the records of one " + ((opts && opts.one) || "person") + " disagree on a column, this decides which value the golden record keeps.",
      undated, el("div", { class: "row" }, el("span", {}, "Usually:"), defSel), units.length ? exceptionBox : el("p", { class: "muted" }, "This project has no data in use yet."),
      el("details", {}, el("summary", {}, "What the choices mean"), el("dl", { class: "help" }, GOLDEN_STRATEGIES.map(([k, tt]) => [el("dt", {}, tt), el("dd", {}, guide.strategies[k] || "")]).flat()))),
    step(2, "Which source to trust most", "Used by 'Most trusted source', and to break ties.",
      el("label", { class: "check" }, auto, el("span", {}, "Decide automatically: the source whose records are most complete comes first. Untick to set the order yourself.")),
      sourceIds.length ? list : el("p", { class: "muted" }, "This project has no data in use yet."), lastNote),
    el("details", { class: "advanced" }, el("summary", {}, "More choices: record dates, your own columns, shared phones and emails"),
      el("h3", { class: "section" }, "Record dates"), el("p", { class: "hint" }, "'Newest record' needs a date per record: when it was created or last updated."),
      guide.sources.length ? table(["Source", "Record date"], dateRows) : null,
      el("h3", { class: "section" }, "Your own columns"),
      el("label", { class: "check" }, carry, el("span", {}, "Carry the sources' extra columns into the golden record (columns with the same name in different sources become one field)")),
      extraRows.length ? table(["Column", "Golden-record column", "Sources", "Rule"], extraRows) : el("p", { class: "hint" }, "No data source keeps extra columns."),
      el("h3", { class: "section" }, "Shared phones and emails"),
      field("A phone or email shared by more than this many people (an office line, an agent) is used only when nothing else exists", shared)),
    opts && opts.embedded ? null : saveBar(why, err, save, cancel, opts));
  return opts && opts.embedded ? { node, collect } : node;          // embedded: the Rules page saves it with the matching rules
}
