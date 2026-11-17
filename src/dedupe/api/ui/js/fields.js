"use strict";
// A project's own fields and how its records are found: the form over the matching settings. Part of the portal script: the files in js/
// share one scope and load in the order of routes_ui.SCRIPTS.
// The built-in fields describe a customer. Anything else a project wants matched (a company name, a GSTIN, a product code, an amount) is a
// field of its own: it has a kind, the kind decides how its values are cleaned and compared, and the engine treats it like a built-in one.
const KIND_TITLE = { text: "Text", company: "Organisation name", name: "Person's name", code: "Code or reference number", number: "Number", date: "Date",
  category: "Category (one of a few values)", phone: "Phone number", email: "Email address" };
const TEXT_KINDS = ["text", "company", "name"];
// which ways a search rule can read a value of each kind
const HOW_OF = { text: ["exact", "sounds", "words", "first_word", "prefix"], code: ["exact", "prefix"], number: ["exact"], date: ["exact", "year"] };
const howOf = (kind) => HOW_OF[TEXT_KINDS.includes(kind) ? "text" : kind] || ["exact", "prefix"];
const BUILTIN_HOW = { dob: ["exact", "year"], dob_year: ["exact"] };
const findsByItself = (f) => (f.block == null ? [...TEXT_KINDS, "code", "phone", "email"].includes(f.kind) : f.block);
const slug = (s, fallback) => (String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^[_0-9]+|_+$/g, "").slice(0, 36) || fallback);
function freeName(base, taken) {
  let name = base;
  for (let i = 2; taken.includes(name); i++) name = base + "_" + i;
  return name;
}
const ownTitle = (f) => f.title || label(f.name);
// a field's options in a few words (the list people read before they change anything)
function fieldWords(f) {
  return [f.identifier ? "an identifier: the same value means the same record" : null, (f.ignore_words || []).length ? "ignores " + f.ignore_words.join(", ") : null,
    f.tolerance ? "close within " + f.tolerance + (f.kind === "date" ? " days" : f.tolerance_is_percent ? "%" : "") : null,
    findsByItself(f) ? null : "not used to find pairs"].filter(Boolean).join("; ");
}
const partWords = (p, fields, search) => {
  const f = fields.find((x) => x.name === p.field);
  return (f ? ownTitle(f) : search.columns[p.field] || label(p.field)) + ": " + search.how[p.how || "exact"] + (p.how === "prefix" ? " (" + (p.chars || 3) + ")" : "");
};
const LIMITS = [["blocking_cap", "A value shared by more records than this is too common to search by", 2, 1000, 1],
  ["max_cluster_size", "A group of records larger than this is sent to a person", 2, 1000, 1],
  ["household_threshold", "Evidence needed to put two people in one household", 0.1, 100, 0.1]];
// Read-only: what the project compares and how pairs are found.
function setupSummary(content, cat) {
  const fields = content.custom_fields || [];
  const rules = content.custom_blocking || [];
  const weights = Object.entries(content.fixed_weights || {});
  const off = cat.search.built_in.filter((r) => !(content.blocking_rules || []).includes(r.name) && r.standard);
  const compTitle = (n) => (cat.comparisons.find((c) => c.name === n || c.name === "x_" + n) || { title: label(n) }).title;
  return el("div", { class: "matching-view" },
    step(1, "Your own fields", "Columns of your own that records are matched on, beside the built-in customer fields.",
      fields.length ? table(["Field", "Kind", "Options"], fields.map((f) => el("tr", {}, el("td", {}, el("strong", {}, ownTitle(f))), el("td", {}, KIND_TITLE[f.kind || "text"]),
        el("td", {}, fieldWords(f) || el("span", { class: "muted" }, "-"))))) : el("p", { class: "muted" }, "None: records are matched on the built-in customer fields only.")),
    step(2, "How pairs are found", "Two records are compared only when a search rule brings them together.",
      el("p", {}, content.compare_within_source === false ? "Only records of different data sources are compared: a file is never compared with itself."
        : "Records are compared within a data source and across data sources."),
      rules.length ? el("ul", { class: "changes" }, rules.map((r) => el("li", {}, r.parts.map((p) => partWords(p, fields, cat.search)).join(" and ")))) : null,
      off.length ? el("p", { class: "muted" }, "Built-in search rules switched off: " + off.map((r) => r.title).join("; ") + ".") : null),
    step(3, "Weights set by hand", "Normally the engine learns how much each agreement counts.",
      weights.length ? el("ul", { class: "changes" }, weights.flatMap(([c, ls]) => Object.entries(ls).map(([lv, bits]) =>
        el("li", {}, compTitle(c) + ", " + (lv === "*" ? "every level" : "“" + lv + "”") + ": " + (bits === 0 ? "never counts" : bits)))))
        : el("p", { class: "muted" }, "None: every weight is learned from the data.")),
    step(4, "Words and limits", null, kv([["One record is called", content.record_name || (fields.length ? "record" : "customer")],
      ["Households", content.build_households == null ? (fields.length ? "No (automatic)" : "Yes (automatic)") : content.build_households ? "Yes" : "No"], ...LIMITS.map(([k, text]) => [text, String(content[k])])])));
}
// One own field as a row of controls. `f` is edited in place; `changed` redraws what depends on the list of fields.
function ownFieldRow(f, isNew, changed, remove) {
  const title = el("input", { type: "text", maxlength: "60", value: f.title || (isNew ? "" : label(f.name)), placeholder: "For example: Company name", "aria-label": "Name of the field" });
  const kind = el("select", { "aria-label": "Kind of " + (ownTitle(f) || "the field") }, Object.entries(KIND_TITLE).map(([k, t]) => el("option", { value: k, selected: k === (f.kind || "text") }, t)));
  const more = el("div", { class: "own-options" });
  const check = (text, on, set) => {
    const cb = el("input", { type: "checkbox", checked: on });
    cb.addEventListener("change", () => { set(cb.checked); changed(); });
    return el("label", { class: "check" }, cb, text);
  };
  const draw = () => {
    const k = f.kind || "text";
    if (k !== "code") delete f.identifier;
    if (!["text", "company"].includes(k)) delete f.ignore_words;
    if (!["number", "date"].includes(k)) { delete f.tolerance; delete f.tolerance_is_percent; }
    if (k !== "number") delete f.tolerance_is_percent;
    const parts = [];
    if (k === "code") parts.push(check("An identifier: the same value always means the same record (like a PAN for a person)", !!f.identifier, (v) => { f.identifier = v; }));
    if (k === "text" || k === "company") {
      const words = el("input", { type: "text", maxlength: "400", value: (f.ignore_words || []).join(", "), placeholder: k === "company" ? "traders, enterprises" : "the, of" });
      words.addEventListener("input", () => { f.ignore_words = words.value.split(/[,\s]+/).map((w) => w.trim().toLowerCase()).filter(Boolean); });
      parts.push(field("Words to leave out before comparing" + (k === "company" ? " (Pvt, Ltd, Inc and the like are left out already)" : ""), words));
    }
    if (k === "number" || k === "date") {
      const tol = el("input", { type: "number", min: "0", step: k === "date" ? "1" : "any", value: String(f.tolerance || 0), class: "pct" });
      tol.addEventListener("input", () => { f.tolerance = Math.max(Number(tol.value) || 0, 0); });
      const unit = el("select", { "aria-label": "Unit" }, el("option", { value: "", selected: !f.tolerance_is_percent }, "as an amount"), el("option", { value: "p", selected: !!f.tolerance_is_percent }, "percent"));
      unit.addEventListener("change", () => { f.tolerance_is_percent = unit.value === "p"; });
      parts.push(el("div", { class: "own-tolerance" }, field(k === "date" ? "Still close when this many days apart (0 = the same day only)" : "Still close when this far apart (0 = equal only)", tol),
        k === "number" ? unit : null));
    }
    parts.push(check("Find pairs by this field", findsByItself(f), (v) => { f.block = v; }));
    if (TEXT_KINDS.includes(k)) {
      parts.push(check("Many different records share the same value (a rare value then counts for more)", f.common_values == null ? k === "name" : f.common_values, (v) => { f.common_values = v; }));
    }
    fill(more, parts);
  };
  title.addEventListener("input", () => { f.title = title.value.trim(); changed(); });
  kind.addEventListener("change", () => { f.kind = kind.value; delete f.block; delete f.common_values; draw(); changed(); });
  draw();
  return el("div", { class: "own-field" }, el("div", { class: "own-head" }, field("Field", title), field("Kind", kind),
    btn("Remove", remove, "small ghost", "trash", { "aria-label": "Remove the field " + (ownTitle(f) || "") })), more);
}
// The editor of the matching settings: own fields, search rules, weights set by hand, limits.
function setupForm(content, cat, propose, cancel, opts) {
  const state = JSON.parse(JSON.stringify(content));
  state.custom_fields = state.custom_fields || [];
  state.custom_blocking = state.custom_blocking || [];
  const fresh = new Set();                                   // fields added in this form: their name is made from the title when saving
  const weights = Object.entries(state.fixed_weights || {}).flatMap(([c, ls]) => Object.entries(ls).map(([lv, bits]) => ({ c, lv, bits })));
  const nameAll = () => {
    for (const f of state.custom_fields) {
      if (!fresh.has(f)) continue;
      const old = f.name;
      f.name = freeName(slug(f.title, "field"), state.custom_fields.filter((x) => x !== f).map((x) => x.name));
      if (!old || old === f.name) continue;                  // renamed while a search rule or a weight already points at it: they follow
      for (const r of state.custom_blocking) for (const part of r.parts) if (part.field === old) part.field = f.name;
      for (const w of weights) if (w.c === old) w.c = f.name;
    }
  };
  // ---- 1 own fields
  const fieldList = el("div", { class: "rule-group" });
  const drawFields = () => fill(fieldList, state.custom_fields.length ? state.custom_fields.map((f) => ownFieldRow(f, fresh.has(f), others, () => {
    state.custom_fields = state.custom_fields.filter((x) => x !== f);
    state.custom_blocking = state.custom_blocking.filter((r) => !r.parts.some((p) => p.field === f.name));
    for (let i = weights.length - 1; i >= 0; i--) if (weights[i].c === f.name || weights[i].c === "x_" + f.name) weights.splice(i, 1);
    drawFields();
    others();
  })) : el("p", { class: "muted" }, "None yet. Add one for every column of your own that should count when two records are compared."));
  const addField = () => {
    const f = { name: "", title: "", kind: "text" };
    fresh.add(f);
    state.custom_fields.push(f);
    drawFields();
    fieldList.lastElementChild.querySelector("input").focus();
  };
  // ---- 2 how pairs are found
  const across = el("input", { type: "checkbox", checked: state.compare_within_source === false });
  const builtIn = new Set(state.blocking_rules || []);
  const builtList = el("div", { class: "check-list" }, cat.search.built_in.map((r) => {
    const cb = el("input", { type: "checkbox", checked: builtIn.has(r.name) });
    cb.addEventListener("change", () => { if (cb.checked) builtIn.add(r.name); else builtIn.delete(r.name); count(); });
    return el("label", { class: "check" }, cb, cap(r.title) + (r.standard ? "" : " (extra)"));
  }));
  const builtSum = el("summary");
  const count = () => { builtSum.textContent = "Built-in search rules for customer data (" + builtIn.size + " of " + cat.search.built_in.length + " on)"; };
  count();
  const ruleList = el("div", { class: "rule-group" });
  const choices = () => { nameAll(); return [...state.custom_fields.map((f) => [f.name, ownTitle(f) || "(field without a name)", howOf(f.kind || "text")]),
    ...Object.entries(cat.search.columns).map(([c, t]) => [c, t, BUILTIN_HOW[c] || ["exact", "prefix"]])]; };
  const drawRules = () => fill(ruleList, state.custom_blocking.length ? state.custom_blocking.map((r, n) => {
    const all = choices();
    const rows = el("div", { class: "match-conds" });
    const name = "Search rule " + (n + 1);
    const drawParts = () => fill(rows, r.parts.map((p, i) => {
      const cur = all.find((c) => c[0] === p.field) || all[0];
      if (!cur[2].includes(p.how || "exact")) p.how = cur[2][0];
      const col = el("select", { "aria-label": name + ": column " + (i + 1) }, all.map(([v, t]) => el("option", { value: v, selected: v === p.field }, t)));
      const how = el("select", { "aria-label": name + ": what must be the same" }, cur[2].map((h) => el("option", { value: h, selected: h === (p.how || "exact") }, cat.search.how[h])));
      const chars = el("input", { type: "number", min: "1", max: "20", step: "1", value: String(p.chars || 3), class: "pct", "aria-label": "How many characters" });
      col.addEventListener("change", () => { p.field = col.value; drawParts(); });
      how.addEventListener("change", () => { p.how = how.value; drawParts(); });
      chars.addEventListener("input", () => { p.chars = Math.min(Math.max(Math.round(Number(chars.value)) || 3, 1), 20); });
      return el("div", { class: "match-cond" }, el("span", { class: "and muted" }, i ? "and" : ""), col, how, p.how === "prefix" ? chars : null,
        r.parts.length > 1 ? btn("", () => { r.parts.splice(i, 1); drawParts(); }, "small ghost", "x", { "aria-label": "Remove column " + (i + 1) + " from " + name }) : null);
    }));
    drawParts();
    return el("div", { class: "match-card" }, el("span", { class: "match-name" }, el("strong", {}, name)), rows,
      btn("Column", () => { if (r.parts.length < 4) { r.parts.push({ field: all[0][0], how: "exact" }); drawParts(); } }, "small", "plus", { "aria-label": "Add a column to " + name }),
      btn("Remove", () => { state.custom_blocking = state.custom_blocking.filter((x) => x !== r); drawRules(); }, "small ghost", "trash", { "aria-label": "Remove " + name }));
  }) : el("p", { class: "muted" }, "No search rule of your own. Fields ticked “Find pairs by this field” already bring theirs."));
  const addRule = () => {
    const all = choices();
    state.custom_blocking.push({ name: freeName("rule", state.custom_blocking.map((r) => r.name)), parts: [{ field: all[0][0], how: all[0][2][0] }] });
    drawRules();
  };
  // ---- 3 weights set by hand
  const weightList = el("div", { class: "rule-group" });
  // the levels of an own field as the form shows it now (kind, tolerance ...), asked from the engine: no need to save first
  const levelCache = new Map();
  const levelsOf = (f) => {
    const key = JSON.stringify({ ...f, name: "", title: "" });
    if (!levelCache.has(key)) {
      levelCache.set(key, []);
      api("POST", P(opts.pid) + "/field-levels", { ...f, name: "f" }).then((r) => { levelCache.set(key, r.levels); drawWeights(); }).catch(() => {});
    }
    return levelCache.get(key);
  };
  const comps = () => { nameAll(); return [...cat.comparisons.filter((c) => !c.own).map((c) => [c.name, c.title, c.levels]),
    ...state.custom_fields.map((f) => [f.name, ownTitle(f), levelsOf(f)])]; };
  const drawWeights = () => fill(weightList, weights.length ? weights.map((w, i) => {
    const all = comps();
    const cur = all.find((c) => c[0] === w.c || "x_" + c[0] === w.c) || all[0];
    w.c = cur[0];
    const col = el("select", { "aria-label": "Weight " + (i + 1) + ": column" }, all.map(([v, t]) => el("option", { value: v, selected: v === cur[0] }, t)));
    const lv = el("select", { "aria-label": "Weight " + (i + 1) + ": when" }, el("option", { value: "*", selected: w.lv === "*" }, "whatever the two values are (every level)"),
      cur[2].map((l) => el("option", { value: l, selected: l === w.lv }, "when: " + l)));
    const bits = el("input", { type: "number", min: "-30", max: "30", step: "0.1", value: String(w.bits), class: "pct", "aria-label": "Weight " + (i + 1) + ": weight" });
    col.addEventListener("change", () => { w.c = col.value; w.lv = "*"; drawWeights(); });
    lv.addEventListener("change", () => { w.lv = lv.value; });
    bits.addEventListener("input", () => { w.bits = Number(bits.value); });
    return el("div", { class: "match-card" }, col, lv, el("span", {}, "counts"), bits,
      btn("Remove", () => { weights.splice(i, 1); drawWeights(); }, "small ghost", "trash", { "aria-label": "Remove weight " + (i + 1) }));
  }) : el("p", { class: "muted" }, "None: every weight is learned from the data."));
  const others = () => { drawRules(); drawWeights(); };
  // ---- 4 limits
  const limits = LIMITS.map(([k, text, min, max, stepBy]) => {
    const input = el("input", { type: "number", min: String(min), max: String(max), step: String(stepBy), value: String(state[k]), class: "pct" });
    return [k, input, field(text, input)];
  });
  const recordName = el("input", { type: "text", maxlength: "30", value: state.record_name || "", class: "narrow-input" });
  const recordPlural = el("input", { type: "text", maxlength: "30", value: state.record_name_plural || "", class: "narrow-input", placeholder: "made from the word above" });
  const households = el("select", {}, el("option", { value: "", selected: state.build_households == null }, "Automatic: only when the project is about people"),
    el("option", { value: "yes", selected: state.build_households === true }, "Yes"), el("option", { value: "no", selected: state.build_households === false }, "No"));
  drawFields();
  others();
  // ---- experts and saving
  const current = () => {
    nameAll();
    const fixed = {};
    for (const w of weights) (fixed[w.c] = fixed[w.c] || {})[w.lv] = w.bits;
    const out = { ...state, blocking_rules: cat.search.built_in.map((r) => r.name).filter((n) => builtIn.has(n)), fixed_weights: fixed, compare_within_source: !across.checked };
    for (const [k, input] of limits) out[k] = Number(input.value);
    out.record_name = recordName.value.trim();
    out.record_name_plural = out.record_name ? recordPlural.value.trim() : "";
    out.build_households = households.value === "" ? null : households.value === "yes";
    return out;
  };
  const editor = el("textarea", { spellcheck: "false", rows: "14", class: "code" });
  const json = el("details", { class: "advanced" }, el("summary", {}, "For experts: the settings as JSON"),
    el("p", { class: "hint" }, "Saving while this is open uses the JSON below as it is, not the choices above."), field("Settings (JSON)", editor));
  json.addEventListener("toggle", () => { if (json.open) editor.value = JSON.stringify(current(), null, 2); });
  const why = el("input", { type: "text", maxlength: "300" });
  const err = errBox();
  const save = async () => {
    fail(err, "");
    let out;
    if (json.open) {
      try { out = JSON.parse(editor.value); } catch (_) { fail(err, "That is not valid JSON.", editor); return; }
    } else {
      if (!/^[A-Za-z ]*$/.test(recordName.value.trim())) { fail(err, "The word for one record is letters only.", recordName); return; }
      if (!/^[A-Za-z ]*$/.test(recordPlural.value.trim())) { fail(err, "The word for several records is letters only.", recordPlural); return; }
      if (state.custom_fields.some((f) => !ownTitle(f))) { fail(err, "Give every field of your own a name."); return; }
      if (weights.some((w) => !Number.isFinite(w.bits) || Math.abs(w.bits) > 30)) { fail(err, "A weight set by hand is a number between -30 and 30."); return; }
      out = current();
      const dup = out.custom_fields.map(ownTitle).find((t, i, all) => all.findIndex((x) => x.toLowerCase() === t.toLowerCase()) !== i);
      if (dup) { fail(err, "Two fields are called " + dup + ". Give each its own name."); return; }
      if (!out.blocking_rules.length && !out.custom_blocking.length && !out.custom_fields.some(findsByItself)) {
        fail(err, "Nothing is left to find pairs with: switch a built-in search rule on, tick “Find pairs by this field” on a field, or add a search rule.");
        return;
      }
    }
    try { await propose(out, why.value); } catch (e) { fail(err, e.message); }
  };
  return el("div", { class: "matching-view editing" },
    step(1, "Your own fields", "A field for every column of your own that should count when two records are compared: a company name, a GSTIN, a product code, an amount. " +
      "Its kind decides how values are cleaned and compared. On the Data page, choose which column of each file fills it.",
    fieldList, el("div", { class: "row" }, btn("Add a field", addField, "", "plus"))),
    step(2, "How pairs are found", "Two records are compared only when a search rule brings them together: comparing every record with every other would take too long. " +
      "A search rule only finds candidates; whether they are the same is decided by the matching rules.",
    el("label", { class: "check" }, across, "Compare records of different data sources only (link two files; a file is never compared with itself)"),
    el("h4", { class: "sub" }, "Search rules of your own"), el("p", { class: "muted" }, "Two records become a candidate pair when every column of a rule gives the same key."),
    ruleList, el("div", { class: "row" }, btn("Add a search rule", addRule, "", "plus")),
    el("details", { class: "advanced" }, builtSum, builtList)),
    step(3, "Weights set by hand", "Normally the engine learns from your data how much each agreement counts. A weight here replaces what it learns: " +
      "0 means it never counts, a positive number counts for a match, a negative one against (between -30 and 30; a shared PAN counts 13).",
    weightList, el("div", { class: "row" }, btn("Set a weight", () => { weights.push({ c: comps()[0][0], lv: "*", bits: 0 }); drawWeights(); }, "", "plus"))),
    step(4, "Words and limits", "Change the limits together with your administrator.",
      el("div", { class: "fields" },
        field("What one record is called on screen (for example: vendor, product). Empty: \u201ccustomer\u201d, or \u201crecord\u201d when the project has fields of its own", recordName),
        field("Several of them (only for a word like person / people)", recordPlural)),
      field("Group records into households (family, shared home)", households),
      el("div", { class: "fields" }, limits.map((l) => l[2]))),
    json, saveBar(why, err, save, cancel, opts));
}
// Mapping screen: a new field of the project's own, made while mapping a column. Saves the project's settings with the field added and
// returns the field ({ name, title, kind }), or null when cancelled.
async function newOwnField(pid, column, taken) {
  const title = el("input", { type: "text", maxlength: "60", value: column });
  const kind = el("select", {}, Object.entries(KIND_TITLE).map(([k, t]) => el("option", { value: k }, t)));
  const ident = el("input", { type: "checkbox" });
  const identRow = el("label", { class: "check", hidden: true }, ident, "An identifier: the same value always means the same record");
  kind.addEventListener("change", () => { identRow.hidden = kind.value !== "code"; });
  const err = errBox();
  const body = el("div", {}, el("p", { class: "hint" }, "Records will be matched on this column too. Its kind decides how its values are cleaned and compared."),
    field("Name of the field", title), field("Kind", kind), identRow, err);
  return dialog("A field of your own", body, "Add the field", async () => {
    const t = title.value.trim();
    if (!t) { fail(err, "Give the field a name.", title); return undefined; }
    try {
      const base = P(pid) + "/config/settings";
      const versions = await api("GET", base);
      const latest = versions.filter((v) => ["proposed", "approved", "active"].includes(v.status)).pop();
      const content = (await api("GET", latest ? base + "/versions/" + latest.version : base + "/default")).content;
      const name = freeName(slug(t, "field"), [...(content.custom_fields || []).map((f) => f.name), ...taken]);
      const f = { name, title: t, kind: kind.value, ...(kind.value === "code" && ident.checked ? { identifier: true } : {}) };
      const proj = await api("GET", P(pid));
      const out = await api("POST", base + "/propose", { content: { ...content, custom_fields: [...(content.custom_fields || []), f] }, reason: "Added the field " + t + " while mapping a file" });
      if (!proj.second_approval && can("config.activate")) await api("POST", base + "/versions/" + out.version + "/activate", { reason: "Added the field " + t });
      return f;
    } catch (e) { fail(err, e.message); return undefined; }
  });
}
