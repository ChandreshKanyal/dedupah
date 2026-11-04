"use strict";
// Review: the inbox, comparing two people, one person, decisions. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
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

// The Review inbox is one work screen (the Task Manager layout of classic MDM tools): the project's open pairs in a work list on the left, the
// selected pair on the right with its decision buttons. Deciding moves to the next pair in place; Undo is offered in the message.
const REVIEW_STATUS = ["open", "unsure", "all", "decided"];
const pairName = (it) => (it.name_l || it.person_l) + " and " + (it.name_r || it.person_r);
const WHY_FILTER = [["any", "Any reason"], ["rule", "Sent by a rule"], ["score", "Its score only"]];
// How strongly a piece of evidence counts, in a word instead of the engine's bits
const strength = (bits) => (Math.abs(bits) >= 6 ? "strong" : Math.abs(bits) >= 2 ? "some weight" : "weak");
const comparisonTitle = (c) => (MATCH_COLUMNS[c] || [label(c)])[0];
async function viewInbox(pidArg, statusArg, ridArg) {
  const status = REVIEW_STATUS.includes(statusArg) ? statusArg : "open";
  const [rows] = await Promise.all([api("GET", "/review/inbox"), refreshCache()]);
  const usable = rows.filter((x) => x.run_id);
  const cur = usable.find((x) => x.project_id === pidArg) || [...usable].sort((a, b) => (b.open || 0) - (a.open || 0))[0];
  const finished = cur ? (await api("GET", P(cur.project_id) + "/runs")).filter((r) => r.status === "succeeded") : [];
  const newest = cur && cur.run_id;
  const chosenRun = finished.find((r) => r.id === ridArg) ? ridArg : newest;
  here(cur ? "/review/" + cur.project_id + (status !== "open" || chosenRun !== newest ? "/" + status : "") + (chosenRun !== newest ? "/" + chosenRun : "") : "/review");
  if (!cur) {
    show(page("Review", "Pairs that need a person appear here after a project's first finished run."),
      empty(rows.length ? "No project has a finished run yet. Pairs to check appear here after a project's first run." : "You are not in any project yet.",
        rows.length ? btn("Go to projects", link("/projects"), "primary", "folder") : null));
    return;
  }
  const pid = cur.project_id;
  const rid = chosenRun;
  const older = rid !== newest;
  const ctx = { pid, rid, status, band: "all", why: "any", q: "", offset: 0 };
  // why a rule sent a pair here, in words: the built-in rules have short titles, the project's own rules carry the name people gave them
  const live = can("config.read") ? (await api("GET", P(pid) + "/config/decision")).find((v) => v.status === "active") : null;
  const projectRules = live ? (await api("GET", P(pid) + "/config/decision/versions/" + live.version)).content.rules || [] : [];
  const ruleWords = (id) => { const r = projectRules.find((x) => x.id === id); return r ? ruleTitle(r) : RULE_TITLE[id] || label(id); };
  const filtered = () => ctx.band !== "all" || ctx.why !== "any" || !!ctx.q;
  // the filter bar: where the pairs come from (project, run) and which of them to list (score, why it is here, a name, id or city)
  const picker = el("select", {}, usable.map((x) => el("option", { value: x.project_id, selected: x.project_id === pid },
    x.project_name + (x.open == null ? "" : " (" + num(x.open) + ")"))));
  picker.addEventListener("change", () => go(() => viewInbox(picker.value, status)));
  const runPick = el("select", {}, finished.map((r) => el("option", { value: r.id, selected: r.id === rid }, "Run of " + shortWhen(r.started_at || r.created_at) + (r.id === newest ? " (newest)" : ""))));
  runPick.addEventListener("change", () => go(() => viewInbox(pid, status, runPick.value)));
  const band = el("select", {}, BANDS.map(([v, t]) => el("option", { value: v }, t)));
  const why = el("select", {}, WHY_FILTER.map(([v, t]) => el("option", { value: v }, t)));
  const text = el("input", { type: "search", maxlength: "64", placeholder: "for example Sharma or P000001411" });
  const apply = () => { ctx.band = band.value; ctx.why = why.value; ctx.q = text.value.trim(); return loadList(0, 0); };
  for (const x of [band, why]) x.addEventListener("change", () => run(apply));
  text.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(apply); } });
  text.setAttribute("aria-label", "Find a pair by name, person ID or city");
  text.placeholder = "Find a pair: name, person ID or city";
  text.classList.add("wide-search");
  const filters = el("div", { role: "search", "aria-label": "Filter the pairs" },
    el("div", { class: "row" }, usable.length > 1 ? picker : null, text, btn("Search", apply, "", "search"),
      btn("Clear", () => { band.value = "all"; why.value = "any"; text.value = ""; return apply(); }, "ghost")),
    el("details", { class: "advanced" }, el("summary", {}, "More filters: run, match score, why it is here"),
      el("div", { class: "filters" }, field("Run", runPick), field("Match score", band), field("Why it is here", why))));
  picker.setAttribute("aria-label", "Project");
  const actions = [
    cur.pending_approval ? btn("Waiting for a second person (" + num(cur.pending_approval) + ")", link("/approvals"), "", "check") : null,
    cur.waiting_for_run && can("runs.start") ? btn("Run again", () => quickRun(pid), "", "play") : null,
    btn("", () => go(() => viewInbox(pid, status, older ? rid : null)), "icon", "refresh", { "aria-label": "Refresh", title: "Refresh" })];
  let count = older ? null : { open: cur.open, unsure: cur.unsure }[status];                    // the inbox counts the newest run only
  const tabsBox = el("div");
  const drawTabs = () => fill(tabsBox, tabs(older ? [["open", "Open"], ["unsure", "Not sure"], ["all", "All pairs"], ["decided", "Decided"]]
    : [["open", "Open" + (cur.open == null ? "" : " (" + num(cur.open) + ")")], ["unsure", "Not sure (" + num(cur.unsure) + ")"], ["all", "All pairs"],
      ["decided", "Decided" + (cur.waiting_for_run ? " (" + num(cur.waiting_for_run) + " for the next run)" : "")]], status, (st) => go(() => viewInbox(pid, st, older ? rid : null))));
  // after each decision: the counts on the tabs and in the list are read again (a decided pair leaves Open at once)
  const recount = async () => {
    if (older) return;
    const now = (await api("GET", "/review/inbox")).find((x) => x.project_id === pid);
    if (now) { Object.assign(cur, now); count = { open: cur.open, unsure: cur.unsure }[status]; drawTabs(); }
  };
  drawTabs();
  const listPane = el("section", { class: "pane list-pane", "aria-label": "Pairs to review" });
  const detail = el("section", { class: "pane detail-pane", "aria-label": "Selected pair", "aria-live": "polite" });
  const writer = can("judgment.write");
  const chosen = new Set();
  let items = [];
  let at = 0;

  async function loadList(offset, select) {
    ctx.offset = Math.max(0, offset);
    items = await api("GET", queueUrl(ctx, QUEUE_PAGE, ctx.offset) + (ctx.why === "any" ? "" : "&forced=" + (ctx.why === "rule")) + (ctx.q ? "&q=" + encodeURIComponent(ctx.q) : ""));
    chosen.clear();
    at = Math.min(Math.max(select || 0, 0), Math.max(items.length - 1, 0));
    drawList();
    detail.hidden = !items.length;                           // with nothing listed, the list alone says so, across the whole width
    if (listPane.parentElement) listPane.parentElement.classList.toggle("no-pairs", !items.length);
    if (items.length) await openPair(at);
  }
  function drawList() {
    const selCount = el("span", { class: "sel-count" }, num(chosen.size) + " selected");
    const bulk = async (decision) => {
      if (!chosen.size) { say("Tick the pairs first.", "bad"); return; }
      const what = decision === "approve" ? "Same person" : "Different people";
      const why = await askReason(what + " for " + plural(chosen.size, "pair", "pairs"), "Why? It is recorded with every one of these decisions", decision === "approve" ? 10 : 0,
        "Decide " + plural(chosen.size, "pair", "pairs"));
      if (why === null) return;
      const out = await api("POST", R(pid, rid) + "/decisions/bulk", { decision, reason: why, items: [...chosen].map((it) => ({ person_l: it.person_l, person_r: it.person_r })) },
        { "Idempotency-Key": uuid() });
      await Promise.all([loadList(ctx.offset, at), recount()]);
      say(plural(out.done.length, "pair", "pairs") + " decided." + (out.failed.length ? " Not done: " + out.failed.map((f) => f.pair + " (" + f.error + ")").join("; ") : ""), out.failed.length ? "bad" : "good");
    };
    const rowsEl = items.map((it, i) => {
      const cb = writer && it.state !== "decided" ? el("input", { type: "checkbox", "aria-label": "Select " + pairName(it), checked: chosen.has(it) }) : null;
      if (cb) cb.addEventListener("change", () => { if (cb.checked) chosen.add(it); else chosen.delete(it); selCount.textContent = num(chosen.size) + " selected"; });
      return el("div", { class: "wl-item" + (i === at ? " on" : "") }, cb || el("span"),
        el("button", { type: "button", class: "wl-open", "aria-current": i === at ? "true" : null, onclick: () => run(() => openPair(i)) },
          el("span", { class: "wl-title" }, pairName(it)), el("span", { class: "wl-score" }, it.forced ? "Rule" : pct(it.top_probability)),
          el("span", { class: "wl-meta" }, it.size_l + " + " + it.size_r + " records", it.city_l ? " · " + it.city_l : "", it.forced ? " · sent by a rule" : "",
            it.state !== "open" ? [" · ", statusTag(it.state)] : null)));
    });
    const last = ctx.offset + items.length;
    const some = items.length > 0;                      // an empty list has nothing to tick or to page through
    fill(listPane,
      some && writer ? el("div", { class: "pane-tool" }, selCount,
        writer ? btn("Same person", () => bulk("approve"), "small", null, { title: "Same person for the ticked pairs" }) : null,
        writer ? btn("Different people", () => bulk("reject"), "small", null, { title: "Different people for the ticked pairs" }) : null,
        el("span", { class: "grow" })) : null,
      items.length ? el("div", { class: "wl" }, rowsEl) : el("div", { class: "pane-empty" }, filtered() ? "No pairs match these filters."
        : status === "open" ? "Nothing to review here: every pair of this run is decided." : status === "unsure" ? "No pair is marked Not sure." : "No pairs in this view."),
      some || ctx.offset ? el("div", { class: "pane-foot" },
        btn("", () => loadList(ctx.offset - QUEUE_PAGE, 0), "small icon", "prev", { "aria-label": "Previous page", disabled: ctx.offset === 0 }),
        btn("", () => loadList(ctx.offset + QUEUE_PAGE, 0), "small icon", "next", { "aria-label": "Next page", disabled: items.length < QUEUE_PAGE }),
        el("span", { class: "grow" }),
        items.length ? (ctx.offset + 1) + " - " + last + (count != null && !filtered() ? " of " + num(count) : "") : "") : null);
  }

  async function openPair(i, reveal) {
    at = i;
    for (const [n, row] of [...listPane.querySelectorAll(".wl-item")].entries()) {
      row.classList.toggle("on", n === i);
      const b = row.querySelector(".wl-open");
      if (n === i) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
    }
    const it = items[i];
    const base = R(pid, rid);
    const c = await api("GET", base + "/people/" + it.person_l + "/compare/" + it.person_r + (reveal ? "?" + unmaskQuery(reveal) : ""));
    const [A, Bp] = c.persons;
    const note = el("input", { type: "text", maxlength: "300", placeholder: "Note (optional, kept with the decision)", "aria-label": "Note" });
    const conflict = c.hard_conflicts.length ? el("input", { type: "checkbox" }) : null;
    const keys = {};
    const decide = async (kind) => {
      if (kind === "approve" && conflict && !conflict.checked) { say("These two people have conflicting identifiers. Tick the confirmation box first, or decide Different people.", "bad"); conflict.focus(); return; }
      if (kind === "approve" && conflict && note.value.trim().length < 10) { say("Merging people with conflicting identifiers needs a note of at least 10 characters.", "bad"); note.focus(); return; }
      keys[kind] = keys[kind] || uuid();
      const body = { person_l: it.person_l, person_r: it.person_r, reason: note.value.trim() };
      if (kind === "approve" && conflict) body.confirm_id_conflict = true;
      const out = await api("POST", base + "/decisions/" + kind, body, { "Idempotency-Key": keys[kind] });
      const words = { approve: "Same person", reject: "Different people", unsure: "Not sure" }[kind];
      const msg = words + " recorded for " + pairName(it) + (out.pending_approval ? ". It waits for a second person." : ". It applies at the project's next run.");
      const stays = status === "all" || (status === "unsure" && kind === "unsure");
      await Promise.all([loadList(ctx.offset, stays ? i + 1 : i), recount()]);
      say(msg, "good", { label: "Undo", icon: "undo", fn: async () => {
        await api("POST", P(pid) + "/decisions/" + out.action_id + "/undo", { reason: "undone right after deciding" });
        await Promise.all([loadList(ctx.offset, i), recount()]);
        say("The decision on " + pairName(it) + " was undone.", "good");
      } });
    };
    const B = {
      same: writer ? btn("Same person", () => decide("approve"), "primary", "check", { "aria-keyshortcuts": "S", title: "S" }) : null,
      diff: writer ? btn("Different people", () => decide("reject"), "", "x", { "aria-keyshortcuts": "D", title: "D" }) : null,
      unsure: writer ? btn("Not sure", () => decide("unsure"), "", null, { "aria-keyshortcuts": "U", title: "U" }) : null,
      prev: btn("Previous", () => (i > 0 ? openPair(i - 1) : ctx.offset ? loadList(ctx.offset - QUEUE_PAGE, QUEUE_PAGE - 1) : null), "", "prev",
        { "aria-keyshortcuts": "P", title: "P", disabled: i === 0 && ctx.offset === 0 }),
      next: btn("Next", () => (i + 1 < items.length ? openPair(i + 1) : loadList(ctx.offset + QUEUE_PAGE, 0)), "", "next",
        { "aria-keyshortcuts": "N", title: "N", disabled: i + 1 >= items.length && items.length < QUEUE_PAGE }),
    };
    pageKeys = (e) => {
      const k = { s: B.same, d: B.diff, u: B.unsure, n: B.next, j: B.next, p: B.prev, k: B.prev }[e.key.toLowerCase()];
      if (k && !k.disabled) { e.preventDefault(); k.click(); }
    };
    const more = [
      can("pii.unmask") && !c.unmasked ? btn("Show identifiers", async () => { const why = await revealIdentifiers("Show identifiers"); if (why) await openPair(i, why); }) : null,
      btn("Open full page", () => { reviewCtx = { ...ctx, index: ctx.offset + i, l: it.person_l, r: it.person_r }; return go(() => viewCompare(pid, rid, it.person_l, it.person_r)); }, "", "external"),
    ];
    const both = [A, Bp].flatMap((x) => x.records.map((r) => el("tr", {}, el("td", { class: "mono" }, x.person_id), el("td", {}, r.source_id), el("td", { class: "mono" }, r.source_record_id),
      el("td", {}, r.name_as_written || ""), el("td", {}, r.dob || ""), el("td", {}, r.mobile || ""), el("td", {}, r.email || ""), el("td", {}, r.city || ""))));
    const evidence = (list) => (list.length ? el("ul", { class: "plain" }, list.map((x) => el("li", {}, comparisonTitle(x.comparison) + ": " + x.level + " (" + strength(x.bits) + ")")))
      : el("p", { class: "muted" }, "None."));
    const panes = {
      compare: () => [fieldDiff(A.golden, Bp.golden)],
      records: () => [table(["Person", "Source", "Record", "Name as written", "Date of birth", "Mobile", "Email", "City"], both)],
      evidence: () => [el("p", { class: "hint" }, "From the best-matching pair of records."),
        el("div", { class: "cols" }, el("div", { class: "panel" }, el("h3", {}, "For the same person"), el("div", { class: "body" }, evidence(c.why_match))),
          el("div", { class: "panel" }, el("h3", {}, "For different people"), el("div", { class: "body" }, evidence(c.why_not))))],
      history: () => [c.existing_judgments.length ? table(["Decision", "By", "Status", "Reason"], c.existing_judgments.map((j) => el("tr", {}, el("td", {}, KIND_LABEL[j.kind] || label(j.kind)),
        el("td", {}, j.actor), el("td", {}, statusTag(j.status === "active" ? "in_force" : j.status === "revoked" ? "undone" : j.status)), el("td", {}, j.reason))))
        : el("p", { class: "muted" }, "No earlier decisions on these records.")],
    };
    const subNames = [["compare", "Comparison"], ["records", "Records (" + (A.record_count + Bp.record_count) + ")"], ["evidence", "Evidence"],
      ["history", "History" + (c.existing_judgments.length ? " (" + c.existing_judgments.length + ")" : "")]];
    const subBody = el("div", { class: "pane-body", role: "tabpanel", id: "subpanel" });
    const subBar = el("div", { class: "subtabs", role: "tablist", "aria-label": "About this pair" });
    const pick = (key) => {
      for (const b of subBar.children) b.setAttribute("aria-selected", b.dataset.key === key ? "true" : "false");
      subBody.setAttribute("aria-labelledby", "sub-" + key);
      subBody.replaceChildren(...panes[key]().flat().filter(Boolean));
    };
    subNames.forEach(([key, text]) => subBar.append(el("button", { type: "button", role: "tab", id: "sub-" + key, "data-key": key, "aria-controls": "subpanel", onclick: () => pick(key) }, text)));
    const position = "pair " + num(ctx.offset + i + 1) + (count != null && !filtered() ? " of " + num(count) : "");
    detail.replaceChildren(...[                                            // filtered: replaceChildren would print an empty slot as "null"
      el("div", { class: "pane-head" }, el("div", { class: "pane-title" },
        el("h2", {}, pairName({ ...it, name_l: A.golden.full_name || it.name_l, name_r: Bp.golden.full_name || it.name_r }), el("span", { class: "muted" }, " (" + position + ")")), el("span", { class: "grow" }), B.prev, B.next),
        el("p", { class: "why" }, c.decided_by ? el("span", {}, el("strong", {}, "Why it is here: "), ruleWords(c.decided_by) + ".")
          : it.forced ? el("strong", {}, "Sent here by a rule.") : el("span", {}, el("strong", {}, "Why it is here: "), "the match score is " + pct(c.probability) + ", too unsure to decide alone.")),
        el("p", { class: "muted" }, A.person_id + " (" + plural(A.record_count, "record", "records") + ") and " + Bp.person_id + " (" + plural(Bp.record_count, "record", "records") + ")",
          c.unmasked ? " · Identifiers shown in full; this view is logged." : " · Identifiers masked.")),
      c.hard_conflicts.length ? el("div", { class: "notice bad", role: "note" }, el("strong", {}, "Conflicting identifiers: "), c.hard_conflicts.map(fieldTitle).join(", ") + ". These are probably two different people.",
        writer ? el("label", { class: "check" }, conflict, el("span", {}, "I confirm they are one person despite the conflicting identifiers (a note is required)")) : null) : null,
      el("div", { class: "pane-actions" }, writer ? [B.same, B.diff, B.unsure, note] : el("span", { class: "muted" }, "Your role can look but not decide."), el("span", { class: "grow" }), more),
      subBar, subBody].filter(Boolean));
    pick("compare");
  }

  const runAt = finished.find((r) => r.id === rid);
  const trail = crumbs(crumb(cur.project_name, P(pid)), crumb("Run of " + shortWhen(runAt ? runAt.started_at || runAt.created_at : null), R(pid, rid)));
  if (status === "decided") {
    const proj = cache.projects.find((x) => x.id === pid) || { id: pid, name: cur.project_name };
    show(page("Review", null, actions, trail), tabsBox, await decisionsPanel([proj], { project: true }));
    return;
  }
  show(page("Review", null, actions, trail), tabsBox,
    filters,
    older ? el("div", { class: "notice warn", role: "note" }, "This is an older run. Decisions made here still count, but the newest run may already show these people differently.",
      el("div", { class: "row" }, btn("Review the newest run", () => go(() => viewInbox(pid, status)), "small"))) : null,
    !older && cur.scoring_mode ? scoringNotice({ scoring: { mode: cur.scoring_mode } }) : null,
    el("div", { class: "work" }, listPane, detail));
  await loadList(0, 0);
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
    return el("tr", {}, el("th", { scope: "row" }, fieldTitle(k)), el("td", {}, x == null ? "" : String(x)), el("td", {}, y == null ? "" : String(y)), el("td", { class: "flag " + verdict[1] }, verdict[0]));
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
  const why = (list) => (list.length ? el("ul", { class: "plain" }, list.map((x) => el("li", {}, comparisonTitle(x.comparison) + ": " + x.level + " (" + strength(x.bits) + ")")))
    : el("p", { class: "muted" }, "None."));
  const stale = runInfo.latest_run_id && runInfo.latest_run_id !== rid ? el("div", { class: "notice warn", role: "note" },
    "This pair comes from an older run. Your decision still counts, but the newest run may already show these people differently.",
    el("div", { class: "row" }, btn("Review the newest run", () => go(() => viewInbox(pid)), "small"))) : null;
  show(page("Compare people", position + (c.decided_by ? "Why it is here: " + (RULE_TITLE[c.decided_by] || label(c.decided_by)) : "Match score " + pct(c.probability)) + ".", actions,
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Review", "/review/" + pid), l + " and " + r)),
  stale, scoringNotice(runInfo.summary),
  c.unmasked ? el("p", { class: "hint" }, "Identifiers are shown in full. This view is logged.") : el("p", { class: "hint" }, "Identifiers are masked. Masked values that look the same may still differ."),
  c.hard_conflicts.length ? el("div", { class: "notice bad", role: "note" }, el("strong", {}, "Conflicting identifiers: "), c.hard_conflicts.map(fieldTitle).join(", ") + ". These are probably two different people.") : null,
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
    const out = await dialog("Correct " + fieldTitle(fieldName), el("div", {}, el("p", { class: "hint" }, "The golden record of " + person + " will show this value from the project's next run on, whatever the sources say. Leave it empty to clear the field."),
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
  const golden = Object.entries(v.golden || {}).map(([k, val]) => [fieldTitle(k), el("span", { class: "title-row" }, String(val),
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
  show(page("Customer " + person, plural(v.record_count, "record", "records") + " from " + (v.sources || []).join(", ") + ".", actions,
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Run", R(pid, rid)), crumb("Customers", R(pid, rid) + "/customers"), person)),
  v.conflict_fields && v.conflict_fields.length ? el("div", { class: "notice warn", role: "note" }, "The sources disagree on: " + v.conflict_fields.map(fieldTitle).join(", ") + ". The golden record shows the best-supported value.") : null,
  el("h2", { class: "section" }, "Golden record"), golden.length ? kv(golden) : empty("No values."),
  Object.keys(v.extra || {}).length ? [el("h3", { class: "section" }, "Other columns from the sources"), kv(Object.entries(v.extra).map(([k, val]) => [label(k), String(val)]))] : null,
  el("h2", { class: "section" }, "Source records"),
  table([...(writer && v.records.length > 1 ? [""] : []), "Source", "Record", "Name as written", "Date of birth", "Mobile", "Email", "City"], rows),
  writer && v.records.length > 1 ? el("div", { class: "row" }, btn("Split the ticked records into a new person", split, "", "split"),
    el("span", { class: "muted" }, "Use this when records of two different people were merged.")) : null);
}

// ---- decisions: what was decided, what became of it, and undo -------------------------------------------------------------------
async function viewDecisions() {
  here("/review/decisions");      // reached from the Review screen (Decisions made); Back returns there
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
    el("td", {}, (KIND_LABEL[d.kind] || label(d.kind)) + (d.field ? ": " + fieldTitle(d.field) : "")), el("td", {}, who(d)), el("td", {}, d.yours ? tag("You", "info") : d.actor),
    el("td", {}, d.reason || el("span", { class: "muted" }, "none")), el("td", {}, statusTag(d.status)),
    el("td", { class: "actions-cell" }, can("judgment.write") && d.action_id && ["in_force", "waiting_for_run", "pending_approval"].includes(d.status) ? btn("Undo", () => undo(d), "small", "undo") : null)));
  add(box, el("div", { class: "row" }, el("label", { class: "check" }, mine, "Only my decisions"), field("Status", status)),
    lines.length ? table(["When", ...(opts.project ? [] : ["Project"]), "Decision", "People", "By", "Reason", "Status", ""], lines)
      : empty(state.mine || state.status ? "No decisions match." : "No decisions yet. They appear here as soon as someone decides a pair.",
        can("queue.read") ? btn("Open the review inbox", link("/review"), "primary", "inbox") : null));
  return box;
}
