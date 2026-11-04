"use strict";
// Home. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- home --------------------------------------------------------------------------------------------------------------
async function viewHome() {
  here("/");
  const [h] = await Promise.all([api("GET", "/home"), refreshCache()]);
  const stats = [stat("Projects", num(h.projects), link("/projects")), stat("Connections", num(h.connections))];
  if (h.open_review_items != null) stats.push(stat("Pairs to review", num(h.open_review_items), link("/review")));
  if (h.pending_approvals != null) stats.push(stat("Waiting for approval", num(h.pending_approvals), link("/approvals")));
  const start = [];
  if (can("projects.create")) start.push(tile("folder", "Create a project", "A project has its own data connections, decisions, person IDs and rules.", () => go(newProject)));
  if (can("sources.manage")) start.push(tile("database", "Add data", "Connect a file, sheet or database, or upload a file, inside a project.", () => pickProject("Add data", "Choose the project this data belongs to.", (pid) => go(() => viewProject(pid, "connections")))));
  if (can("runs.start")) start.push(tile("play", "Start a run", "Match and merge the records of chosen connections.", () => go(() => newRun())));
  if (can("queue.read")) start.push(tile("inbox", "Review matches", "Every pair waiting for a person, across your projects.", link("/review")));
  show(page("Welcome, " + me.username, "Find duplicate customers across your sources, review them and keep one golden record per person."),
    el("div", { class: "stats" }, stats),
    start.length ? el("h2", { class: "section" }, "Get started") : null, start.length ? el("div", { class: "tiles" }, start) : null,
    can("runs.read") ? el("h2", { class: "section" }, "Recent runs") : null,
    can("runs.read") ? runsTable(h.recent_runs, true) : null);
}
