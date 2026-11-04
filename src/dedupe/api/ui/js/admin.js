"use strict";
// Administration: approvals, audit log, users and roles, the account menu and Settings. Part of the portal script: the files in js/ share one scope and load in the order of routes_ui.SCRIPTS.
// ---- approvals: everything waiting for someone ------------------------------------------------------------------------------------
async function viewApprovals() {
  here("/approvals");
  await refreshCache();
  const per = await Promise.all(cache.projects.map(async (p) => {
    const [decisions, connections, rules, dests] = await Promise.all([
      can("queue.read") ? api("GET", P(p.id) + "/approvals").catch(() => []) : [],
      can("config.read") ? api("GET", P(p.id) + "/connections").catch(() => []) : [],
      can("config.read") ? Promise.all(CONFIGS.map(([n, t]) => api("GET", P(p.id) + "/config/" + n).then((vs) => vs.filter((v) => v.status === "proposed" || v.status === "approved")
        .map((v) => ({ ...v, name: n, title: t }))).catch(() => []))).then((x) => x.flat()) : [],
      can("results.export") ? api("GET", P(p.id) + "/destinations").catch(() => []) : []]);
    const waitingC = connections.filter((c) => c.latest_status === "proposed" || c.latest_status === "approved");
    const waitingD = dests.filter((d) => d.latest_status === "proposed" || d.latest_status === "approved");
    return { p, decisions, connections: waitingC, rules, dests: waitingD };
  }));
  const sections = [];
  const decisionRows = per.flatMap(({ p, decisions }) => decisions.map((i) => ({ p, i })));
  const act = async (p, i, what) => {
    const why = what === "decline" ? await askReason("Decline this decision", "Why is it declined?", 3, "Decline", true) : await askReason("Approve this decision", "Note", 0, "Approve");
    if (why === null) return;
    await api("POST", P(p.id) + "/approvals/" + i.id + "/" + what, { reason: why });
    await go(viewApprovals);
    say(what === "approve" ? "Approved. It applies at the project's next run." : "Declined. The decision has no effect.", "good");
  };
  const people = (p, i) => (i.run_id && i.persons && i.persons.length ? el("button", { type: "button", class: "link mono", onclick: () => go(i.persons.length === 2
    ? () => viewCompare(p.id, i.run_id, i.persons[0], i.persons[1]) : () => viewPerson(p.id, i.run_id, i.persons[0])) }, i.persons.join(" and ")) : el("span", { class: "mono" }, (i.persons || []).join(", ")));
  if (decisionRows.length) {
    sections.push(el("h2", { class: "section" }, "Review decisions waiting for a second person"),
      table(["Project", "Decision", "People", ["Score"], "Main evidence", "Made by", "Reason", ""], decisionRows.map(({ p, i }) => el("tr", {}, el("td", {}, p.name),
        el("td", {}, (KIND_LABEL[i.kind] || label(i.kind)) + (i.field ? ": " + label(i.field) : "")), el("td", {}, people(p, i)), el("td", { class: "num" }, pct(i.probability)),
        el("td", {}, (i.top_evidence || []).map((x) => label(x.comparison) + ": " + x.level).join("; ")), el("td", {}, i.yours ? tag("You", "info") : i.actor), el("td", {}, i.reason),
        el("td", { class: "actions-cell" }, i.yours ? tag("Someone else approves it", "warn") : can("judgment.approve")
          ? [btn("Approve", () => act(p, i, "approve"), "small primary"), btn("Decline", () => act(p, i, "decline"), "small danger")] : null)))));
  }
  const conn = per.flatMap(({ p, connections }) => connections.map((c) => ({ p, c })));
  if (conn.length) {
    sections.push(el("h2", { class: "section" }, "Connection changes"), table(["Connection", "Project", "Type", "Version", "Status", "Proposed by"], conn.map(({ p, c }) =>
      linkRow(c.id, () => viewConnection(p.id, c.id), el("td", {}, p.name), el("td", {}, label(c.type)), el("td", {}, "Version " + c.latest_version), el("td", {}, statusTag(c.latest_status)),
        el("td", {}, c.updated_by)))));
  }
  const rules = per.flatMap(({ p, rules: rs }) => rs.map((v) => ({ p, v })));
  if (rules.length) {
    sections.push(el("h2", { class: "section" }, "Rule changes"), table(["Rules", "Project", "Version", "Status", "Proposed by", "Reason"], rules.map(({ p, v }) =>
      linkRow(v.title, () => viewProject(p.id, "setup", Object.keys(SETUP_CONFIG).find((k) => SETUP_CONFIG[k] === v.name) || "matching"), el("td", {}, p.name), el("td", {}, "Version " + v.version), el("td", {}, statusTag(v.status)), el("td", {}, v.proposed_by), el("td", {}, v.reason)))));
  }
  const dests = per.flatMap(({ p, dests: ds }) => ds.map((d) => ({ p, d })));
  if (dests.length) {
    sections.push(el("h2", { class: "section" }, "Database destinations"), table(["Destination", "Project", "Version", "Status", "Proposed by"], dests.map(({ p, d }) =>
      linkRow(d.id, () => viewProject(p.id, "results"), el("td", {}, p.name), el("td", {}, "Version " + d.latest_version), el("td", {}, statusTag(d.latest_status)), el("td", {}, d.updated_by)))));
  }
  show(page("Approvals", "Everything waiting for someone across your projects: review decisions that need a second person, and changes to connections, rules and destinations."),
    ...(sections.length ? sections : [empty("Nothing waits for approval.")]));
}

// ---- audit log ------------------------------------------------------------------------------------------------------------
const AUDIT_EVENTS = ["auth_failed", "login", "permission_denied", "rate_limited", "request_rejected", "pii_unmasked", "run_started", "run_finished", "run_cancelled", "export",
  "config_change", "judgment_change", "data_deleted", "server_error", "startup"];
async function viewAudit(event) {
  here("/audit");
  const q = "/security/events?newest_first=true&limit=100" + (event ? "&event=" + event : "");
  const [rows] = await Promise.all([api("GET", q), refreshCache()]);
  const filter = el("select", {}, el("option", { value: "" }, "All events"), AUDIT_EVENTS.map((e) => el("option", { value: e, selected: e === event }, label(e))));
  filter.addEventListener("change", () => go(() => viewAudit(filter.value)));
  const verify = async () => {
    const v = await api("GET", "/security/verify");
    say(v.ok ? "The log is intact: the hash chain verifies." : "The log chain is BROKEN. Investigate before trusting it.", v.ok ? "good" : "bad");
  };
  const target = (e) => {
    const known = e.project_id && cache.projects.some((p) => p.id === e.project_id);
    const m = /^([a-z0-9-]+)\/(R[0-9a-f]{12})$/.exec(e.object_ref || "");
    if (known && m && m[1] === e.project_id) return el("button", { type: "button", class: "link mono", onclick: () => go(() => viewRun(m[1], m[2])) }, e.object_ref);
    if (known) return el("button", { type: "button", class: "link mono", onclick: () => go(() => viewProject(e.project_id)) }, e.object_ref || e.project_id);
    return el("span", { class: "mono" }, e.object_ref);
  };
  const rowOf = (e) => el("tr", {}, el("td", {}, when(e.created_at)), el("td", {}, label(e.event)), el("td", {}, e.actor),
    el("td", {}, (e.method ? e.method + " " : "") + (e.route || (e.detail && e.detail.action ? label(e.detail.action) : ""))), el("td", {}, target(e)),
    el("td", { class: "num" }, e.status == null ? "" : e.status));
  const t = table(["Time", "Event", "Who", "What", "Object", ["Status"]], rows.map(rowOf));
  const body = t.querySelector("tbody");
  let last = rows.length ? rows[rows.length - 1].id : null;
  const more = rows.length === 100 ? btn("Show older events", async () => {
    const older = await api("GET", q + "&before_id=" + last);
    body.append(...older.map(rowOf));
    if (older.length) last = older[older.length - 1].id;
    if (older.length < 100) more.remove();
  }, "small") : null;
  show(page("Audit log", "Security-relevant events, newest first. Append-only and hash-chained; personal data values are never written here.",
    [field("Show", filter), btn("Verify integrity", verify, "", "shield")]),
  rows.length ? t : empty("No events."), more ? el("div", { class: "row" }, more) : null);
}

// ---- password, users and roles ---------------------------------------------------------------------------------------------
async function viewPassword(forced) {
  if (!forced) here("/password");
  const cur = el("input", { type: "password", autocomplete: "current-password" });
  const nw = el("input", { type: "password", autocomplete: "new-password" });
  const again = el("input", { type: "password", autocomplete: "new-password" });
  const err = errBox();
  const save = async () => {
    fail(err, "");
    if (nw.value !== again.value) { fail(err, "The two new passwords differ.", again); return; }
    try {
      const r = await api("POST", "/auth/password", { current_password: cur.value, new_password: nw.value });
      token = r.token;
      me = await api("GET", "/me");
    } catch (e) { fail(err, e.message, nw); return; }
    cur.value = ""; nw.value = ""; again.value = "";
    buildNav();
    await go(() => dispatch(forced ? "/" : "/password"));
    say("Your password was changed.", "good");
  };
  focusNext = true;
  show(page(forced ? "Choose a new password" : "Change password",
    forced ? "You signed in with a temporary password. Choose your own before you continue." : "Changing it signs you out of any other session."),
  el("div", { class: "fields" }, field("Current password", cur), field("New password (12 to 128 characters, not your username)", nw), field("Repeat the new password", again)), err,
  el("div", { class: "row" }, btn("Save password", save, "primary")));
}
function checkList(items, chosen, allowed, readOnly) {
  const boxes = items.map((it) => {
    const box = el("input", { type: "checkbox", value: it.name, checked: chosen.has(it.name), disabled: readOnly || !allowed(it) });
    return [box, el("label", { class: "check" }, box, el("span", {}, el("strong", { class: readOnly ? "" : "mono" }, it.name), el("span", { class: "muted" }, it.description || "")))];
  });
  return { node: el("div", { class: "checks" }, boxes.map((b) => b[1])), values: () => boxes.filter((b) => b[0].checked).map((b) => b[0].value) };
}
const heldBy = (perms) => { const mine = new Set(me.permissions); return perms.every((p) => mine.has(p)); };
const roleTags = (roles) => el("span", { class: "row" }, roles.map((r) => tag(label(r))));
async function viewUsers() {
  here("/users");
  const [users, roles] = await Promise.all([api("GET", "/users"), api("GET", "/roles")]);
  const state = (u) => (u.disabled ? tag("Disabled", "danger") : u.locked ? tag("Locked", "danger") : u.must_change_password ? tag("Temporary password", "warn") : tag("Active", "success"));
  const rows = users.map((u) => el("tr", {}, el("td", {}, u.username), el("td", {}, u.display_name), el("td", {}, roleTags(u.roles)), el("td", {}, state(u)),
    el("td", {}, u.last_login_at ? when(u.last_login_at) : "Never"),
    el("td", { class: "actions-cell" }, btn("Edit", () => userDialog(u, roles), "small"), u.username === me.sub ? null : btn("Reset password", () => resetDialog(u), "small"))));
  show(page("Users", "People who can sign in with a username and password. Roles decide what each person may do.", [btn("New user", () => userDialog(null, roles), "primary", "plus")]),
    rows.length ? table(["Username", "Name", "Roles", "Status", "Last sign-in", ""], rows) : empty("No users yet."));
}
async function userDialog(u, catalogue) {
  const roles = catalogue.roles.filter((r) => heldBy(r.permissions) || (u && u.roles.includes(r.name)));
  const self = !!u && u.username === me.sub;
  const name = el("input", { type: "text", maxlength: "64", value: u ? u.username : "", disabled: !!u });
  const full = el("input", { type: "text", maxlength: "100", value: u ? u.display_name : "" });
  const pass = el("input", { type: "password", autocomplete: "new-password" });
  const off = el("input", { type: "checkbox", checked: !!(u && u.disabled), disabled: self });
  const picks = checkList(roles.map((r) => ({ name: r.name, description: r.description })), new Set(u ? u.roles : []),
    (it) => heldBy(roles.find((r) => r.name === it.name).permissions), self);
  const err = errBox();
  const body = el("div", {}, field("Username (lower-case letters, digits, . - _)", name), field("Name (optional)", full),
    u ? null : field("Temporary password (12 to 128 characters; the person must change it at first sign-in)", pass),
    el("h3", { class: "section" }, "Roles"), self ? el("p", { class: "hint" }, "You cannot change your own roles.") : null, picks.node,
    u ? el("label", { class: "check" }, off, el("span", {}, "Disabled: this person cannot sign in")) : null, err);
  const done = await dialog(u ? "Edit " + u.username : "New user", body, u ? "Save user" : "Create user", async () => {
    try {
      if (u) {
        const patch = { display_name: full.value.trim() };
        if (!self) { patch.roles = picks.values(); patch.disabled = off.checked; }
        await api("PATCH", "/users/" + u.username, patch);
      } else {
        await api("POST", "/users", { username: name.value.trim().toLowerCase(), display_name: full.value.trim(), password: pass.value, roles: picks.values() });
      }
      return true;
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (done) { await go(viewUsers); say(u ? "User saved." : "User created. Give them the temporary password in person; they must change it at first sign-in.", "good"); }
}
async function resetDialog(u) {
  const pass = el("input", { type: "password", autocomplete: "new-password" });
  const err = errBox();
  const body = el("div", {}, el("p", { class: "hint" }, "Their current sessions end and they must choose a new password at the next sign-in."),
    field("New temporary password (12 to 128 characters)", pass), err);
  const done = await dialog("Reset password for " + u.username, body, "Reset password", async () => {
    try { await api("POST", "/users/" + u.username + "/password", { password: pass.value }); return true; } catch (e) { fail(err, e.message, pass); return undefined; }
  });
  if (done) { await go(viewUsers); say("Password reset for " + u.username + ".", "good"); }
}
async function viewRoles() {
  here("/roles");
  const data = await api("GET", "/roles");
  const rows = data.roles.map((r) => el("tr", {}, el("td", {}, label(r.name)), el("td", {}, r.builtin ? tag("Built-in") : tag("Custom", "info")), el("td", {}, r.description),
    el("td", { class: "num" }, r.permissions.length), el("td", { class: "num" }, r.users),
    el("td", { class: "actions-cell" }, btn(r.builtin ? "View" : "Edit", () => roleDialog(r, data.permissions), "small"),
      r.builtin ? null : btn("Delete", () => deleteRole(r), "small danger"))));
  show(page("Roles", "A role is a named set of permissions. Built-in roles are fixed; create your own to give people exactly the access they need. Nobody can give a role more than they hold themselves.",
    [btn("New role", () => roleDialog(null, data.permissions), "primary", "plus")]), table(["Role", "Type", "Description", ["Permissions"], ["People"], ""], rows));
}
async function roleDialog(role, catalogue) {
  const fixed = !!(role && role.builtin);
  const name = el("input", { type: "text", maxlength: "40" });
  const desc = el("input", { type: "text", maxlength: "300", value: role ? role.description : "", disabled: fixed });
  const picks = checkList(catalogue, new Set(role ? role.permissions : []), (it) => heldBy([it.name]), fixed);
  const err = errBox();
  const body = el("div", {}, role ? null : field("Name (lower-case letters, digits, - or _)", name), field("Description", desc),
    el("h3", { class: "section" }, "What this role may do"), picks.node, err);
  const title = role ? (fixed ? label(role.name) + " (built-in, read only)" : "Edit " + role.name) : "New role";
  const done = await dialog(title, body, fixed ? "Close" : "Save role", async () => {
    if (fixed) return true;
    try {
      if (role) await api("PUT", "/roles/" + role.name, { description: desc.value.trim(), permissions: picks.values() });
      else await api("POST", "/roles", { name: name.value.trim().toLowerCase(), description: desc.value.trim(), permissions: picks.values() });
      return true;
    } catch (e) { fail(err, e.message); return undefined; }
  });
  if (done && !fixed) { await go(viewRoles); say("Role saved. It applies to everyone holding it from their next request.", "good"); }
}
async function deleteRole(r) {
  if (!(await confirmDialog("Delete role " + r.name + "?", "This cannot be undone. A role that people or projects still use cannot be deleted.", "Delete role", true))) return;
  await api("DELETE", "/roles/" + r.name);
  await go(viewRoles);
  say("Role deleted.", "good");
}

// ---- the account menu, My account and Settings ------------------------------------------------------------------------------
// The name in the top bar opens a small menu (a real menu: arrow keys move, Escape and a click elsewhere close it).
function initUserMenu() {
  const opener = $("user-btn");
  const menu = $("user-menu");
  const items = () => [...menu.querySelectorAll("[role=menuitem]")];
  const toggle = (open) => { menu.hidden = !open; opener.setAttribute("aria-expanded", String(open)); if (open) items()[0].focus(); };
  const pick = (fn) => () => { toggle(false); fn(); };
  opener.addEventListener("click", () => toggle(menu.hidden));
  $("menu-account").addEventListener("click", pick(() => go(viewAccount)));
  $("menu-settings").addEventListener("click", pick(() => go(viewSettings)));
  $("menu-signout").addEventListener("click", pick(() => signOut("")));
  menu.addEventListener("keydown", (e) => {
    const all = items();
    const i = all.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); toggle(false); opener.focus(); return; }
    const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: all.length - 1 }[e.key];
    if (to !== undefined) { e.preventDefault(); all[(to + all.length) % all.length].focus(); }
  });
  document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target) && !opener.contains(e.target)) toggle(false); });
}
const SIGN_IN_WORDS = { local: "Username and password, kept by this workspace", oidc: "Your organisation's single sign-on", dev: "Access token (development)" };
async function viewAccount() {
  here("/account");
  const cfg = await api("GET", "/auth/config");
  show(page("My account", null, [cfg.mode === "local" ? btn("Change password", () => go(() => viewPassword(false)), "", "key") : null, btn("Sign out", () => signOut(""), "", "lock")]),
    el("div", { class: "cols" },
      el("div", { class: "panel" }, el("h3", {}, "Profile"), kv([["Name", me.username], ["User ID", el("span", { class: "mono" }, me.sub)],
        ["Signs in with", SIGN_IN_WORDS[cfg.mode] || label(cfg.mode)]])),
      el("div", { class: "panel" }, el("h3", {}, "Roles"), el("div", { class: "body" }, el("ul", { class: "plain" }, me.roles.map((r) => el("li", {}, label(r))))),
        el("details", { class: "advanced body" }, el("summary", {}, "What these roles allow (" + me.permissions.length + ")"),
          el("ul", { class: "plain" }, me.permissions.map((x) => el("li", { class: "mono" }, x)))))),
    el("p", { class: "hint" }, "Roles are given by an administrator under Users. " + (cfg.mode === "local" ? "Changing your password signs you out of every other session." : "")));
}
async function viewSettings() {
  here("/settings");
  const cfg = await api("GET", "/auth/config");
  const admin = [["Users", "users.manage", "users", "/users", "Add people, reset passwords, turn accounts off, give roles."],
    ["Roles", "roles.manage", "sliders", "/roles", "Decide what each role may do."],
    ["Approvals", "config.read", "check", "/approvals", "Rules, connections and decisions waiting for a second person."],
    ["Audit log", "logs.read", "shield", "/audit", "Who did what and when: sign-ins, exports, shown identifiers, changes."]].filter((x) => can(x[1]));
  show(page("Settings", "How this workspace is set up. How matching works is set per project, under its Rules tab."),
    el("div", { class: "panel" }, el("h3", {}, "Workspace"), kv([["Sign-in", SIGN_IN_WORDS[cfg.mode] || label(cfg.mode)],
      ["Showing masked identifiers", me.unmask_reason_required ? "Allowed for permitted roles with a written reason; every view is logged"
        : "Allowed for permitted roles; every view is logged"]])),
    admin.length ? el("div", { class: "panel" }, el("h3", {}, "Administration"), table(["Page", "What it is for"], admin.map(([name, , ic, path, text]) =>
      linkRow(el("span", { class: "type-cell" }, icon(ic), name), () => dispatch(path), el("td", {}, text))))) : null,
    el("div", { class: "panel" }, el("h3", {}, "Keyboard shortcuts"), kv([["Ctrl + K", "Search projects and runs"],
      ["S, D, U", "Review: Same person, Different people, Not sure"], ["N, P", "Review: next and previous pair"], ["Escape", "Close a menu or dialog"]])));
}
