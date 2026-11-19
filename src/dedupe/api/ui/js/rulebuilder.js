"use strict";
// The rule builder: any matching rule as conditions in groups, without code. A group is "all of these", "any of these", "none of these" or
// "not all of these"; groups go inside groups. Each condition is one line in words: how two records compare on a column ("name is
// similar"), a column's values ("segment is one of Corporate, SME on both records", "amount within 5%", "opened within 30 days"), which
// data source a record came from, one record's column against ANOTHER column of the other record ("parent GSTIN of one is the GSTIN of
// the other"), the match score, or "one of your own fields agrees". What is built is the engine's own rule language (decision.conditions),
// so every built-in rule opens here too; a condition the builder has no line for stays as it was and shows as JSON.
// While a rule is being built, it is tried on the latest run: how many pairs it catches and how that run decided them.
// Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.

// columns whose VALUES a rule can check besides the project's own fields: [record column, title, kind, mapped fields that fill it]
const VALUE_COLUMNS = [["source_id", "Data source", "text", null], ["gender", "Gender", "text", ["gender"]], ["city", "City", "text", ["city"]],
  ["state", "State", "text", ["state"]], ["address_pin", "PIN code", "text", ["pincode"]], ["dob", "Date of birth", "date", ["dob"]],
  ["dob_year", "Birth year", "year", ["dob", "age"]], ["record_date", "Record date", "date", ["record_date"]],
  ["customer_number", "Customer number", "text", ["customer_number"]]];
// columns one record's value can be compared with ANOTHER column of the other record, besides the project's own fields
const CROSS_COLUMNS = [["name_clean", "Full name", ["full_name"]], ["mobile", "Mobile", ["mobile"]], ["mobile_alt", "Second phone", ["mobile_alt"]],
  ["email_key", "Email", ["email"]], ["customer_number", "Customer number", ["customer_number"]], ["pan", "PAN", ["pan"]],
  ["passport_no", "Passport number", ["passport_no"]], ["dl_no", "Driving licence", ["dl_no"]], ["voter_id", "Voter ID", ["voter_id"]],
  ["city", "City", ["city"]], ["address_pin", "PIN code", ["pincode"]]];
// what can be checked on a column's values: [op, words, what is typed, the kinds of column it fits (none: every kind)]
const VALUE_HOW = [
  ["same", "is the same on both records", "none"], ["differs", "differs between the records", "none"],
  ["both_in", "is one of these on both records", "list", ["text", "date"]], ["either_in", "is one of these on at least one record", "list", ["text", "date"]],
  ["neither_in", "is none of these on either record", "list", ["text", "date"]],
  ["both_contain", "contains this on both records", "text", ["text"]], ["either_contains", "contains this on at least one record", "text", ["text"]],
  ["within", "is within this amount", "number", ["number", "year"]], ["within_pct", "is within this percentage", "number", ["number"]],
  ["within_days", "is within this many days", "number", ["date"]], ["abs_diff_gt", "differs by more than", "number", ["number", "year"]],
  ["both_gte", "is at least this on both records", "number", ["number", "year"]], ["both_lte", "is at most this on both records", "number", ["number", "year"]],
  ["both_born_within_last", "both born in the last ... years", "number", ["year"]],
  ["either_empty", "is empty on at least one record", "none"], ["both_empty", "is empty on both records", "none"], ["both_filled", "is filled on both records", "none"]];
const SCORE_OPS = [[">=", "is at least"], [">", "is above"], ["<", "is below"], ["<=", "is at most"]];
const GROUPS = [["all", "All of these are true"], ["any", "Any of these is true"], ["none", "None of these is true"], ["notall", "Not all of these are true"]];
const RULE_TYPES_SAID = [["auto_merge", "Merge the two records"], ["force_review", "Ask a person"], ["never_merge", "Never merge them"],
  ["not_enough_evidence", "Neither merge nor ask (too little evidence)"]];

// ---- the engine's condition JSON <-> groups of lines ------------------------------------------------------------------------------
function condTree(n) {
  if (n && n.all) return { g: "all", items: n.all.map(condTree) };
  if (n && n.any) return { g: "any", items: n.any.map(condTree) };
  if (n && n.not) {
    const inner = n.not;
    if (inner.any) return { g: "none", items: inner.any.map(condTree) };
    if (inner.all) return { g: "notall", items: inner.all.map(condTree) };
    return { g: "none", items: [condTree(inner)] };
  }
  return { leaf: n };
}
const topTree = (when) => { const t = condTree(when); return t.leaf ? { g: "all", items: [t] } : t; };
function condJson(t) {
  if (t.leaf) return t.leaf;
  const items = t.items.map(condJson);
  if (t.g === "all") return items.length === 1 ? items[0] : { all: items };
  if (t.g === "any") return items.length === 1 ? items[0] : { any: items };
  const inner = items.length === 1 ? items[0] : { [t.g === "none" ? "any" : "all"]: items };
  return { not: inner };
}

// ---- what the columns are, for one project ---------------------------------------------------------------------------------------
// ctx: { cat, present (mapped fields; empty = all), pid, sources (data source ids) }
function builderColumns(ctx) {
  const has = (fs) => !fs || !ctx.present || !ctx.present.size || fs.some((f) => ctx.present.has(f));
  const own = (ctx.cat.own_fields || []);
  const kindOf = (f) => (f.kind === "number" ? "number" : f.kind === "date" ? "date" : "text");
  return {
    compare: Object.keys(MATCH_COLUMNS).filter((k) => has(MATCH_COLUMNS[k][1])).map((k) => [k, MATCH_COLUMNS[k][0]]),
    values: [...VALUE_COLUMNS.filter(([, , , fs]) => has(fs)).map(([c, t, k]) => [c, t, k]), ...own.map((f) => [f.comparison, f.title, kindOf(f)])],
    cross: [...CROSS_COLUMNS.filter(([, , fs]) => has(fs)).map(([c, t]) => [c, t]), ...own.map((f) => [f.comparison, f.title])],
  };
}
const elseLevel = (cat, c) => { const x = (cat.comparisons || []).find((y) => y.name === c); return x ? x.levels[x.levels.length - 1] : null; };
const levelsOf = (cat, c) => ((cat.comparisons || []).find((y) => y.name === c) || { levels: [] }).levels;
function cmpMode(cat, n) {
  if (n.missing) return "missing";
  const ls = n.is ? [n.is] : n.in;
  if (!ls) return null;
  const ways = (MATCH_COLUMNS[n.comparison] || [null, null, []])[2];
  const j = ways.findIndex(([, w]) => sameSet(w, ls));
  if (j >= 0) return "w:" + j;
  if (ls.length === 1 && ls[0] === elseLevel(cat, n.comparison)) return "differs";
  return "levels";
}
function leafKind(cat, n) {
  if (!n || typeof n !== "object") return "json";
  if (n.comparison) return cmpMode(cat, n) ? "cmp" : "json";
  if (n.cross) return "cross";
  if (n.field) return VALUE_HOW.some(([op]) => op === n.op) ? "val" : "json";
  if (n.score === "probability" && SCORE_OPS.some(([op]) => op === n.op)) return "score";
  if (n.own_fields === "agree") return "own";
  return "json";
}

// ---- in words: a condition, for the cards and the rule's title ---------------------------------------------------------------------
function conditionWords(n, cat) {
  if (!n || typeof n !== "object") return "?";
  if (n.all) return n.all.map((x) => conditionWords(x, cat)).join(", and ");
  if (n.any) return "(" + n.any.map((x) => conditionWords(x, cat)).join(", or ") + ")";
  if (n.not) return n.not.any ? "none of (" + n.not.any.map((x) => conditionWords(x, cat)).join(", ") + ")" : "not " + conditionWords(n.not, cat);
  const title = (c) => (MATCH_COLUMNS[c] ? MATCH_COLUMNS[c][0] : ((cat.comparisons || []).find((x) => x.name === c) || {}).title || colTitle(cat, c));
  const kind = leafKind(cat, n);
  if (kind === "cmp") {
    const m = cmpMode(cat, n);
    const t = title(n.comparison).toLowerCase();
    if (m === "missing") return t + " is empty on one or both records";
    if (m === "differs") return t + " differs";
    if (m.startsWith("w:")) return t + " " + MATCH_COLUMNS[n.comparison][2][Number(m.slice(2))][0].replace(/ \(.*\)$/, "");
    return t + ": " + (n.is ? [n.is] : n.in).join(" or ");
  }
  if (kind === "val") return colTitle(cat, n.field).toLowerCase() + " " + valueWords(n);
  if (kind === "cross") return colTitle(cat, n.cross[0]).toLowerCase() + " of one record " + (n.op === "similar" ? "is similar to" : "is the same as") + " " + colTitle(cat, n.cross[1]).toLowerCase() + " of the other";
  if (kind === "score") return "the match score " + SCORE_OPS.find(([op]) => op === n.op)[1] + " " + Math.round(n.value * 1000) / 10 + "%";
  if (kind === "own") return "one of your own fields agrees";
  return describeRule(n, cat);
}
function valueWords(n) {
  const list = (n.values || []).join(" or ");
  const v = n.value == null ? "" : String(n.value);
  return { same: "is the same on both records", differs: "differs", both_in: "is " + list + " on both records", either_in: "is " + list + " on at least one record",
    neither_in: "is not " + list + " on either record", both_contain: "contains \u201c" + v + "\u201d on both records",
    either_contains: "contains \u201c" + v + "\u201d on at least one record", within: "is within " + v, within_pct: "is within " + v + "%",
    within_days: "is within " + v + " days", abs_diff_gt: "differs by more than " + v, both_gte: "is at least " + v + " on both records",
    both_lte: "is at most " + v + " on both records", both_born_within_last: "is in the last " + v + " years on both records (both are young)",
    either_empty: "is empty on at least one record", both_empty: "is empty on both records", both_filled: "is filled on both records" }[n.op] || n.op;
}
function colTitle(cat, c) {
  const own = (cat.own_fields || []).find((f) => f.comparison === c);
  if (own) return own.title;
  const v = VALUE_COLUMNS.find(([x]) => x === c) || CROSS_COLUMNS.find(([x]) => x === c);
  return v ? v[1] : label(String(c).replace(/^x_/, ""));
}
// Can the rule find pairs on its own (records are compared only when they share an identifier, a phone or an email, two of name, date of
// birth and address, or a two-column "is the same")? A rule that cannot still applies to the pairs other rules find.
function conditionFinds(n, cat) {
  if (!n || typeof n !== "object") return false;
  if (n.all) {
    const agreeing = n.all.filter((x) => x.comparison && !x.missing && cmpMode(cat, x) && !["differs", "missing"].includes(cmpMode(cat, x))).map((x) => x.comparison);
    return n.all.some((x) => conditionFinds(x, cat)) || new Set(agreeing.filter((c) => ["name", "dob", "address"].includes(c))).size >= 2;
  }
  if (n.any) return n.any.every((x) => conditionFinds(x, cat));
  if (n.not) return false;
  if (n.cross) return n.op === "same";
  if (n.own_fields) return (cat.own_fields || []).some((f) => f.finds_pairs);
  if (n.comparison) {
    const m = cmpMode(cat, n);
    return STRONG_COLUMNS.has(n.comparison) && !!m && m !== "differs" && m !== "missing" && !(m === "levels" && (n.is ? [n.is] : n.in || []).includes(elseLevel(cat, n.comparison)));
  }
  return false;
}
// What is missing before a condition can be saved, in words (null when it is complete)
function conditionProblem(t, cat) {
  if (!t.leaf) {
    if (!t.items.length) return "A group has no condition: add one or remove the group.";
    for (const x of t.items) { const p = conditionProblem(x, cat); if (p) return p; }
    return null;
  }
  const n = t.leaf;
  const kind = leafKind(cat, n);
  if (kind === "cmp" && cmpMode(cat, n) === "levels" && !(n.is || (n.in || []).length)) return "Choose at least one result for " + colTitle(cat, n.comparison) + ".";
  if (kind === "val") {
    const how = VALUE_HOW.find(([op]) => op === n.op);
    if (how[2] === "list" && !(n.values || []).length) return "Type at least one value for " + colTitle(cat, n.field) + ".";
    if (how[2] === "text" && !String(n.value || "").trim()) return "Type what " + colTitle(cat, n.field) + " should contain.";
    if (how[2] === "number" && (n.value == null || Number.isNaN(n.value))) return "Type a number for " + colTitle(cat, n.field) + ".";
  }
  if (kind === "cross" && n.cross[0] === n.cross[1]) return "Choose two different columns to compare.";
  return null;
}

// ---- one rule in the side panel ------------------------------------------------------------------------------------------------------
// r: the rule (changed in place); ctx: { cat, present, pid, sources, useKept(column) -> {key, kind} | null, named: Set of rules named by a
// person }; from: the button that opened it; after(): called when the panel closes; changed(): after every change.
function openRuleBuilder(r, ctx, from, after, changed, remove) {
  const cat = ctx.cat;
  const tree = topTree(r.when);
  const cols = () => builderColumns(ctx);
  const nameIn = el("input", { type: "text", maxlength: "200", value: ctx.titleOf(r), "aria-label": "Name of the rule" });
  const typeSel = el("select", { "aria-label": "What happens" }, RULE_TYPES_SAID.map(([k, t]) => el("option", { value: k, selected: k === r.type }, t)));
  const box = el("div", { class: "cond-root" });
  const problem = el("p", { class: "warn-line", hidden: true });
  const finds = el("p", { class: "warn-line", hidden: true });
  const tested = el("div", { class: "rule-test", role: "status" });
  let timer = null;
  const test = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const p = conditionProblem(tree, cat);
      if (p) { fill(tested, el("p", { class: "muted" }, "Tested on the latest run once the rule is complete.")); return; }
      fill(tested, el("p", { class: "muted" }, "Testing on the latest run..."));
      try {
        const t = await api("POST", P(ctx.pid) + "/config/decision/test-rule", { when: r.when });
        if (t.no_run) { fill(tested, el("p", { class: "muted" }, "It is tested on the latest run once the project has one.")); return; }
        const NOW = { auto_merge: "merged", review: "sent to a person", reject: "kept apart" };
        const now = Object.entries(t.now).filter(([, n]) => n).map(([z, n]) => num(n) + " " + NOW[z]);
        fill(tested, el("strong", {}, t.not_applied ? "Not tested: it uses a field the latest run did not have. Run again to see what it catches."
          : "On the latest run it catches " + num(t.caught) + " of " + plural(t.pairs, "pair", "pairs") + "."),
        !t.not_applied && t.caught ? el("p", {}, "That run decided them: " + now.join(", ") + ".") : null,
        t.finds_more ? el("p", { class: "muted" }, "It also finds pairs the latest run did not compare (one column against another): the next run adds them.") : null,
        t.examples.length ? el("ul", { class: "rule-examples" }, t.examples.map((x) => el("li", {},
          el("span", {}, (x.left.name || x.left.record) + " (" + x.left.source + ")"), " and ", el("span", {}, (x.right.name || x.right.record) + " (" + x.right.source + ")"),
          el("span", { class: "muted" }, ": score " + Math.round(x.probability * 100) + "%, " + NOW[x.now])))) : null);
      } catch (e) {
        fill(tested, el("p", { class: "muted" }, e.message));
      }
    }, 600);
  };
  // after a person's change: the rule takes the new condition (and, unless a person named it, a title from it)
  const set = () => {
    r.when = condJson(tree);
    if (!ctx.named.has(r)) { r.reason = ctx.autoTitle(r); nameIn.value = r.reason; }
    changed();
    refresh();
  };
  // what is shown under the condition: what is missing, whether it finds pairs, the test on the latest run (opening changes nothing)
  const refresh = () => {
    const p = conditionProblem(tree, cat);
    problem.textContent = p || "";
    problem.hidden = !p;
    const lone = (r.type === "auto_merge" || r.type === "force_review") && !p && !conditionFinds(r.when, cat);
    finds.textContent = lone ? "On its own this finds no new pairs (records are compared only when they share an identifier, a phone or an email, two of " +
      "name, date of birth and address, or one column is the same as another). It still applies to the pairs other rules find." : "";
    finds.hidden = !lone;
    test();
  };
  nameIn.addEventListener("input", () => { ctx.named.add(r); r.reason = nameIn.value.trim() || ctx.autoTitle(r); changed(); });
  typeSel.addEventListener("change", () => { r.type = typeSel.value; set(); });

  // a line: what is checked, then how, then the value; changing what is checked starts the line again
  const leafRow = (t, parent) => {
    const n = t.leaf;
    const kind = leafKind(cat, n);
    const c = cols();
    const current = kind === "cmp" ? "cmp:" + n.comparison : kind === "val" ? "val:" + n.field : kind;
    const what = el("select", { "aria-label": "What to check" },
      el("optgroup", { label: "How the two records compare" }, c.compare.map(([k, t2]) => el("option", { value: "cmp:" + k, selected: current === "cmp:" + k }, t2))),
      el("optgroup", { label: "A column's values" }, c.values.map(([k, t2]) => el("option", { value: "val:" + k, selected: current === "val:" + k }, t2))),
      el("optgroup", { label: "Other" }, el("option", { value: "cross", selected: kind === "cross" }, "One column against another column"),
        el("option", { value: "score", selected: kind === "score" }, "Match score"),
        (cat.own_fields || []).length ? el("option", { value: "own", selected: kind === "own" }, "Any field of your own agrees") : null,
        kind === "json" ? el("option", { value: "json", selected: true }, "Written in JSON") : null),
      ctx.keptCols().length && can("config.propose") ? el("optgroup", { label: "Columns kept as they are (become a field of your own)" },
        ctx.keptCols().map((k) => el("option", { value: "kept:" + k }, k))) : null);
    // a value no list offers (a field this project does not have any more): shown as it is, so nothing changes without a person choosing
    if (![...what.options].some((o) => o.selected)) {
      const t2 = n.comparison && MATCH_COLUMNS[n.comparison] ? MATCH_COLUMNS[n.comparison][0] : colTitle(cat, n.comparison || n.field || "?");
      what.prepend(el("option", { value: current, selected: true }, t2 + " (not in your data)"));
    }
    const fresh = (v) => {
      if (v.startsWith("cmp:")) { const k = v.slice(4); const w = (MATCH_COLUMNS[k] || [null, null, []])[2]; return w.length ? (w[0][1].length === 1 ? { comparison: k, is: w[0][1][0] } : { comparison: k, in: w[0][1] }) : { comparison: k, in: [] }; }
      if (v.startsWith("val:")) return { field: v.slice(4), op: "same" };
      if (v === "cross") { const xs = c.cross.map(([k]) => k); return { cross: [xs[0], xs[1] || xs[0]], op: "same" }; }
      if (v === "score") return { score: "probability", op: ">=", value: 0.9 };
      return { own_fields: "agree" };
    };
    what.addEventListener("change", () => run(async () => {
      let v = what.value;
      if (v.startsWith("kept:")) {
        const k = await ctx.useKept(v.slice(5));
        if (!k) { draw(); return; }
        v = (k.kind === "category" ? "val:" : "cmp:") + k.key;
      }
      t.leaf = fresh(v);
      draw();
      set();
    }, true));
    const how = [];
    if (kind === "cmp") {
      const m = cmpMode(cat, n);
      const ways = (MATCH_COLUMNS[n.comparison] || [null, null, []])[2];
      const sel = el("select", { "aria-label": "How " + colTitle(cat, n.comparison) + " compares" },
        ways.map(([w], j) => el("option", { value: "w:" + j, selected: m === "w:" + j }, w)),
        elseLevel(cat, n.comparison) ? el("option", { value: "differs", selected: m === "differs" }, "differs") : null,
        el("option", { value: "missing", selected: m === "missing" }, "is empty on one or both records"),
        el("option", { value: "levels", selected: m === "levels" }, "has one of these results..."));
      sel.addEventListener("change", () => {
        const k = n.comparison;
        const v = sel.value;
        t.leaf = v.startsWith("w:") ? (() => { const ls = ways[Number(v.slice(2))][1]; return ls.length === 1 ? { comparison: k, is: ls[0] } : { comparison: k, in: ls }; })()
          : v === "differs" ? { comparison: k, is: elseLevel(cat, k) } : v === "missing" ? { comparison: k, missing: true } : { comparison: k, in: (n.is ? [n.is] : n.in || []) };
        draw();
        set();
      });
      how.push(sel);
      if (m === "levels") {
        const chosen = new Set(n.is ? [n.is] : n.in || []);
        how.push(el("div", { class: "level-picks" }, levelsOf(cat, n.comparison).map((lv) => {
          const cb = el("input", { type: "checkbox", checked: chosen.has(lv) });
          cb.addEventListener("change", () => {
            if (cb.checked) chosen.add(lv); else chosen.delete(lv);
            const ls = levelsOf(cat, n.comparison).filter((x) => chosen.has(x));
            t.leaf = ls.length === 1 ? { comparison: n.comparison, is: ls[0] } : { comparison: n.comparison, in: ls };
            set();
          });
          return el("label", { class: "check" }, cb, el("span", {}, lv));
        })));
      }
    } else if (kind === "val") {
      const col = c.values.find(([k]) => k === n.field);
      const ck = col ? col[2] : "text";
      const ops = VALUE_HOW.filter(([op, , , kinds]) => (!kinds || kinds.includes(ck)) && (op !== "both_born_within_last" || n.field === "dob_year"));
      const sel = el("select", { "aria-label": "How " + colTitle(cat, n.field) + " is checked" }, ops.map(([op, w]) => el("option", { value: op, selected: op === n.op }, w)));
      sel.addEventListener("change", () => {
        const arg = VALUE_HOW.find(([op]) => op === sel.value)[2];
        t.leaf = { field: n.field, op: sel.value, ...(arg === "list" ? { values: n.values || [] } : arg === "text" ? { value: typeof n.value === "string" ? n.value : "" }
          : arg === "number" ? { value: typeof n.value === "number" ? n.value : sel.value === "within_days" ? 30 : sel.value === "within_pct" ? 5 : 1 } : {}) };
        draw();
        set();
      });
      how.push(sel);
      const arg = (VALUE_HOW.find(([op]) => op === n.op) || [null, null, "none"])[2];
      if (arg === "list") {
        const i = el("input", { type: "text", value: (n.values || []).join(", "), placeholder: n.field === "source_id" ? (ctx.sources || []).join(", ") : "For example Corporate, SME",
          "aria-label": "Values of " + colTitle(cat, n.field) + ", separated by commas" });
        i.addEventListener("input", () => { n.values = i.value.split(",").map((x) => x.trim()).filter(Boolean); set(); });
        how.push(i, n.field === "source_id" && (ctx.sources || []).length ? el("span", { class: "muted small" }, "Data sources: " + ctx.sources.join(", ")) : el("span", { class: "muted small" }, "Separate values with commas; any case."));
      } else if (arg === "text") {
        const i = el("input", { type: "text", maxlength: "100", value: n.value || "", "aria-label": "Text " + colTitle(cat, n.field) + " contains" });
        i.addEventListener("input", () => { n.value = i.value; set(); });
        how.push(i);
      } else if (arg === "number") {
        const i = el("input", { type: "number", step: "any", min: "0", value: n.value == null ? "" : String(n.value), class: "narrow-input", "aria-label": "Number for " + colTitle(cat, n.field) });
        i.addEventListener("input", () => { n.value = i.value === "" ? null : ["within_days", "both_born_within_last"].includes(n.op) ? Math.round(Number(i.value)) : Number(i.value); set(); });
        how.push(i, n.op === "within_pct" ? el("span", {}, "%") : n.op === "within_days" ? el("span", {}, "days") : n.op === "both_born_within_last" ? el("span", {}, "years") : null);
      }
    } else if (kind === "cross") {
      const pick = (i) => {
        const s = el("select", { "aria-label": i ? "Column of the other record" : "Column of one record" }, c.cross.map(([k, t2]) => el("option", { value: k, selected: k === n.cross[i] }, t2)));
        s.addEventListener("change", () => { n.cross[i] = s.value; set(); });
        return s;
      };
      const op = el("select", { "aria-label": "How the two columns compare" }, el("option", { value: "same", selected: n.op === "same" }, "is the same as"),
        el("option", { value: "similar", selected: n.op === "similar" }, "is similar to"));
      op.addEventListener("change", () => { n.op = op.value; set(); });
      how.push(pick(0), el("span", { class: "muted" }, "of one record"), op, pick(1), el("span", { class: "muted" }, "of the other"));
    } else if (kind === "score") {
      const op = el("select", { "aria-label": "Match score" }, SCORE_OPS.map(([o, w]) => el("option", { value: o, selected: o === n.op }, w)));
      const v = el("input", { type: "number", min: "0", max: "100", step: "0.1", value: String(Math.round(n.value * 1000) / 10), class: "narrow-input", "aria-label": "Match score in percent" });
      op.addEventListener("change", () => { n.op = op.value; set(); });
      v.addEventListener("input", () => { n.value = Math.round(Number(v.value) * 10) / 1000; set(); });
      how.push(op, v, el("span", {}, "%"));
    } else if (kind === "json") {
      const ta = el("textarea", { class: "code", rows: "3", spellcheck: "false", "aria-label": "Condition in JSON" }, JSON.stringify(n));
      ta.addEventListener("input", () => { try { t.leaf = JSON.parse(ta.value); set(); } catch (_) { /* finished typing later */ } });
      how.push(ta);
    }
    const rm = btn("", () => { parent.items.splice(parent.items.indexOf(t), 1); draw(); set(); }, "small ghost icon", "x", { "aria-label": "Remove this condition" });
    return el("div", { class: "cond-row" }, el("div", { class: "cond-what" }, what, how), rm);
  };
  const groupBox = (g, parent, depth) => {
    const sel = el("select", { "aria-label": depth ? "This group" : "The rule applies when" }, GROUPS.map(([k, t]) => el("option", { value: k, selected: k === g.g }, t)));
    sel.addEventListener("change", () => { g.g = sel.value; set(); });
    const items = g.items.map((x) => (x.leaf ? leafRow(x, g) : groupBox(x, g, depth + 1)));
    // a new line starts as the first column's first way to agree (a project without columns: the match score)
    const newLeaf = () => {
      const c0 = cols().compare[0];
      if (!c0) return { leaf: { score: "probability", op: ">=", value: 0.9 } };
      const ls = MATCH_COLUMNS[c0[0]][2][0][1];
      return { leaf: ls.length === 1 ? { comparison: c0[0], is: ls[0] } : { comparison: c0[0], in: ls } };
    };
    const addLeaf = () => { g.items.push(newLeaf()); draw(); set(); };
    const addGroup = () => { g.items.push({ g: g.g === "all" ? "any" : "all", items: [newLeaf()] }); draw(); set(); };
    return el("div", { class: "cond-group" + (depth ? " nested" : "") },
      el("div", { class: "cond-group-head" }, depth ? null : el("span", { class: "muted" }, "When"), sel,
        depth ? btn("", () => { parent.items.splice(parent.items.indexOf(g), 1); draw(); set(); }, "small ghost icon", "x", { "aria-label": "Remove this group" }) : null),
      el("div", { class: "cond-items" }, items.length ? items : el("p", { class: "muted" }, "No condition yet.")),
      el("div", { class: "row tight" }, btn("Condition", addLeaf, "small", "plus", { "aria-label": "Add a condition to this group" }),
        depth < 3 ? btn("Group", addGroup, "small", "plus", { "aria-label": "Add a group inside this group" }) : null));
  };
  const draw = () => fill(box, groupBox(tree, null, 0));
  draw();
  refresh();
  sidePanel("Rule", el("div", { class: "rule-builder" },
    field("Name (what people read on the card)", nameIn), field("What happens", typeSel),
    el("p", { class: "hint" }, "Build the condition from lines and groups. A group inside a group is checked as one line of the outer group."),
    box, problem, finds, tested),
  (close) => [btn("Done", close, "primary", "check"), btn("Remove this rule", () => { close(); remove(r); }, "danger", "trash")], from, after);
  const panel = $("side-panel");
  if (panel) panel.classList.add("wide");
}
