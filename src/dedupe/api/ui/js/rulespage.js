"use strict";
// The Rules step on one page: how careful merging is (ready-made settings), the matching rules as cards in plain words, how the golden record
// is built, and ONE save for both rule sets (two versions, each approved and put in use as before). After a run, the page tries the rules as
// they are being edited on the latest run's pairs and says what would change. Fields and search stay a page of their own (Advanced).
// Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
const RULE_SETS = [["decision", "Matching rules", "matching rules"], ["survivorship", "Golden record", "golden-record rules"]];
async function rulesPage(p, focus) {
  const pid = p.id;
  const second = p.second_approval;
  const now = !second && can("config.activate");             // one-person project: what is saved is in use at once
  const canEdit = can("config.propose");
  const wrap = el("div", { class: "rules-page" });
  const again = async () => { leaveGuard = null; await go(() => viewProject(pid, "setup", "matching")); };      // the step bar follows too
  const result = el("div", { class: "rules-result" });
  const sets = {};
  for (const [name] of RULE_SETS) {
    const versions = await api("GET", P(pid) + "/config/" + name);
    const live = versions.find((v) => v.status === "active");
    const pending = versions.filter((v) => v.status === "proposed" || v.status === "approved").reverse();
    sets[name] = { versions, live, pending, latest: pending[0] || live, tools: versionTools(p, name, live, result, again) };
  }
  const startOf = (name) => (sets[name].latest ? api("GET", P(pid) + "/config/" + name + "/versions/" + sets[name].latest.version)
    : api("GET", P(pid) + "/config/" + name + "/default"));
  const [decStart, goldStart, cat, guide, conns, runs] = await Promise.all([startOf("decision"), startOf("survivorship"), api("GET", P(pid) + "/rules-catalogue"),
    api("GET", P(pid) + "/golden-record-guide"), api("GET", P(pid) + "/connections"), can("runs.read") ? api("GET", P(pid) + "/runs") : []]);
  const ownKeys = useOwnFields(cat);
  const present = new Set([...conns.flatMap((c) => Object.keys(c.fields || {})), ...ownKeys]);

  // ---- what is in use, what waits, what was declined
  const missing = RULE_SETS.filter(([n]) => !sets[n].live);
  const status = el("div", { class: "rules-status" },
    RULE_SETS.map(([n]) => declinedNotice(sets[n].versions, sets[n].live)),
    missing.length ? el("div", { class: "notice warn", role: "note" }, el("strong", {}, "Not chosen yet. "), "Runs cannot start until this project's rules are in use. " +
      (canEdit ? "Below are the standard rules: check them, change anything you want, and press " + (now ? "Use these rules" : "Send for approval") + "."
        : "Someone who manages the project's rules chooses them."))
      : el("div", { class: "live-line on" }, el("strong", {}, "In use"), el("span", { class: "muted" }, RULE_SETS.map(([n, t]) => t + (second ? " version " + sets[n].live.version : "") +
        ", put in use " + (sets[n].live.activated_by ? "by " + sets[n].live.activated_by + " " : "") + when(sets[n].live.activated_at)).join("; "))),
    RULE_SETS.flatMap(([n, t]) => sets[n].pending.map((v) => el("div", { class: "pending-card" },
      el("div", { class: "grow" }, el("strong", {}, t + " version " + v.version + (v.status === "approved" ? " is approved and waits to be put in use"
        : second ? " waits for a second person to approve it" : " waits to be put in use")),
      el("div", { class: "muted" }, "Proposed by " + v.proposed_by + ", " + when(v.proposed_at) + (v.reason ? ": “" + v.reason + "”" : ""))),
      el("div", { class: "row" }, sets[n].tools.decide(v))))),
    second ? el("p", { class: "hint" }, "Second approval is on: a saved change waits until someone else approves it, then it is put in use and the next run uses it.") : null);

  // ---- history of both rule sets, folded away
  const historyOf = (n, t) => {
    const s = sets[n];
    return s.versions.length ? [el("h3", { class: "section" }, t), table(["Version", "Status", "Proposed by", "When", "Reason", "Approved by", ""],
      s.versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version), el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by),
        el("td", {}, when(v.proposed_at)), el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")),
        el("td", { class: "actions-cell" }, btn("View", () => s.tools.view(v), "small"),
          can("config.activate") && v.status === "superseded" && (v.reviewed || !second) ? btn("Go back to this", () => s.tools.back(v), "small", "undo") : null))))] : [];
  };
  const count = RULE_SETS.reduce((a, [n]) => a + sets[n].versions.length, 0);
  const history = count > 2 || second ? el("details", { class: "history" }, el("summary", {}, icon("clock"), "Earlier versions (" + count + ")"),
    RULE_SETS.map(([n, t]) => historyOf(n, t))) : null;
  const advanced = el("section", { class: "rules-advanced", "aria-label": "Advanced" }, el("h2", { class: "section" }, "Advanced"),
    el("p", { class: "hint" }, "Your own fields (company, product code, GSTIN ...), how two records are compared, and how the engine finds the pairs it compares."),
    btn("Fields and search", () => go(() => viewProject(pid, "setup", "technical")), "", "sliders"));

  // ---- read only: the rules in use (or the standard ones) in words
  if (!canEdit) {
    const shownOf = async (n) => (sets[n].live ? api("GET", P(pid) + "/config/" + n + "/versions/" + sets[n].live.version) : api("GET", P(pid) + "/config/" + n + "/default"));
    const [d, g] = await Promise.all([shownOf("decision"), shownOf("survivorship")]);
    add(wrap, status, result, el("h2", { class: "section" }, "Matching rules"), matchingSummary(d.content, cat),
      el("h2", { class: "section", id: "golden-rules" }, "Golden record"), goldenSummary(g.content, guide), history);
    if (focus === "golden") setTimeout(() => { const h = $("golden-rules"); if (h) h.scrollIntoView(); }, 0);
    return wrap;
  }

  // ---- the editor: both rule sets, one save
  const done = runs.find((r) => r.status === "succeeded");
  const effect = el("div", { class: "rules-effect", role: "status" });
  let tryTimer = null;
  const tryIt = () => {
    clearTimeout(tryTimer);
    if (!done) return;
    tryTimer = setTimeout(async () => {
      let content;
      try { content = matching.collect(); } catch (e) { fill(effect, el("p", { class: "muted" }, "Effect on the latest run: " + e.message)); return; }
      fill(effect, el("p", { class: "muted" }, "Trying the rules on the latest run..."));
      try {
        const r = await api("POST", P(pid) + "/config/decision/try", { content });
        const NAMES = { auto_merge: "merged automatically", review: "sent to a person", reject: "kept apart" };
        const diffs = Object.keys(NAMES).map((z) => [z, r.zones_after[z] - r.zones_before[z]]).filter(([, d]) => d);
        fill(effect, el("strong", {}, "Effect on the latest run (" + plural(r.pairs, "pair", "pairs") + " compared): "),
          el("span", {}, diffs.length ? diffs.map(([z, d]) => (d > 0 ? "+" : "") + num(d) + " " + NAMES[z]).join(", ") + "." : "no pair would be decided differently."),
          (r.rules_not_applied || []).length ? el("p", { class: "warn-line" }, "Not tried: " + r.rules_not_applied.map(label).join(", ") +
            ". The latest run did not compare the field these rules use. Run again to see their effect.") : null);
      } catch (e) { fill(effect, el("p", { class: "muted" }, "Effect on the latest run: " + e.message)); }
    }, 500);
  };
  let dirty = false;                                   // leaving with changes not saved asks first
  const matching = matchingEditor(decStart.content, cat, { present, pid, onChange: () => { dirty = true; tryIt(); } });
  const golden = goldenForm(goldStart.content, guide, null, null, { present: new Set(conns.flatMap((c) => Object.keys(c.fields || {}))), embedded: true, one: wordsOf(pid).one });
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  const starts = { decision: decStart.content, survivorship: goldStart.content };
  const save = async () => {
    fail(err, "");
    let contents;
    try { contents = { decision: matching.collect(), survivorship: golden.collect() }; } catch (e) { fail(err, e.message); return; }
    const reason = second ? why.value.trim() : "";
    // a rule set that changed, or that is not in use yet (the standard rules are saved as they are)
    const todo = RULE_SETS.filter(([n]) => !sets[n].latest || !sameJson(contents[n], starts[n]));
    if (!todo.length) {
      say(missing.length ? "These rules are already saved and wait to be put in use." : "Nothing changed: these rules are already in use.", "good");
      return;
    }
    if (second && reason.length < 10) { fail(err, "Say why in at least 10 characters: the person who approves it reads this.", why); return; }
    const saved = [];
    for (const [n, , words] of todo) {
      const text = reason || (sets[n].live ? "Changed the " : "Chose the ") + words;
      let out;
      try { out = await api("POST", P(pid) + "/config/" + n + "/propose", { content: contents[n], reason: text }); }
      catch (e) {
        if (/identical/i.test(e.message)) continue;
        fail(err, (saved.length ? "Saved the " + saved.join(" and ") + ", but not the " + words + ": " : "") + e.message);
        if (saved.length) await again();
        return;
      }
      if (now) await api("POST", P(pid) + "/config/" + n + "/versions/" + out.version + "/activate", { reason: text });
      saved.push(words + " (version " + out.version + ")");
    }
    await again();
    const ready = now && conns.some((c) => c.live_version);
    say("Saved the " + saved.join(" and ") + (now ? ". In use: the project's next run uses them." : second ? ". Another person approves them, then they can be put in use."
      : ". Someone who can put rules in use does that next."), "good",
    ready && can("runs.start") ? { label: done ? "Run again" : "Start the first run", fn: () => quickRun(pid), icon: "play" } : null);
  };
  const text = now ? (missing.length ? "Use these rules" : "Save changes") : "Send for approval";
  const saveBox = el("div", { class: "save-bar" }, second ? field("Why this change? (at least 10 characters; kept in the history)", why) : null, err,
    el("div", { class: "row" }, btn(text, save, "primary", "check")));
  why.placeholder = "For example: stricter merging after the March audit";
  const body = el("div", { class: "rules-body" },
    el("h2", { class: "section" }, "Matching rules"), el("p", { class: "hint" }, "When are two records the same " + wordsOf(pid).one + "?"),
    matching.node, done ? effect : null,
    el("h2", { class: "section", id: "golden-rules" }, "Golden record"), el("p", { class: "hint" }, "One clean record per " + wordsOf(pid).one +
      ": when the records disagree, which value it keeps."), golden.node,
    advanced, saveBox);
  // any edit in the form counts (the save bar's own fields do not)
  const mark = (e) => { if (!e.target.closest(".save-bar") && (e.type !== "click" || e.target.closest("button:not(.ghost)"))) dirty = true; };
  for (const t of ["input", "change"]) body.addEventListener(t, mark);
  leaveGuard = () => dirty && body.isConnected;
  add(wrap, status, result, body, history);
  if (done) tryIt();
  if (focus === "golden") setTimeout(() => { const h = $("golden-rules"); if (h) h.scrollIntoView(); }, 0);
  return wrap;
}
