"use strict";
// Data of a project: connections, uploads and column mapping. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
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
// Uploading files: each file is sent and checked on its own, so one bad file is named and the others still go in. Each gets a data source id
// from its name, never one the project already uses. Returns the uploaded files as a batch for the mapping screens (or null if none went in).
const uploadLabel = (u) => u.filename + " (" + num(u.rows) + " rows, " + u.columns.length + " columns, stored encrypted)";
function uploadId(filename, taken) {
  const base = filename.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 36) || "upload";
  let id = base;
  for (let n = 2; taken.has(id); n++) id = base + "_" + n;
  taken.add(id);
  return id;
}
async function uploadFiles(pid, list, status) {
  const files = [...list];
  const taken = new Set((await api("GET", P(pid) + "/connections")).map((c) => c.id));
  const done = [];
  const failed = [];
  for (const [i, f] of files.entries()) {
    status.textContent = "Uploading and checking " + f.name + (files.length > 1 ? " (" + (i + 1) + " of " + files.length + ")" : "") + "...";
    try {
      const r = await api("POST", P(pid) + "/uploads?filename=" + encodeURIComponent(f.name), f, { "Content-Type": "application/octet-stream" }, true);
      done.push({ upload_id: r.upload_id, id: uploadId(r.filename, taken), label: uploadLabel(r) });
    } catch (e) { failed.push(f.name + ": " + e.message.replace(/[^.]$/, "$&.")); }
  }
  status.textContent = "";
  const bad = failed.length ? (failed.length === 1 ? "This file could not be read. " : "These files could not be read. ") + failed.join(" ") : "";
  if (!done.length) throw new Error(bad || "No file was uploaded.");
  return { batch: { files: done, index: 0, memo: { reason: null } }, bad };
}
// Which identity field a document type names ("PAN", "Pan card", "AADHAAR", "Driving Licence" ...), for document columns (U2-15).
const DOC_TYPES = [[/\bpan\b|pan ?card/i, "pan", ["pan", "pan card"]], [/aadh?aa?r|\buid\b/i, "aadhaar", ["aadhaar", "aadhar", "aadhaar card", "uid"]],
  [/passport/i, "passport_no", ["passport"]], [/driv|licen[cs]e|\bdl\b/i, "dl_no", ["dl", "driving licence", "driving license", "driving_licence", "driving_license"]],
  [/voter|\bepic\b|election/i, "voter_id", ["voter id", "voter_id", "voter", "epic", "voter card", "election card"]]];
// The upload area: drop files or choose them; when they are in, the mapping screens open with the first file.
function uploadZone(pid, firstId) {
  const input = el("input", { type: "file", accept: ".csv,.tsv,.txt,.xlsx,.json,.jsonl,.ndjson,.parquet", multiple: true, hidden: true });
  const status = el("div", { role: "status" });
  const zone = el("div", { class: "dropzone" }, icon("upload"), el("strong", {}, "Drop files here"), btn("Choose files", () => input.click(), "small"),
    el("div", { class: "muted" }, "One file or several at once: CSV, TSV, Excel (.xlsx), JSON, JSON Lines or Parquet. Each file becomes its own data source; " +
      "with several, you map them one after another."), status);
  const send = (list) => run(async () => {
    if (list.length === 1 && firstId && firstId() && (await api("GET", P(pid) + "/connections")).some((c) => c.id === firstId())) {
      throw new Error("This project already has a data source called " + firstId() + ". Choose another id, or open that one to change it.");
    }
    const { batch, bad } = await uploadFiles(pid, list, status);
    if (batch.files.length === 1 && firstId && firstId()) batch.files[0].id = firstId();          // an id typed before the upload is kept
    await connectionWizard(pid, "upload", null, 2, batch);
    if (bad) say(bad + " The other files are in: map them now.", "bad");
    else if (batch.files.length > 1) say(num(batch.files.length) + " files uploaded. Map them one at a time: this is the first.", "good");
  });
  input.addEventListener("change", () => { if (input.files.length) send(input.files); });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); if (e.dataTransfer.files.length) send(e.dataTransfer.files); });
  return el("div", {}, zone, input);
}
// Files uploaded but not mapped yet (no data source reads them), oldest first, as a batch; `/map/<upload>` opens one of them, so a reload or
// Back during mapping comes back to the same file.
async function mapUpload(pid, uid) {
  const [uploads, conns] = await Promise.all([api("GET", P(pid) + "/uploads"), api("GET", P(pid) + "/connections")]);
  const taken = new Set(conns.map((c) => c.id));
  const files = uploads.filter((u) => !u.used_by.length).reverse().map((u) => ({ upload_id: u.upload_id, id: uploadId(u.filename, taken), label: uploadLabel(u) }));
  const index = files.findIndex((f) => f.upload_id === uid);
  if (index < 0) { await viewProject(pid, "setup", "data"); say("That file is mapped already or was deleted.", "good"); return; }
  await connectionWizard(pid, "upload", null, 2, { files, index, memo: { reason: null } });
}
async function addDataPage(pid) {
  here(P(pid) + "/add-data");
  const cat = await catalogue();
  const pick = (type) => run(() => connectionWizard(pid, type));
  const others = cat.catalogue.filter((c) => c.type !== "upload");
  const q = el("input", { type: "search", placeholder: "Search: oracle, sql server, s3, sftp, salesforce...", "aria-label": "Search sources", class: "source-search" });
  const sections = cat.groups.map((g) => {
    const items = others.filter((c) => c.group === g);
    return items.length ? el("section", { class: "source-group", "data-group": g }, el("h3", { class: "section" }, g, el("span", { class: "count-badge" }, String(items.length))),
      el("div", { class: "tiles" }, items.map((c) => sourceTile(c, () => pick(c.type))))) : null;
  }).filter(Boolean);
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
  show(page("Add data", "Upload files, or connect a database, cloud storage or an application. Every source is only read, never changed.",
    [btn("Back to data", () => go(() => viewProject(pid, "setup", "data")), "", "prev")],
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Data", P(pid) + "/setup/data"), "Add data")),
  el("h2", { class: "section" }, "Upload files"), uploadZone(pid),
  el("h2", { class: "section" }, "Or connect a database, storage or an application"),
  el("p", { class: "hint" }, others.length + " kinds of source. Passwords are never typed here: they stay in environment variables on the server."),
  el("div", { class: "row source-search-row" }, icon("search"), q), legend, none, sections);
}
// The Data step: a summary of the project's data (sources, records, ready or not, what to do next), then one card per data source: what
// it holds, what its records can be matched on and how full each of those fields is, and any problem with it. Files still to map come first.
// `next`: the step after the data ({ text, open }), shown once the data is ready.
const MATCH_ON = ["full_name", "dob", "age", "mobile", "mobile_alt", "email", "address", "pincode", "pan", "aadhaar", "aadhaar_last4", "aadhaar_reference_key",
  "passport_no", "dl_no", "voter_id", "customer_number"];
const share = (n, d) => (d ? Math.round((100 * n) / d) : 0);
async function projectConnections(p, connections, next) {
  const pid = p.id;
  const manage = can("sources.manage");
  const uploads = manage ? await api("GET", P(pid) + "/uploads") : [];
  const unmapped = uploads.filter((u) => !u.used_by.length).reverse();          // oldest first, the order they are mapped in
  const own = can("config.read") ? (await api("GET", P(pid) + "/rules-catalogue").catch(() => ({ own_fields: [] }))).own_fields : [];
  const removeUpload = async (u) => {
    if (!(await confirmDialog("Delete " + u.filename + "?", "The uploaded file is erased for good. No data source uses it.", "Delete file", true))) return;
    await api("DELETE", P(pid) + "/uploads/" + u.upload_id);
    await go(() => viewProject(pid, "setup", "data"));
    say(u.filename + " was deleted.", "good");
  };
  const add = manage ? btn("Add data", () => go(() => addDataPage(pid)), connections.length || unmapped.length ? "" : "primary", "plus") : null;
  const more = manage ? moreMenu([cache.projects.length > 1 ? ["Copy a data source from another project", () => copyDialog(pid)] : null]) : null;
  const used = uploads.filter((u) => u.used_by.length);
  if (!connections.length && !unmapped.length) {
    return el("div", {}, el("div", { class: "title-row" }, el("h2", { class: "section" }, "Data sources"), el("div", { class: "row tight" }, more)),
      empty(manage ? "No data yet. Upload files or connect a database; the data belongs to this project only." : "No data yet. Someone who manages data adds it.", add));
  }
  const fileCards = unmapped.map((u) => el("article", { class: "source-card todo", "aria-label": u.filename },
    el("div", { class: "source-card-head" }, logo("upload", "big"), el("div", { class: "grow" }, el("strong", {}, u.filename),
      el("div", { class: "muted" }, "Uploaded file. " + plural(u.rows, "row", "rows") + ", " + plural(u.columns.length, "column", "columns") + ", uploaded " + when(u.created_at) + ".")),
    tag("Needs mapping", "warn")),
    el("p", { class: "source-card-line" }, "Choose what each column holds; a run can read the file after that."),
    el("div", { class: "row tight" }, btn("Map it", () => go(() => mapUpload(pid, u.upload_id)), "primary small", "columns"),
      moreMenu([["Delete file", () => removeUpload(u), true]], u.filename))));
  const cards = connections.map((c) => sourceCard(pid, c, own, manage));
  return el("div", {}, dataSummary(pid, connections, unmapped, next),
    el("div", { class: "title-row" }, el("h2", { class: "section" }, "Data sources"), el("div", { class: "row tight" }, add, more)),
    el("div", { class: "source-cards" }, fileCards, cards),
    connections.length > 1 ? el("details", { class: "advanced" }, el("summary", {}, "Compare sources: which field comes from which column"), coverage(connections, own)) : null,
    used.length ? el("details", { class: "advanced" }, el("summary", {}, "Uploaded files (" + used.length + ")"),
      table(["File", ["Rows"], "Read by", "Uploaded", "By"], used.map((u) => el("tr", {}, el("td", {}, u.filename), el("td", { class: "num" }, num(u.rows)),
        el("td", {}, u.used_by.join(", ")), el("td", {}, when(u.created_at)), el("td", {}, u.uploaded_by))))) : null);
}
// The strip at the top of the Data step: how many sources and records, and whether a run can start (or what is missing).
function dataSummary(pid, connections, unmapped, next) {
  const inUse = connections.filter((c) => c.live_version);
  const waiting = connections.filter((c) => c.latest_status === "proposed" || c.latest_status === "approved");
  const counted = connections.filter((c) => c.records != null);
  const total = counted.reduce((a, c) => a + c.records, 0);
  const failing = connections.filter((c) => c.last_check && !c.last_check.ok);
  const them = (n) => (n === 1 ? "it" : "them");
  let line, kind;
  if (unmapped.length) [line, kind] = [plural(unmapped.length, "uploaded file needs", "uploaded files need") + " mapping before a run can read " + them(unmapped.length) + ".", "warn"];
  else if (!inUse.length) [line, kind] = [waiting.length ? "Not ready yet: " + plural(waiting.length, "data source waits", "data sources wait") + " to be approved and put in use." : "Not ready yet: no data source is in use.", "warn"];
  else if (failing.length) [line, kind] = ["The last check could not read " + failing.map((c) => c.id).join(", ") + ": open " + them(failing.length) + " to see why.", "bad"];
  else [line, kind] = ["Ready to match: " + (inUse.length === connections.length ? (inUse.length === 1 ? "the data source is" : inUse.length === 2 ? "both data sources are" : "all " + inUse.length + " data sources are") + " in use"
    : plural(inUse.length, "of " + connections.length + " data sources is", "of " + connections.length + " data sources are") + " in use") +
    (waiting.length ? "; " + plural(waiting.length, "change waits", "changes wait") + " for approval" : "") + ".", "good"];
  return el("section", { class: "data-summary " + kind, "aria-label": "Summary of the data" },
    el("div", { class: "data-summary-stats" },
      el("div", {}, el("span", { class: "value" }, num(connections.length + unmapped.length)), el("span", { class: "label" }, connections.length + unmapped.length === 1 ? "data source" : "data sources")),
      el("div", {}, el("span", { class: "value" }, counted.length ? num(total) : "-"), el("span", { class: "label" }, "records" +
        (counted.length && counted.length < connections.length ? " (" + (connections.length - counted.length) + " not counted yet)" : !counted.length ? " (counted by the first run)" : ""))),
      el("div", {}, el("span", { class: "value" }, num(inUse.length)), el("span", { class: "label" }, "in use by runs"))),
    el("div", { class: "data-summary-line" }, icon(kind === "good" ? "check" : "alert"), el("span", {}, line),
      kind === "good" && next ? btn(next.text, () => run(next.open), "primary small", "next") : null));
}
// One data source as a card: kind, records, state; the fields its records are matched on (with how full each is, read from a sample of the
// source when the page opens) and the other things it gives; problems in plain words, each with what to do.
function sourceCard(pid, c, own, manage) {
  const fields = c.fields || {};
  const mine = c.own_fields || {};
  const kept = (c.extra_columns || []).filter((a) => !(a in mine));
  const known = (n) => own.find((f) => f.name === n);
  const state = c.live_version ? (c.latest_status === "active" ? tag("In use", "success") : el("span", {}, tag("In use", "success"), " ", statusTag(c.latest_status)))
    : statusTag(c.latest_status === "active" ? "proposed" : c.latest_status);
  const chip = (key, title, from) => el("span", { class: "chip", "data-key": key, title: "From " + from }, el("span", {}, title), el("span", { class: "chip-fill" }));
  const chips = [...MATCH_ON.filter((f) => fields[f]).map((f) => chip("field:" + f, fieldTitle(f), fields[f])),
    ...Object.keys(mine).map((n) => chip("own:" + n, known(n) ? known(n).title : label(n), mine[n]))];
  const others = CANON_ORDER.filter((f) => fields[f] && !MATCH_ON.includes(f)).concat(Object.keys(fields).filter((f) => !CANON_ORDER.includes(f)));
  const health = el("ul", { class: "health" });
  const problems = [];
  const note = (kind, text) => el("li", { class: "is-" + kind }, icon(kind === "ok" ? "check" : kind === "info" ? "info" : "alert"), el("span", {}, text));
  if (c.last_check && !c.last_check.ok) problems.push(note("bad", "The last check could not read it: " + (c.last_check.error || "unknown error") + ". Open it and press Test now once it is fixed."));
  const gone = c.last_check && c.last_check.ok ? (c.last_check.drift || {}).removed || [] : [];
  if (gone.length) problems.push(note("warn", "Columns gone from the source since the check before: " + gone.join(", ") + ". Check the mapping."));
  for (const n of Object.keys(mine)) if (!known(n)) problems.push(note("warn", label(n) + " is not a field of this project: its values are only kept, not matched on. Add it under Rules, Fields and search."));
  if (!c.live_version) problems.push(note("info", c.latest_status === "declined" ? "Its latest version was declined: open it to see why." : "Not in use yet: runs read it once it is approved and put in use."));
  if (!chips.length) problems.push(note("bad", "Nothing to match on: map at least a name, an identifier, a phone, an email or a field of your own."));
  fill(health, problems);
  const facts = el("div", { class: "muted" }, typeTitle(c.type) + ". " + (c.records != null ? plural(c.records, "record", "records") + (c.records_from === "run" ? " (read by the last run)" : "")
    : "Records are counted by the first run") + ". Changed " + when(c.updated_at) + (c.updated_by ? " by " + c.updated_by : "") + ".");
  const checked = el("div", { class: "muted small" });
  const card = el("article", { class: "source-card", "aria-label": c.id },
    el("div", { class: "source-card-head" }, logo(c.type, "big"), el("div", { class: "grow" }, el("button", { type: "button", class: "link source-name", onclick: () => go(() => viewConnection(pid, c.id)) }, c.id), facts),
      state),
    el("div", { class: "source-card-row" }, el("span", { class: "source-card-label" }, "Matched on"), chips.length ? el("div", { class: "chips" }, chips) : el("span", { class: "muted" }, "Nothing yet")),
    others.length ? el("div", { class: "source-card-row" }, el("span", { class: "source-card-label" }, "Also gives"), el("span", {}, others.map(fieldTitle).join(", "))) : null,
    kept.length ? el("div", { class: "source-card-row" }, el("span", { class: "source-card-label" }, "Kept as is"), el("span", {}, kept.join(", "))) : null,
    health, checked,
    el("div", { class: "row tight" }, btn("Open", () => go(() => viewConnection(pid, c.id)), "small"),
      manage ? btn("Edit mapping", () => run(() => editConnection(pid, c.id)), "small", "columns", { "aria-label": "Edit the mapping of " + c.id }) : null));
  if (manage && chips.length) {          // how full each field is, from a sample: filled in when it arrives, the card is usable before
    fill(checked, "Checking how full each field is...");
    api("GET", P(pid) + "/connections/" + c.id + "/profile").then((pr) => {
      if (!pr.ok) { fill(checked); add(health, note("bad", "It could not be read just now: " + pr.error + ".")); return; }
      const n = pr.sample_size;
      const by = Object.fromEntries(pr.columns.map((x) => [(x.group === "field" ? "field:" : x.group === "own" ? "own:" : "kept:") + x.key, x.filled]));
      const found = [];
      let strong = 0;
      for (const ch of card.querySelectorAll(".chip")) {
        const f = by[ch.dataset.key];
        if (f == null) continue;
        const pc = share(f, n);
        ch.querySelector(".chip-fill").textContent = pc + "%";
        const title = ch.firstChild.textContent;
        if (!f) { ch.classList.add("is-empty"); found.push(note("bad", title + " has no value in the " + plural(n, "row", "rows") + " read: is the right column mapped?")); }
        else if (pc < 50) { ch.classList.add("is-low"); found.push(note("warn", title + " is filled in only " + pc + "% of rows: records without it are matched on the other fields.")); }
        if (pc >= 50) strong++;
      }
      for (const k of kept) if (by["kept:" + k] === 0) found.push(note("warn", "The kept column " + k + " has no value in the rows read."));
      if (!strong && chips.length) found.push(note("warn", "Little to match on: no field is filled in at least half of the rows."));
      if (!found.length && !problems.length) found.push(note("ok", "Looks good: every field it is matched on is filled in most rows."));
      add(health, found);
      fill(checked, n >= 1000 ? "Checked on the first " + num(n) + " rows." : "Checked on all " + plural(n, "row", "rows") + ".");
    }).catch((e) => fill(checked, /too many/i.test(e.message) ? "Not checked: many sources were read in the last minute. Open the page again shortly." : "Not checked: " + e.message));
  }
  return card;
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
      which.append(...list.filter((c) => c.type !== "upload").map((c) => el("option", { value: c.id }, c.id + " (" + typeTitle(c.type) + ")")));
      fail(err, which.children.length ? "" : "That project has no data source that can be copied (uploaded files stay with their project).");
    } catch (e) { fail(err, e.message); }
  };
  from.addEventListener("change", load);
  await load();
  const done = await dialog("Copy a data source", el("div", {}, el("p", { class: "hint" }, "The copy is independent: later changes to either one do not affect the other. It starts as a proposal in this project."),
    field("From project", from), field("Data source", which), field("New id here (leave empty to keep the same)", newId), err), "Copy", async () => {
    try {
      return await api("POST", P(pid) + "/connections/copy", { from_project: from.value, connection_id: which.value, new_id: newId.value.trim() || null });
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (done) { await go(() => viewConnection(pid, done.id)); say("Copied as version " + done.version + ". Check the mapping, then put it in use.", "good"); }
}
// Which of our fields each connection fills, and from which of its columns: the whole project's data at a glance.
const CANON_ORDER = ["full_name", "father_name", "dob", "age", "gender", "marital_status", "mobile", "mobile_alt", "email", "address", "address_alt", "city", "state", "pincode",
  "customer_number", "pan", "aadhaar", "aadhaar_last4", "aadhaar_reference_key", "passport_no", "dl_no", "voter_id", "record_date", "occupation", "annual_income", "related_name",
  "party_role", "relation_to_proposer"];
function coverage(connections, ownFields) {
  const ownTitle = (f) => ((ownFields || []).find((x) => x.name === f) || { title: label(f) }).title;
  const used = CANON_ORDER.filter((f) => connections.some((c) => (c.fields || {})[f]));
  const own = [...new Set(connections.flatMap((c) => Object.keys(c.own_fields || {})))];
  if (!used.length && !own.length) return null;
  const extra = connections.filter((c) => (c.extra_columns || []).length);
  return el("div", {},
    el("p", { class: "hint" }, "Each column shows the column of that source that fills the field (latest version). Records of all sources are matched with each other on these fields."),
    table(["Field", ...connections.map((c) => c.id)], [
      ...used.map((f) => el("tr", {}, el("td", {}, fieldTitle(f)), connections.map((c) => el("td", {}, (c.fields || {})[f] ? el("span", { class: "mono" }, c.fields[f]) : el("span", { class: "muted" }, "-"))))),
      ...own.map((f) => el("tr", {}, el("td", {}, ownTitle(f) + " (your own)"), connections.map((c) => el("td", {}, (c.own_fields || {})[f] ? el("span", { class: "mono" }, c.own_fields[f]) : el("span", { class: "muted" }, "-"))))),
      el("tr", {}, el("td", {}, "Record id"), connections.map((c) => el("td", {}, c.record_id ? el("span", { class: "mono" }, c.record_id) : el("span", { class: "muted" }, "row number")))),
      extra.length ? el("tr", {}, el("td", {}, "Extra columns kept"), connections.map((c) => el("td", {}, (c.extra_columns || []).join(", ") || el("span", { class: "muted" }, "-")))) : null,
    ]));
}
async function editConnection(pid, cid) {
  const c = await api("GET", P(pid) + "/connections/" + cid);
  await connectionWizard(pid, c.latest.connector.type, JSON.parse(JSON.stringify(c.latest)), 2);
}
// Where a field's value comes from, in words: "first_name + last_name (joined)", "mobile, else landline", "doc_no where doc_type is PAN".
function exprWords(e) {
  if (typeof e === "string") return e;
  if (!e || typeof e !== "object") return String(e);
  if (e.concat) return e.concat.join(" + ") + " (joined)";
  if (e.coalesce) return e.coalesce.join(", else ");
  if (e.const !== undefined) return "always \u201c" + e.const + "\u201d";
  if (e.column) return e.column + (e.when ? " where " + Object.entries(e.when).map(([k, v]) => k + " is " + (Array.isArray(v) ? v.slice(0, 3).join(" or ") : v)).join(" and ") : "");
  return JSON.stringify(e);
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
    can("config.activate") && (v.status === "proposed" || v.status === "approved") && (!second || v.approved_by.length) ? btn("Put in use", () => act(v, "activate", "Put version " + v.version + " in use", 0, "Note"), "small primary") : null,
    second && v.status === "proposed" && mine(v) ? tag("Yours: someone else approves it", "warn") : null,
  ].filter(Boolean);
  const changes = pending && live ? await api("GET", base + "/diff?old=" + live.version + "&new=" + latest.version) : null;
  const shown = c.live || c.latest;
  // what the source gives: each of our fields and the column it comes from; everything technical under Details
  const own = can("config.read") ? (await api("GET", P(pid) + "/rules-catalogue").catch(() => ({ own_fields: [] }))).own_fields : [];
  const mapRows = CANON_ORDER.filter((f) => (shown.fields || {})[f]).concat(Object.keys(shown.fields || {}).filter((f) => !CANON_ORDER.includes(f)))
    .map((f) => el("tr", {}, el("td", {}, fieldTitle(f)), el("td", { class: "mono" }, exprWords(shown.fields[f]))))
    .concat(Object.entries(shown.custom || {}).map(([f, col]) => {
      // a field this project does not define (the source was copied from another project, or the field was removed): its values are only kept
      const known = own.find((x) => x.name === f);
      return el("tr", {}, el("td", {}, known ? known.title + " (your own field)" : el("span", {}, label(f) + " ", tag("Not a field of this project", "warn"),
        el("div", { class: "muted small" }, "Its values are only kept, not matched on. Add the field under Rules, Fields and search, or map the column again."))),
        el("td", { class: "mono" }, exprWords(col)));
    }));
  const settings = Object.entries(shown.connector).filter(([k]) => k !== "type").map(([k, v]) => [label(k), el("span", { class: "mono" }, String(v))]);
  const test = can("sources.manage") ? btn("Test now", async () => {
    const t = await api("POST", base + "/test");
    await go(() => viewConnection(pid, cid));
    say(t.ok ? "The source reads fine (" + num(t.rows) + " sample rows). " + driftText(t.drift) : "The source could not be read: " + t.error, t.ok ? "good" : "bad");
  }, "small", "refresh") : null;
  const versions = table(["Version", "Status", "Proposed by", "Reason", "Approved by", ""], c.versions.slice().reverse().map((v) => el("tr", {}, el("td", {}, "Version " + v.version),
    el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by), el("td", {}, v.reason), el("td", {}, v.approved_by.join(", ")), el("td", { class: "actions-cell" }, buttons(v)))));
  const lastCheck = c.health[0];
  show(page(cid, typeTitle(shown.connector.type) + ". " + (live ? "In use: runs read it." : "Not in use yet: runs do not read it."),
    can("sources.manage") ? [btn("Change", () => run(() => editConnection(pid, cid)), "", "edit")] : [],
    crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Data", P(pid) + "/setup/data"), cid), null,
    logo(shown.connector.type, "big")),
  declinedNotice(c.versions, live),
  pending ? el("div", { class: "notice", role: "note" }, el("strong", {}, (live ? "A change" : "This data source") + " waits to be put in use. "),
    live ? (changes && changes.length ? "Compared with what is in use: " + changes.join("; ") + "." : "It reads the same as what is in use.") : "",
    el("div", { class: "row" }, buttons(latest))) : null,
  lastCheck && !lastCheck.ok ? el("div", { class: "notice bad", role: "note" }, "The last check could not read this source: " + (lastCheck.error || "unknown error") + ".") : null,
  el("h2", { class: "section" }, "Fields it gives"),
  mapRows.length ? table(["Field", "From column"], mapRows)
    : empty("No field is mapped yet: runs cannot match its records.", can("sources.manage") ? btn("Map the columns", () => run(() => editConnection(pid, cid)), "primary", "columns") : null),
  el("p", { class: "hint" }, "Each row's own id: " + (shown.record_id || "the row number") + "." + ((shown.attributes || []).length ? " Extra columns kept: " + shown.attributes.join(", ") + "." : "")),
  el("details", { class: "advanced" }, el("summary", {}, "Details: settings, versions and checks"),
    el("h3", { class: "section" }, "Settings"), settings.length ? kv(settings) : el("p", { class: "muted" }, "None."),
    el("h3", { class: "section" }, "Versions"), versions,
    el("div", { class: "title-row" }, el("h3", { class: "section" }, "Checks"), test),
    c.health.length ? table(["When", "Check", "Version", "Result", ["Rows"], "Changes in the source", "By"], c.health.map((h) => el("tr", {}, el("td", {}, when(h.checked_at)),
      el("td", {}, h.kind === "run" ? "A run" : "Test now"), el("td", {}, h.version == null ? "" : "Version " + h.version), el("td", {}, h.ok ? tag("OK", "success") : tag("Failed", "danger")),
      el("td", { class: "num" }, h.rows == null ? "" : num(h.rows)), el("td", {}, h.ok ? driftText(h.drift) : (h.error || "")), el("td", {}, h.checked_by))))
      : el("p", { class: "muted" }, "Not checked yet. Press Test now, or start a run.")));
}
const driftText = (d) => ((d && ((d.added || []).length || (d.removed || []).length))
  ? [(d.added || []).length ? "Added: " + d.added.join(", ") : "", (d.removed || []).length ? "Removed: " + d.removed.join(", ") : ""].filter(Boolean).join(". ") : "");

// A column whose name is like an own field's name or title: "GST No" and "gstin", "Annual turnover" and "turnover". Words such as "name" or
// "no" say nothing on their own and are left out.
const NAME_NOISE = new Set(["name", "no", "num", "number", "id", "code", "the", "of", "and", "value"]);
const nameWords = (s) => String(s).replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 3 && !NAME_NOISE.has(w));
const namesAlike = (col, f) => {
  const a = nameWords(col);
  const b = [...nameWords(f.name), ...nameWords(f.title || "")];
  return a.some((x) => b.some((y) => x.startsWith(y) || y.startsWith(x)));
};
// The add-a-connection wizard: 1 connect (or upload), 2 map the columns, 3 preview and save. Every step can be clicked once the source is open.
// Our fields in words, and what each is used for, shown next to it while mapping.
const FIELD_TITLE = { full_name: "Full name", father_name: "Father's or husband's name", dob: "Date of birth", age: "Age", gender: "Gender", marital_status: "Marital status",
  mobile: "Mobile", mobile_alt: "Second phone", email: "Email", address: "Address", address_alt: "Second address", city: "City", state: "State", pincode: "PIN code",
  customer_number: "Customer number (shared)", pan: "PAN", aadhaar: "Aadhaar number", aadhaar_last4: "Aadhaar last 4 digits", aadhaar_reference_key: "Aadhaar vault reference", aadhaar_token: "Aadhaar (kept as a token)",
  passport_no: "Passport number", dl_no: "Driving licence", voter_id: "Voter ID", record_date: "Record date", occupation: "Occupation", annual_income: "Annual income",
  related_name: "Related person's name", party_role: "Role on the record", relation_to_proposer: "Relation to the proposer" };
const fieldTitle = (f) => FIELD_TITLE[f] || label(f);
const FIELD_ROLE = {
  full_name: ["Matching", "compared on every pair: spelling, word order, initials"], father_name: ["Golden record", "and to tell relatives apart"],
  dob: ["Matching", "a strong sign when it agrees"], age: ["Matching", "used when there is no birth date"], gender: ["Matching", "a difference stops a merge"],
  marital_status: ["Golden record", ""], mobile: ["Matching", ""], mobile_alt: ["Matching", ""], email: ["Matching", ""],
  address: ["Matching", "with the PIN code"], address_alt: ["Households", ""], city: ["Matching", ""], state: ["Golden record", ""], pincode: ["Matching", ""],
  customer_number: ["Identifier", "use it in a matching rule if it is shared across your systems"], pan: ["Identifier", "strong evidence"],
  aadhaar: ["Identifier", "kept only as a keyed token, never stored"], aadhaar_last4: ["Identifier", "weak: last four digits only"],
  aadhaar_reference_key: ["Identifier", "your Aadhaar Data Vault key"], passport_no: ["Identifier", "strong evidence"], dl_no: ["Identifier", "strong evidence"],
  voter_id: ["Identifier", "strong evidence"], record_date: ["Newest value", "decides which value is the most recent"], occupation: ["Golden record", ""],
  annual_income: ["Golden record", ""], related_name: ["Households", "links family members"], party_role: ["Households", ""], relation_to_proposer: ["Households", ""],
};
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
// `batch`: several files uploaded at once ({ files: [{ upload_id, id, label, saved }], index, memo: { reason } }); each becomes its own connection.
// A bar lists the files: any can be opened (a saved one with its saved mapping); Save moves on to the next file not saved yet. The reason is
// asked once for the whole upload.
async function connectionWizard(pid, type, preset, startStep, batch) {
  const opts = await catalogue();
  const spec = opts.catalogue.find((c) => c.type === type || c.aliases.includes(type));
  const root = P(pid);
  const state = { source: preset || { source_id: "", connector: { type }, record_id: null, fields: {}, attributes: [] }, info: null, step: 1, uploadName: null,
    sample: null, reason: null };
  if (batch && !preset) {
    const f = batch.files[batch.index];
    state.source = { source_id: f.id, connector: { type: "upload", upload_id: f.upload_id }, record_id: null, fields: {}, attributes: [] };
    state.uploadName = f.label;
  }
  if (preset) preset.attributes = preset.attributes || [];
  // the project's own fields (Fields and search): a column can fill one of them, or become a new one
  let ownFields = [];
  try { if (can("config.read")) ownFields = (await api("GET", root + "/rules-catalogue")).own_fields; } catch (_) { /* mapped without them */ }
  const ownName = (n) => (ownFields.find((f) => f.name === n) || { title: label(n) }).title;
  if (batch && !preset) here(root + "/map/" + batch.files[batch.index].upload_id);       // Back and reload come back to this file
  const original = preset ? JSON.stringify(preset) : null;            // opened again unchanged: Save only moves on
  const ofBatch = batch && batch.files.length > 1 ? " (file " + (batch.index + 1) + " of " + batch.files.length + ")" : "";
  const unsaved = () => (batch ? batch.files.filter((f) => !f.saved).length : 0);
  const openFile = (i) => go(async () => {
    const f = batch.files[i];
    const preset2 = f.saved ? JSON.parse(JSON.stringify((await api("GET", root + "/connections/" + f.id)).latest)) : null;
    await connectionWizard(pid, "upload", preset2, 2, { ...batch, index: i });
  });
  const fileBar = () => (batch && batch.files.length > 1 ? el("nav", { class: "subtabs page-sub", "aria-label": "Uploaded files" },
    batch.files.map((f, i) => el("button", { type: "button", "aria-current": i === batch.index ? "true" : null, "aria-selected": i === batch.index ? "true" : "false",
      onclick: () => (i === batch.index ? null : openFile(i)) }, (i + 1) + ". " + f.id + (f.saved ? " (saved)" : i === batch.index ? "" : " (not saved yet)")))) : null);
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
    const header = page(batch ? "Add data: " + state.source.source_id + ofBatch : preset ? "Change " + state.source.source_id : "Add data: " + (spec ? spec.title : typeTitle(type)),
      spec ? spec.summary : "", [btn(batch && batch.files.length > 1 ? "Stop here" : "Cancel", () => go(async () => {
        await (batch ? viewProject(pid, "setup", "data") : preset ? viewConnection(pid, state.source.source_id) : addDataPage(pid));
        if (unsaved()) say(plural(unsaved(), "file is", "files are") + " not saved yet. They wait on this page as Needs mapping: map them later, or delete them if they are not needed.", "good");
      }))],
      crumbs(crumb("Projects", "/projects"), crumb(projectName(pid), P(pid)), crumb("Data", P(pid) + "/setup/data"), preset ? state.source.source_id : "Add data"),
      null, logo(kind(), "big"));
    if (state.step === 1) show(header, fileBar(), bar, connectStep());
    else if (state.step === 2) show(header, fileBar(), bar, mapStep());
    else show(header, fileBar(), bar, previewStep(), out);
  };
  const idField = () => {
    const i = el("input", { type: "text", value: state.source.source_id, maxlength: "40", placeholder: "For example bank_crm", disabled: preset ? true : null });
    i.addEventListener("input", () => { state.source.source_id = i.value.trim(); });
    return field("Name of this data source (stamped on every record it delivers: letters, digits, _ and -)", i);
  };
  const inspectUrl = () => root + "/connections/inspect" + (state.reason ? "?" + unmaskQuery(state.reason) : "");
  const openSource = async (target) => {
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(state.source.source_id)) throw new Error("Give the data source a name of letters, digits, _ or - (no spaces).");
    if (kind() === "upload" && !state.source.connector.upload_id) throw new Error("Upload a file first.");
    if (!preset && !batch && (await api("GET", root + "/connections")).some((c) => c.id === state.source.source_id)) {
      throw new Error("This project already has a data source called " + state.source.source_id + ". Choose another name, or open that one to change it.");
    }
    state.info = await api("POST", inspectUrl(), { connector: state.source.connector });
    if (!Object.keys(state.source.fields).length && !Object.keys(state.source.custom || {}).length) {
      for (const s of state.info.suggestions) state.source.fields[s.field] = s.column;
      // the project's own fields: a column whose values look like what a field holds fills it (company names -> the company field)
      const looks = { organisation_name: ["company"], person_name: ["name"], email: ["email"], mobile: ["phone"], gstin: ["code"] };
      const used = new Set([...Object.values(state.source.fields), state.info.record_id]);
      state.source.custom = {};
      const cols = state.info.columns || Object.keys(state.info.types || {});
      for (const f of ownFields) {
        const free = cols.filter((c) => !used.has(c));
        const col = free.find((c) => (looks[((state.info.types || {})[c] || {}).type] || []).includes(f.kind)) || free.find((c) => namesAlike(c, f));
        if (col) { state.source.custom[f.name] = col; used.add(col); }
      }
      if (!state.source.record_id && state.info.record_id) state.source.record_id = state.info.record_id;
    }
    state.step = target || 2;
    render();
  };
  // Save: the mapping as a new version (put in use at once in a one-person project); with several files, the next one not saved opens
  const save = async () => {
    const many = batch && batch.files.length > 1;
    const proj = await api("GET", P(pid));               // the setting as it is now, not as the list was loaded (U1-10)
    const solo = proj && !proj.second_approval;          // nobody else approves: the reason is recorded without asking
    const again = preset || (batch && batch.files[batch.index].saved);
    let why = solo ? (again ? "Changed the mapping of " : "Added from ") + (kind() === "upload" ? "uploaded file " : "source ") + state.source.source_id
      : batch ? batch.memo.reason : null;
    if (!why) {
      why = await askReason(many ? "Save the uploaded files" : "Save " + state.source.source_id,
        many ? "Why add these " + batch.files.length + " files? (asked once for all of them)" : "Why add or change it?", 10, "Save");
      if (why === null) return;
      if (batch) batch.memo.reason = why;
    }
    const me1 = batch ? batch.files[batch.index] : null;
    let res = null;
    if (!(me1 && me1.saved && JSON.stringify(state.source) === original)) {
      try { res = await api("POST", root + "/connections", { connection: state.source, reason: why }); }
      catch (e) { if (!(me1 && me1.saved && /identical/i.test(e.message))) throw e; }    // the same content in another key order
    }
    let saved = "No change to " + state.source.source_id + ".";
    if (res) {
      const now = !res.second_approval && can("config.activate");          // one-person project: saved means in use
      if (now) await api("POST", root + "/connections/" + res.id + "/versions/" + res.version + "/activate", { reason: why });
      if (me1) me1.saved = true;
      saved = "Saved " + res.id + " as version " + res.version + (now ? " and in use: runs read it from now on." : res.second_approval ? ". Another person approves it and puts it in use; then runs can read it."
        : ". Someone who can put data sources in use does that next.");
    }
    if (many) {
      const n = batch.files.length;
      const next = [...Array(n).keys()].map((k) => (batch.index + 1 + k) % n).find((i) => !batch.files[i].saved);
      if (next !== undefined) {
        await openFile(next);
        say(saved + " Next: file " + (next + 1) + " of " + n + ".", "good");
        return;
      }
      await go(() => viewProject(pid, "setup", "data"));
      say(saved + " All " + n + " files are saved.", "good");
      return;
    }
    await go(() => viewProject(pid, "setup", "data"));          // back to the data, where the new source's card says how full its fields are
    say(saved, "good");
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
      if (state.source.connector.upload_id) parts.push(el("p", {}, "Uploaded " + (state.uploadName || state.source.connector.upload_id) + "."));
      else parts.push(uploadZone(pid, () => state.source.source_id));
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
  // One row per column of the source: what its values look like and what it is used as (one of our fields, the row's own id, an extra
  // column kept in the golden record, or nothing). Suggestions from the values are filled in already; the sample rows are folded away.
  function mapStep() {
    const cols = state.info.columns;
    const sample = state.info.sample;
    const types = state.info.types || {};
    const src = state.source;
    src.attributes = src.attributes || [];
    src.custom = src.custom || {};
    const ownOf = (c) => Object.keys(src.custom).find((f) => src.custom[f] === c);
    const examples = (c) => [...new Set(sample.map((r) => r[c]).filter((v) => v != null && v !== ""))].slice(0, 3);
    // the columns a field reads: one column, several joined ("first + last name") or several as fallbacks ("mobile, else phone")
    const partsOf = (e) => (typeof e === "string" ? [e] : e && (e.concat || e.coalesce) ? (e.concat || e.coalesce) : null);
    const fieldOf = (c) => Object.keys(src.fields).find((f) => (partsOf(src.fields[f]) || []).includes(c));
    // one column of document numbers whose type (PAN, Aadhaar, passport ...) is in another column: each type fills its own field (U2-15)
    const isDoc = (e) => e && typeof e === "object" && e.when && e.column;
    const docsOf = (c) => Object.entries(src.fields).filter(([, e]) => isDoc(e) && e.column === c);
    const wantDocs = new Set();                            // chosen as document numbers, type column not found yet
    const useOf = (c) => (fieldOf(c) ? "f:" + fieldOf(c) : ownOf(c) ? "o:" + ownOf(c) : docsOf(c).length || wantDocs.has(c) ? "docs" : src.record_id === c ? "id" : src.attributes.includes(c) ? "keep" : "");
    const combined = Object.keys(src.fields).filter((f) => !partsOf(src.fields[f]) && !isDoc(src.fields[f]));
    const seen = (col) => [...new Set(sample.map((r) => r[col]).filter((v) => v != null && String(v).trim() !== "").map((v) => String(v).trim()))];
    const docField = (v) => DOC_TYPES.find(([re]) => re.test(v));
    // the usual spellings of every type, plus the ones the sample shows: a type missing from the first rows still counts (Aadhaar rows further down)
    const setDocs = (c, typeCol) => {
      for (const [f] of docsOf(c)) delete src.fields[f];
      const by = Object.fromEntries(DOC_TYPES.map(([, f, usual]) => [f, [...usual]]));
      for (const v of seen(typeCol)) { const d = docField(v); if (d && !by[d[1]].includes(v.toLowerCase())) by[d[1]].push(v.toLowerCase()); }
      for (const [f, vals] of Object.entries(by)) if (typeof src.fields[f] !== "string") src.fields[f] = { column: c, when: { [typeCol]: vals } };
    };
    // a field that already comes from another column: join the two, use this one as a fallback, or use only this one (asked, never silent)
    const JOINABLE = ["full_name", "father_name", "address", "address_alt", "related_name", "occupation"];
    const combineAsk = async (c, f) => {
      const cur = partsOf(src.fields[f]);
      if (!src.fields[f] || !cur || cur.includes(c)) return "replace";
      const join = JOINABLE.includes(f);
      return dialog(fieldTitle(f) + " already comes from " + cur.join(" + "), el("p", {}, join
        ? "Join the columns into one value (for example a first name and a last name, in this order: " + [...cur, c].join(", ") + "), or use only " + c + "."
        : "Use " + c + " when " + cur.join(", ") + " is empty, or use only " + c + " (" + cur.join(", ") + " is then not used)."),
      join ? "Join them" : "Use as a fallback", async () => "combine", false, ["Use only " + c, "replace"]);
    };
    const setUse = (c, use, how) => {
      for (const f of Object.keys(src.fields)) {
        const ps = partsOf(src.fields[f]);
        if (ps && ps.includes(c)) {
          const rest = ps.filter((x) => x !== c);
          if (!rest.length) delete src.fields[f];
          else src.fields[f] = rest.length === 1 ? rest[0] : src.fields[f].concat ? { ...src.fields[f], concat: rest } : { coalesce: rest };
        } else if (isDoc(src.fields[f]) && src.fields[f].column === c) delete src.fields[f];
      }
      if (src.record_id === c) src.record_id = null;
      src.attributes = src.attributes.filter((x) => x !== c);
      for (const f of Object.keys(src.custom)) if (src.custom[f] === c) delete src.custom[f];
      wantDocs.delete(c);
      if (use.startsWith("f:")) {
        const f = use.slice(2);
        const cur = partsOf(src.fields[f]);
        src.fields[f] = how === "combine" && cur ? (JOINABLE.includes(f) ? { concat: [...cur, c], sep: f.startsWith("address") ? ", " : " " } : { coalesce: [...cur, c] }) : c;
      }
      else if (use.startsWith("o:")) src.custom[use.slice(2)] = c;
      else if (use === "id") src.record_id = c;
      else if (use === "keep") src.attributes.push(c);
      else if (use === "docs") {
        const typeCol = cols.find((x) => x !== c && seen(x).some(docField));
        if (typeCol) setDocs(c, typeCol); else wantDocs.add(c);
      }
      refresh.forEach((r) => r());
    };
    // the columns in three groups, sorted once when the step opens (a row never jumps away while it is being changed): chosen from clear
    // evidence; worth a look, with the reason (the values disagree, the column is empty in the rows read, it was chosen from its name only,
    // it looks like a field not chosen yet or one that already comes from another column, part of a name); not used
    const nameOnly = new Set((state.info.suggestions || []).filter((x) => x.why === "name").map((x) => x.column));
    const NAME_PART = /(^|[^a-z])(first|last|middle|sur|given|family|fore)[ _-]?name|surname|^(fname|lname|mname)$/i;
    const why = (c) => {
      const t = types[c] || {};
      const cur = useOf(c);
      const f = fieldOf(c);
      if (cur) {
        if (f && (t.contradicts || []).includes(f)) return ["check", "The values do not look like " + fieldTitle(f).toLowerCase() + "."];
        if (t.filled === 0) return ["check", "No value in the rows read: keep it only if later rows have values."];
        if (cur === "docs" && !docsOf(c).length) return ["check", "Choose the column that says which document each number is."];
        if (f && nameOnly.has(c) && !preset) return ["check", "Chosen from the column's name; the values were not recognised."];
        return ["done", ""];
      }
      if (t.filled === 0) return ["unused", ""];
      const from = (x) => (partsOf(src.fields[x]) || []).join(" + ");
      if (NAME_PART.test(c) && src.fields.full_name) return ["check", "Part of a name? Choose Full name to join it with " + from("full_name") + "."];
      if (t.field && FIELD_TITLE[t.field]) {
        return ["check", src.fields[t.field] ? "Looks like " + fieldTitle(t.field).toLowerCase() + ", which comes from " + from(t.field) +
          (t.field === "mobile" ? " now: choose Second phone, or Mobile to use it when that one is empty." : " now: choose it to join them or use this one as a fallback.")
          : "Looks like " + fieldTitle(t.field).toLowerCase() + "."];
      }
      if (t.type === "organisation_name") return ["check", "Company names: choose \u201cA new field of your own\u201d to match on them."];
      return ["unused", ""];
    };
    const verdict = Object.fromEntries(cols.map((c) => [c, why(c)]));
    const refresh = [];
    const rows = cols.map((c) => {
      const t = types[c] || {};
      const sel = el("select", { "aria-label": "Use " + c + " as" });
      const note = el("div", { class: "muted small" });
      const first = useOf(c);                                         // the reason to check a column goes once its choice is changed
      const reason = verdict[c][1] ? el("div", { class: "map-reason small" }, verdict[c][1]) : null;
      const draw = () => {
        const cur = useOf(c);
        if (reason) reason.hidden = cur !== first;
        const taken = (f) => (partsOf(src.fields[f]) && !partsOf(src.fields[f]).includes(c) ? " (now from " + partsOf(src.fields[f]).join(" + ") + ")"
          : combined.includes(f) ? " (now several columns combined)" : "");
        fill(sel, el("option", { value: "", selected: cur === "" }, "Don't use"),
          el("option", { value: "id", selected: cur === "id" }, "Each row's own id" + (src.record_id && src.record_id !== c ? " (now " + src.record_id + ")" : "")),
          el("option", { value: "keep", selected: cur === "keep" }, "Keep as an extra column"),
          el("option", { value: "docs", selected: cur === "docs" }, "Identity document numbers (their type is in another column)"),
          opts.field_groups.map((g) => el("optgroup", { label: g.title }, g.fields.map((f) => el("option", { value: "f:" + f, selected: cur === "f:" + f }, fieldTitle(f) + taken(f))))),
          el("optgroup", { label: "Your own fields" },
            [...new Set([...ownFields.map((f) => f.name), ...Object.keys(src.custom)])].map((n) => el("option", { value: "o:" + n, selected: cur === "o:" + n },
              ownName(n) + (typeof src.custom[n] === "string" && src.custom[n] !== c ? " (now from " + src.custom[n] + ")" : ""))),
            can("config.propose") ? el("option", { value: "new" }, "A new field of your own...") : null));
        const f = fieldOf(c);
        const role = f ? FIELD_ROLE[f] || ["Golden record", ""] : null;
        const wrong = f && (t.contradicts || []).includes(f);
        const docs = docsOf(c);
        if (cur === "docs") {
          const typeCol = docs.length ? Object.keys(docs[0][1].when)[0] : "";
          const pick = el("select", { "aria-label": "Column holding the document type for " + c }, el("option", { value: "" }, "Choose the column with the type"),
            cols.filter((x) => x !== c).map((x) => el("option", { value: x, selected: x === typeCol }, x)));
          pick.addEventListener("change", () => { if (pick.value) { setDocs(c, pick.value); if (docsOf(c).length) wantDocs.delete(c); } refresh.forEach((r) => r()); });
          fill(note, el("div", {}, field("Type of each document is in", pick),
            docs.length ? docs.map(([df, e]) => el("div", {}, fieldTitle(df) + " where " + Object.keys(e.when)[0] + " is " + Object.values(e.when)[0].slice(0, 4).join(" or ") +
              (Object.values(e.when)[0].length > 4 ? " (or another usual spelling)" : "")))
              : el("span", { class: "bad" }, "No column of this file says PAN, Aadhaar, passport, driving licence or voter ID: choose the type column.")));
          return;
        }
        const both = f && (partsOf(src.fields[f]) || []).length > 1 ? (src.fields[f].concat ? "Joined: " : "The first filled of: ") + partsOf(src.fields[f]).join(" + ") + ". " : "";
        fill(note, both, wrong ? tag("The values do not look like " + fieldTitle(f).toLowerCase(), "warn")
          : role ? role[0] + (role[1] ? ": " + role[1] : "") : cur === "id" ? "Traces a golden record back to this row; not used to match"
            : cur.startsWith("o:") ? "Your own field: records are matched on it (how is set under Fields and search)"
            : cur === "keep" ? "Carried into the golden record as it is; a matching rule can still use it" : t.field && !src.fields[t.field] ? "Could be " + fieldTitle(t.field).toLowerCase()
              : t.type === "organisation_name" && !cur ? "Could be a field of your own: choose \u201cA new field of your own\u201d, kind Organisation name" : "");
      };
      sel.addEventListener("change", () => run(async () => {
        if (sel.value.startsWith("f:")) {
          const how = await combineAsk(c, sel.value.slice(2));
          if (!how) { draw(); return; }
          setUse(c, sel.value, how);
          return;
        }
        if (sel.value !== "new") { setUse(c, sel.value); return; }
        const f = await newOwnField(pid, c, Object.keys(src.custom));
        if (f) {
          ownFields = [...ownFields, f];
          setUse(c, "o:" + f.name);
          say(f.pending ? "The field " + f.title + " is saved and waits for approval: runs match on it once it is approved and in use (Rules, Fields and search)."
            : "The field " + f.title + " is added. How it is compared can be changed under Rules, Fields and search.", "good");
        }
        else draw();
      }, true));
      refresh.push(draw);
      draw();
      const filled = t.filled != null ? Math.round(t.filled * 100) : null;
      return { c, row: el("tr", {}, el("td", {}, el("strong", { class: "mono" }, c),
        el("div", { class: "muted small" }, (t.type ? t.description : "Not recognised") + (filled != null ? ". " + filled + "% of rows filled" : "")),
        reason),
        el("td", {}, el("span", { class: "examples" }, examples(c).map((v) => el("span", { class: "ex" }, String(v))))),
        el("td", {}, sel, note)) };
    });
    const groups = { done: [], check: [], unused: [] };
    for (const r of rows) groups[verdict[r.c][0]].push(r.row);
    const GROUPS = [["check", "Please check", "These may need your choice: the values disagree with the choice, the column is empty in the rows read, " +
      "or it looks like a field that is not chosen yet."], ["done", preset ? "Mapped" : "Mapped for you", "Chosen from the column names and the values. Change any that are wrong."],
    ["unused", "Not used", "Not read by runs. Keep a column as an extra if you want it in the golden record; a matching rule can then still use it."]];
    const section = ([k, title, help]) => (groups[k].length ? el("section", { class: "map-group", "data-group": k, "aria-label": title },
      el("h3", { class: "section" }, title, el("span", { class: "count-badge" }, String(groups[k].length))), el("p", { class: "hint" }, help),
      table(["Your column", "Examples", "Use as"], groups[k], "map-table")) : null);
    return el("div", { class: "map-layout" },
      el("div", { class: "map-main" },
        el("p", { class: "hint" }, "Say what each column of your file holds. Suggestions are filled in; the ones worth a look are first. A column that fits none of the fields " +
          "(a company name, a GSTIN, a product code) can still be matched on: choose \u201cA new field of your own\u201d."),
        GROUPS.map(section),
        combined.length ? el("p", { class: "hint" }, "Kept as set before (several columns combined): " + combined.map(fieldTitle).join(", ") + ".") : null,
        el("details", { class: "advanced" }, el("summary", {}, "Sample rows (" + sample.length + ", " + (state.info.unmasked ? "actual values" : "values shortened") + ")"),
          el("div", { class: "row" }, revealButton(async () => { state.info = { ...state.info, ...(await api("POST", inspectUrl(), { connector: state.source.connector })) }; render(); })),
          wide(cols, sample.map((r) => el("tr", {}, cols.map((c) => el("td", {}, r[c] == null ? "" : r[c])))))),
        el("div", { class: "row" }, btn("Back", () => goStep(1), "", "prev"))),
      matchPanel(src, types, state.info.sample_size || sample.length, refresh));
  }
  // The side panel of the mapping step: what records of this source can be matched on (each with how full its column is in the rows read),
  // what else it gives, and the buttons. Redrawn whenever a choice changes.
  function matchPanel(src, types, n, refresh) {
    const box = el("div", { class: "map-checklist" });
    const partsOf = (e) => (typeof e === "string" ? [e] : e && (e.concat || e.coalesce) ? (e.concat || e.coalesce) : e && e.column ? [e.column] : []);
    const fullness = (e) => Math.max(0, ...partsOf(e).map((c) => ((types[c] || {}).filled != null ? Math.round(types[c].filled * 100) : 0)));
    const ITEMS = [["Name", "full_name"], ["Birth date or age", "dob", "age"], ["Phone", "mobile", "mobile_alt"], ["Email", "email"], ["Address or PIN code", "address", "pincode"],
      ["PAN", "pan"], ["Aadhaar", "aadhaar", "aadhaar_last4", "aadhaar_reference_key"], ["Passport, licence or voter ID", "passport_no", "dl_no", "voter_id"],
      ["Customer number", "customer_number"]];
    const draw = () => {
      const have = ITEMS.map(([title, ...fs]) => {
        const got = fs.filter((f) => src.fields[f]);
        return { on: got.length > 0, title, pc: got.length ? Math.max(...got.map((f) => fullness(src.fields[f]))) : null };
      });
      const own = Object.entries(src.custom || {}).map(([f, col]) => ({ on: true, title: ownName(f) + " (your own)", pc: fullness(col) }));
      const items = [...have, ...own];
      const on = items.filter((x) => x.on);
      const line = (x) => el("li", { class: x.on ? (x.pc === 0 ? "is-zero" : x.pc < 50 ? "is-low" : "is-on") : "is-off" }, icon(x.on ? "check" : "x"), el("span", { class: "grow" }, x.title),
        x.on ? el("span", { class: "pc" }, x.pc + "%") : el("span", { class: "muted" }, "not in this file"));
      const verdict = !on.length ? ["bad", "Nothing to match on yet: choose at least a name, an identifier, a phone, an email or a field of your own."]
        : !src.fields.full_name && !Object.keys(src.custom || {}).length ? ["warn", "No name: records of this source are matched only on the fields ticked here."]
          : on.some((x) => x.pc === 0) ? ["warn", "A ticked field has no value in the rows read: check its column."]
            : ["good", "Enough to match on. The more fields, the more duplicates are found."];
      const kept = (src.attributes || []).filter((a) => !Object.values(src.custom || {}).includes(a));
      fill(box, el("h3", { class: "section" }, "What records can be matched on"),
        el("ul", { class: "checklist" }, items.filter((x) => x.on).map(line), items.filter((x) => !x.on).map(line)),
        el("p", { class: "verdict " + verdict[0] }, verdict[1]),
        el("p", { class: "muted small" }, "Each row's own id: " + (src.record_id || "the row number") + ". " + (kept.length ? "Kept as is: " + kept.join(", ") + ". " : "") +
          "Percentages are from the first " + plural(n, "row", "rows") + "."));
    };
    refresh.push(draw);
    draw();
    return el("aside", { class: "map-aside", "aria-label": "What records can be matched on" }, box,
      el("div", { class: "row" }, btn("Save data source", save, "primary", "check"), btn("Preview rows first", () => goStep(3), "", "next")));
  }
  function previewStep() {
    const doPreview = () => run(async () => {
      const r = await api("POST", root + "/connections/preview" + (state.reason ? "?" + unmaskQuery(state.reason) : ""), state.source);
      // every column the mapping keeps: our fields, the project's own fields and the columns kept as they are, each with how it is used
      const GROUP = { id: "Row id", field: "Matched on", own: "Your own field", kept: "Kept as is (not matched on)" };
      const titleOf = (c) => (c.group === "id" ? "Row id" : c.group === "field" ? fieldTitle(c.key) : c.group === "own" ? ownName(c.key) : c.key);
      const roleOf = (c) => (c.group === "field" ? ((FIELD_ROLE[c.key] || ["Golden record"])[0]) : GROUP[c.group]);
      const cols = r.columns.filter((c) => c.group !== "id");
      fill(out, el("h3", { class: "section" }, "What this data source gives (" + plural(r.sample_size, "sample row", "sample rows") + ")"),
        table(["Column", "Used for", ["Rows filled"]], cols.map((c) => el("tr", {}, el("td", {}, titleOf(c)), el("td", {}, roleOf(c)),
          el("td", { class: "num" + (c.filled ? "" : " bad") }, c.filled + " of " + r.sample_size)))),
        cols.some((c) => !c.filled) ? el("p", { class: "warn-line" }, "A column with no value in the sample may be mapped to the wrong column of your file.") : null,
        r.values.length ? el("div", { class: "title-row" }, el("h3", { class: "section" }, "Your rows as the engine reads them (" + (r.unmasked ? "actual values" : "values shortened") + ")"),
          revealButton(async () => doPreview())) : null,
        r.values.length ? wide(r.columns.map(titleOf), r.values.map((row) => el("tr", {}, row.map((v) => el("td", {}, v == null ? "" : String(v)))))) : null);
    });
    setTimeout(doPreview, 0);
    return el("div", {},
    el("div", { class: "row" }, btn("Back", () => goStep(2), "", "prev"), btn("Preview again", doPreview, "", "refresh"), btn("Save data source", save, "primary", "check")));
  }
  render();
  if ((preset || batch) && startStep && startStep > 1) await goStep(startStep);
}
