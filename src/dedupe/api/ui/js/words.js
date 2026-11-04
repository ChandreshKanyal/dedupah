"use strict";
// The words of the portal, in one place, so every screen says the same thing the same way. Part of the portal script: the files in js/
// share one scope and load in the order of routes_ui.SCRIPTS.
// One word for each state, everywhere: what people see never depends on which screen they are on.
const STATUS_LABEL = { active: "In use", proposed: "Waiting for approval", approved: "Approved, not in use", superseded: "Replaced", declined: "Declined",
  succeeded: "Succeeded", failed: "Failed", interrupted: "Interrupted", cancelled: "Cancelled", running: "Running", queued: "Queued", deleted: "Deleted",
  open: "Open", unsure: "Not sure", decided: "Decided", in_force: "In force", waiting_for_run: "Waits for the next run",
  pending_approval: "Waits for a second person", undone: "Undone" };
const STATUS_KIND = { active: "success", succeeded: "success", in_force: "success", failed: "danger", declined: "danger", interrupted: "danger",
  proposed: "info", approved: "info", running: "info", queued: "info", waiting_for_run: "info", pending_approval: "warn", unsure: "warn" };
const KIND_LABEL = { same_person: "Same person", different_person: "Different people", unsure: "Not sure", split: "Split", field_override: "Corrected a field" };
const statusTag = (s) => tag(STATUS_LABEL[s] || label(s), STATUS_KIND[s]);

// How far a project is (the server's progress.step): a short state, and the next thing to do about it.
const PROGRESS_LABEL = { data: "Needs data", rules: "Needs rules", run: "Ready to run", running: "Running", failed: "Last run failed",
  review: "Pairs to review", done: "Up to date" };
const PROGRESS_KIND = { data: "warn", rules: "warn", run: "info", running: "info", failed: "danger", review: "warn", done: "success" };
const progressTag = (pr) => (pr ? tag(pr.step === "review" && pr.open_review ? plural(pr.open_review, "pair", "pairs") + " to review" : PROGRESS_LABEL[pr.step],
  PROGRESS_KIND[pr.step]) : null);

// A run is called by its number in its project and when it started: "Run 3 · 4 Nov, 06:40" (the machine id stays under Details).
const shortWhen = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
function runTitle(r, runs) {
  const i = (runs || []).findIndex((x) => x.id === r.id);
  return (i < 0 ? "Run" : "Run " + (runs.length - i)) + " \u00b7 " + shortWhen(r.started_at || r.created_at);
}
const LOAD_WORDS = { incremental: "Only what changed (faster)", full: "Everything again" };

// A change someone declined: who, why, and that the person who proposed it can change it and save again. Null when the newest version was not declined.
function declinedNotice(versions, live) {
  const v = versions[versions.length - 1];
  if (!v || v.status !== "declined" || (live && live.version > v.version)) return null;
  return el("div", { class: "notice warn", role: "note" }, el("strong", {}, (v.proposed_by === me.sub ? "Your change" : "The change by " + v.proposed_by) + " was declined"),
    " by " + v.declined_by + (v.declined_reason ? ": \u201c" + v.declined_reason + "\u201d" : "") + ". Change it and save again, or keep what is in use.");
}

// A refused request in words: "The reason needs at least 10 characters." instead of "reason (string_too_short)". The server names the field,
// the kind of problem and the limit it broke, never the value.
const FIELD_WORDS = { reason: "the reason", name: "the name", id: "the id", description: "the description", username: "the username", password: "the password",
  new_password: "the new password", current_password: "the current password", display_name: "the name", table: "the table name", schema_name: "the schema",
  source_id: "the data source name", record_id: "the row id column", confirm: "the confirmation", new_id: "the new name", at_time: "the time",
  value: "the new value", q: "the search", limit: "the page size", destination: "the destination", url_env: "the environment variable name",
  when: "a rule's condition", rules: "the rules", after_id: "the starting point", mode: "the choice", format: "the file format", frequency: "how often",
  timezone: "the time zone", connections: "the data sources", fields: "the mapping", connector: "the source settings" };
function problemWords(f) {
  const parts = String(f.field || "").split(".").filter((x) => x && !/^\d+$/.test(x) && !["query", "path", "body"].includes(x));
  const last = parts[parts.length - 1] || "";
  const what = FIELD_WORDS[last] || (last ? "the " + label(last) : "a value");
  const n = f.limit;
  const how = { missing: "is missing", string_too_short: n === 1 ? "is empty" : "needs at least " + n + " characters", string_too_long: "can have at most " + n + " characters",
    too_short: "needs at least " + n + " entries", too_long: "can have at most " + n + " entries", string_pattern_mismatch: "has characters that are not allowed",
    greater_than_equal: "must be " + n + " or more", less_than_equal: "must be " + n + " or less", greater_than: "must be more than " + n, less_than: "must be less than " + n,
    int_parsing: "must be a whole number", float_parsing: "must be a number", bool_parsing: "must be yes or no", literal_error: "is not one of the choices",
    enum: "is not one of the choices", extra_forbidden: "is not expected here" }[f.problem] || "is not valid";
  return what.charAt(0).toUpperCase() + what.slice(1) + " " + how + ".";
}
