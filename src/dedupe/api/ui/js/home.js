"use strict";
// Home. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- home --------------------------------------------------------------------------------------------------------------
// Home answers "what needs me now?": one line per thing to do, each with the button that does it (the first is the main button), then every
// project with how far it is. Nothing destructive is offered here.
function homeTodo(p) {
  const pr = p.progress || {};
  const todo = [];
  const step = {
    data: can("sources.manage", p.id) ? ["database", "Add the data of " + p.name, "Its files or databases, to find the duplicates in.", "Add data", () => go(() => addDataPage(p.id))] : null,
    rules: can("config.read", p.id) ? ["sliders", "Choose the rules of " + p.name, "When two records are the same " + wordsOf(p.id).one + ", and how the one clean record is built.", "Choose rules",
      () => go(() => viewProject(p.id, "setup"))] : null,
    run: can("runs.start", p.id) ? ["play", p.name + " is ready for its first run", "Its data and rules are set.", "Start run", () => quickRun(p.id)] : null,
    failed: p.latest_run ? ["alert", "The last run of " + p.name + " did not finish", "Open it to see what went wrong, then run again.", "Open run",
      () => go(() => viewRun(p.id, p.latest_run.id))] : null,
    review: can("queue.read", p.id) ? ["inbox", plural(pr.open_review, "pair", "pairs") + " in " + p.name + " need your decision", "The same " + wordsOf(p.id).one + " or not: the matching rules sent them to a person.",
      "Review", () => go(() => viewInbox(p.id))] : null,
  }[pr.step];
  if (step) todo.push(step);
  if (pr.pending_approvals) todo.push(["check", plural(pr.pending_approvals, "change", "changes") + " in " + p.name + " wait for your approval", "Another person proposed them.",
    "Open approvals", link("/approvals")]);
  return todo;
}
async function viewHome() {
  here("/");
  const h = await api("GET", "/home");
  await refreshCache();
  const projects = h.project_list || [];
  const todo = projects.flatMap(homeTodo);
  const create = can("projects.create") ? btn("New project", () => go(newProject), projects.length ? "" : "primary", "plus") : null;
  const needs = todo.length
    ? el("div", { class: "panel todo" }, todo.map(([ic, text, hint, action, fn], i) => el("div", { class: "todo-row" },
      el("span", { class: "tile-icon" }, icon(ic)), el("div", {}, el("strong", {}, text), el("span", { class: "muted" }, hint)),
      btn(action, fn, i === 0 ? "primary" : ""))))
    : el("p", { class: "hint" }, projects.length ? "Nothing needs you right now." : "");
  const list = projects.length
    ? table(["Project", "Where it is", "Last run"], projects.map((p) => linkRow(p.name, () => viewProject(p.id), el("td", {}, progressTag(p.progress)),
      el("td", {}, p.latest_run ? when(p.latest_run.started_at || p.latest_run.created_at) : el("span", { class: "muted" }, "no runs yet")))))
    : empty(create ? "No projects yet. A project holds one piece of dedupe work: its data, its rules and its results." : "You are not in any project yet. Ask an administrator to add you to one.", create);
  show(page("Welcome, " + me.username, "Find duplicate customers across your sources, check the doubtful ones and keep one clean record per customer.", projects.length ? [create] : []),
    projects.length ? el("h2", { class: "section" }, "Needs you now") : null, projects.length ? needs : null,
    el("h2", { class: "section" }, "Your projects"), list);
}
