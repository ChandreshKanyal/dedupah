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
