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
  el("details", { class: "advanced" }, el("summary", {}, "Choose a source"), el("div", { class: "row" }, icon("search"), q), legend, none, sections));
}
// The Data step: the project's data sources first (files still to map among them), Add data, and the details folded away.
async function projectConnections(p, connections) {
  const pid = p.id;
  const manage = can("sources.manage");
  const uploads = manage ? await api("GET", P(pid) + "/uploads") : [];
  const unmapped = uploads.filter((u) => !u.used_by.length).reverse();          // oldest first, the order they are mapped in
  const removeUpload = async (u) => {
    if (!(await confirmDialog("Delete " + u.filename + "?", "The uploaded file is erased for good. No data source uses it.", "Delete file", true))) return;
    await api("DELETE", P(pid) + "/uploads/" + u.upload_id);
    await go(() => viewProject(pid, "setup", "data"));
    say(u.filename + " was deleted.", "good");
  };
  const state = (c) => (c.live_version ? (c.latest_status === "active" ? tag("In use", "success") : el("span", {}, tag("In use", "success"), " ", statusTag(c.latest_status)))
    : statusTag(c.latest_status === "active" ? "proposed" : c.latest_status));
  const rows = [
    ...unmapped.map((u) => el("tr", {}, el("td", {}, el("strong", {}, u.filename)), el("td", {}, typeCell("upload")), el("td", {}, tag("Needs mapping", "warn")),
      el("td", {}, num(u.rows) + " rows, uploaded " + when(u.created_at)),
      el("td", { class: "actions-cell" }, el("div", { class: "row tight" }, btn("Map it", () => go(() => mapUpload(pid, u.upload_id)), "small"),
        moreMenu([["Delete file", () => removeUpload(u), true]], u.filename))))),
    ...connections.map((c) => linkRow(c.id, () => viewConnection(pid, c.id), el("td", {}, typeCell(c.type)), el("td", {}, state(c)),
      el("td", {}, plural(c.fields_mapped, "field", "fields") + " mapped" + (c.last_check && !c.last_check.ok ? ". " : ""), c.last_check && !c.last_check.ok ? tag("Last check failed", "danger") : null),
      manage ? el("td", { class: "actions-cell" }, btn("Change", () => run(() => editConnection(pid, c.id)), "small", null, { "aria-label": "Change data source " + c.id })) : el("td", {}))),
  ];
  const add = manage ? btn("Add data", () => go(() => addDataPage(pid)), rows.length ? "" : "primary", "plus") : null;
  const more = manage ? moreMenu([cache.projects.length > 1 ? ["Copy a data source from another project", () => copyDialog(pid)] : null]) : null;
  const used = uploads.filter((u) => u.used_by.length);
  return el("div", {},
    el("div", { class: "title-row" }, el("h2", { class: "section" }, "Data sources"), el("div", { class: "row tight" }, add, more)),
    unmapped.length ? el("div", { class: "notice warn", role: "note" }, plural(unmapped.length, "uploaded file needs", "uploaded files need") +
      " mapping before a run can read " + (unmapped.length === 1 ? "it" : "them") + ": press Map it.") : null,
    rows.length ? table(["Data source", "Kind", "State", "", ""], rows)
      : empty(manage ? "No data yet. Upload files or connect a database; the data belongs to this project only." : "No data yet. Someone who manages data adds it.", add),
    connections.length > 1 ? el("details", { class: "advanced" }, el("summary", {}, "Compare sources: which field comes from which column"), coverage(connections)) : null,
    used.length ? el("details", { class: "advanced" }, el("summary", {}, "Uploaded files (" + used.length + ")"),
      table(["File", ["Rows"], "Read by", "Uploaded", "By"], used.map((u) => el("tr", {}, el("td", {}, u.filename), el("td", { class: "num" }, num(u.rows)),
        el("td", {}, u.used_by.join(", ")), el("td", {}, when(u.created_at)), el("td", {}, u.uploaded_by))))) : null);
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
function coverage(connections) {
  const used = CANON_ORDER.filter((f) => connections.some((c) => (c.fields || {})[f]));
  const own = [...new Set(connections.flatMap((c) => Object.keys(c.own_fields || {})))];
  if (!used.length && !own.length) return null;
  const extra = connections.filter((c) => (c.extra_columns || []).length);
  return el("div", {},
    el("p", { class: "hint" }, "Each column shows the column of that source that fills the field (latest version). Records of all sources are matched with each other on these fields."),
    table(["Field", ...connections.map((c) => c.id)], [
      ...used.map((f) => el("tr", {}, el("td", {}, fieldTitle(f)), connections.map((c) => el("td", {}, (c.fields || {})[f] ? el("span", { class: "mono" }, c.fields[f]) : el("span", { class: "muted" }, "-"))))),
      ...own.map((f) => el("tr", {}, el("td", {}, label(f) + " (your own)"), connections.map((c) => el("td", {}, (c.own_fields || {})[f] ? el("span", { class: "mono" }, c.own_fields[f]) : el("span", { class: "muted" }, "-"))))),
      el("tr", {}, el("td", {}, "Record id"), connections.map((c) => el("td", {}, c.record_id ? el("span", { class: "mono" }, c.record_id) : el("span", { class: "muted" }, "row number")))),
      extra.length ? el("tr", {}, el("td", {}, "Extra columns kept"), connections.map((c) => el("td", {}, (c.extra_columns || []).join(", ") || el("span", { class: "muted" }, "-")))) : null,
    ]));
}
async function editConnection(pid, cid) {
  const c = await api("GET", P(pid) + "/connections/" + cid);
  await connectionWizard(pid, c.latest.connector.type, JSON.parse(JSON.stringify(c.latest)), 2);
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
  const mapRows = CANON_ORDER.filter((f) => (shown.fields || {})[f]).concat(Object.keys(shown.fields || {}).filter((f) => !CANON_ORDER.includes(f)))
    .map((f) => el("tr", {}, el("td", {}, fieldTitle(f)), el("td", { class: "mono" }, typeof shown.fields[f] === "string" ? shown.fields[f] : JSON.stringify(shown.fields[f]))))
    .concat(Object.entries(shown.custom || {}).map(([f, col]) => el("tr", {}, el("td", {}, label(f) + " (your own field)"), el("td", { class: "mono" }, typeof col === "string" ? col : JSON.stringify(col)))));
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
    const ownName = (n) => (ownFields.find((f) => f.name === n) || { title: label(n) }).title;
    const examples = (c) => [...new Set(sample.map((r) => r[c]).filter((v) => v != null && v !== ""))].slice(0, 3);
    const fieldOf = (c) => Object.keys(src.fields).find((f) => src.fields[f] === c);
    // one column of document numbers whose type (PAN, Aadhaar, passport ...) is in another column: each type fills its own field (U2-15)
    const isDoc = (e) => e && typeof e === "object" && e.when && e.column;
    const docsOf = (c) => Object.entries(src.fields).filter(([, e]) => isDoc(e) && e.column === c);
    const wantDocs = new Set();                            // chosen as document numbers, type column not found yet
    const useOf = (c) => (fieldOf(c) ? "f:" + fieldOf(c) : ownOf(c) ? "o:" + ownOf(c) : docsOf(c).length || wantDocs.has(c) ? "docs" : src.record_id === c ? "id" : src.attributes.includes(c) ? "keep" : "");
    const combined = Object.keys(src.fields).filter((f) => typeof src.fields[f] !== "string" && !isDoc(src.fields[f]));
    const seen = (col) => [...new Set(sample.map((r) => r[col]).filter((v) => v != null && String(v).trim() !== "").map((v) => String(v).trim()))];
    const docField = (v) => DOC_TYPES.find(([re]) => re.test(v));
    // the usual spellings of every type, plus the ones the sample shows: a type missing from the first rows still counts (Aadhaar rows further down)
    const setDocs = (c, typeCol) => {
      for (const [f] of docsOf(c)) delete src.fields[f];
      const by = Object.fromEntries(DOC_TYPES.map(([, f, usual]) => [f, [...usual]]));
      for (const v of seen(typeCol)) { const d = docField(v); if (d && !by[d[1]].includes(v.toLowerCase())) by[d[1]].push(v.toLowerCase()); }
      for (const [f, vals] of Object.entries(by)) if (typeof src.fields[f] !== "string") src.fields[f] = { column: c, when: { [typeCol]: vals } };
    };
    const setUse = (c, use) => {
      for (const f of Object.keys(src.fields)) if (src.fields[f] === c || (isDoc(src.fields[f]) && src.fields[f].column === c)) delete src.fields[f];
      if (src.record_id === c) src.record_id = null;
      src.attributes = src.attributes.filter((x) => x !== c);
      for (const f of Object.keys(src.custom)) if (src.custom[f] === c) delete src.custom[f];
      wantDocs.delete(c);
      if (use.startsWith("f:")) src.fields[use.slice(2)] = c;            // a field comes from one column: another column using it lets go
      else if (use.startsWith("o:")) src.custom[use.slice(2)] = c;
      else if (use === "id") src.record_id = c;
      else if (use === "keep") src.attributes.push(c);
      else if (use === "docs") {
        const typeCol = cols.find((x) => x !== c && seen(x).some(docField));
        if (typeCol) setDocs(c, typeCol); else wantDocs.add(c);
      }
      refresh.forEach((r) => r());
    };
    const refresh = [];
    const rows = cols.map((c) => {
      const t = types[c] || {};
      const sel = el("select", { "aria-label": "Use " + c + " as" });
      const note = el("div", { class: "muted small" });
      const draw = () => {
        const cur = useOf(c);
        const taken = (f) => (typeof src.fields[f] === "string" && src.fields[f] !== c ? " (now from " + src.fields[f] + ")" : combined.includes(f) ? " (now several columns combined)" : "");
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
        fill(note, wrong ? tag("The values do not look like " + fieldTitle(f).toLowerCase(), "warn")
          : role ? role[0] + (role[1] ? ": " + role[1] : "") : cur === "id" ? "Traces a golden record back to this row; not used to match"
            : cur.startsWith("o:") ? "Your own field: records are matched on it (how is set under Fields and search)"
            : cur === "keep" ? "Carried into the golden record as it is" : t.field && !src.fields[t.field] ? "Could be " + fieldTitle(t.field).toLowerCase()
              : t.type === "organisation_name" && !cur ? "Could be a field of your own: choose \u201cA new field of your own\u201d, kind Organisation name" : "");
      };
      sel.addEventListener("change", () => run(async () => {
        if (sel.value !== "new") { setUse(c, sel.value); return; }
        const f = await newOwnField(pid, c, Object.keys(src.custom));
        if (f) { ownFields = [...ownFields, f]; setUse(c, "o:" + f.name); say("The field " + f.title + " is added. How it is compared can be changed under Fields and search.", "good"); }
        else draw();
      }, true));
      refresh.push(draw);
      draw();
      return el("tr", {}, el("td", {}, el("strong", {}, c)),
        el("td", {}, el("span", { class: "examples" }, examples(c).map((v) => el("span", { class: "ex" }, String(v))))),
        el("td", {}, t.type ? t.description : el("span", { class: "muted" }, "Not recognised"), t.filled != null ? el("div", { class: "muted small" }, Math.round(t.filled * 100) + "% of rows filled") : null),
        el("td", {}, sel, note));
    });
    const KEY = ["full_name", "dob", "mobile", "email", "address", "pincode", "pan", "aadhaar", "customer_number"];
    const missing = el("p", { class: "hint" });
    const warnName = el("div", { class: "notice warn", role: "note" }, "No column is used as Full name: without names, records of this source are hard to match.");
    const drawMissing = () => {
      const gone = KEY.filter((f) => !src.fields[f]);
      missing.textContent = gone.length ? "Not in this file: " + gone.map(fieldTitle).join(", ") + ". Every field is optional; the more a source has, the more duplicates are found." : "";
      warnName.hidden = !!src.fields.full_name || Object.keys(src.custom).length > 0;      // a project matching on its own fields needs no person's name
    };
    refresh.push(drawMissing);
    drawMissing();
    return el("div", {},
      el("p", { class: "hint" }, "Choose what each column of your file holds. Suggestions from the values are already filled in: check them and change any that are wrong. " +
        "Whether an identifier such as PAN decides on its own is set in the project's matching rules. " +
        "A column that fits none of the fields (a company name, a GSTIN, a product code) can still be matched on: choose \u201cA new field of your own\u201d."),
      warnName,
      table(["Your column", "Examples", "Values look like", "Use as"], rows, "map-table"),
      missing,
      combined.length ? el("p", { class: "hint" }, "Kept as set before (several columns combined): " + combined.map(fieldTitle).join(", ") + ".") : null,
      el("details", { class: "advanced" }, el("summary", {}, "Sample rows (" + sample.length + ", " + (state.info.unmasked ? "actual values" : "values shortened") + ")"),
        el("div", { class: "row" }, revealButton(async () => { state.info = { ...state.info, ...(await api("POST", inspectUrl(), { connector: state.source.connector })) }; render(); })),
        wide(cols, sample.map((r) => el("tr", {}, cols.map((c) => el("td", {}, r[c] == null ? "" : r[c])))))),
      el("div", { class: "row" }, btn("Back", () => goStep(1), "", "prev"), btn("Next: preview", () => goStep(3), "primary", "next")));
  }
  function previewStep() {
    const doPreview = () => run(async () => {
      const r = await api("POST", root + "/connections/preview" + (state.reason ? "?" + unmaskQuery(state.reason) : ""), state.source);
      const cols = Object.keys(r.rows[0] || {}).filter((c) => c !== "attributes" && r.rows.some((row) => row[c] != null));
      fill(out, el("h3", { class: "section" }, "Fields filled in " + r.sample_size + " sample rows"),
        table(["Field", ["Rows filled"]], Object.entries(r.filled).map(([f, n]) => el("tr", {}, el("td", {}, fieldTitle(f)), el("td", { class: "num" }, n + " of " + r.sample_size)))),
        r.rows.length ? el("div", { class: "title-row" }, el("h3", { class: "section" }, "Your rows as the engine reads them (" + (r.unmasked ? "actual values" : "values shortened") + ")"),
          revealButton(async () => doPreview())) : null,
        r.rows.length ? wide(cols, r.rows.map((row) => el("tr", {}, cols.map((c) => el("td", {}, row[c] == null ? "" : row[c]))))) : null);
    });
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
      await go(() => viewConnection(pid, state.source.source_id));
      say(saved, "good");
    };
    setTimeout(doPreview, 0);
    return el("div", {},
    el("div", { class: "row" }, btn("Back", () => goStep(2), "", "prev"), btn("Preview again", doPreview, "", "refresh"), btn("Save data source", save, "primary", "check")));
  }
  render();
  if ((preset || batch) && startStep && startStep > 1) await goStep(startStep);
}
