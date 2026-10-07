"""Who may do what. Roles come from the customer's identity provider; this table is the only place permissions are defined.

Segregation of duties is built in: stewards decide pairs but cannot approve their own decisions; rule admins propose changes but approvers
activate them; platform admins start runs but cannot change decisions or see unmasked personal data; auditors read everything (including
unmasked data, which is logged) but change nothing. A person may hold several roles; the four-eyes checks compare identities, not roles,
so holding two roles never lets anyone approve their own work.
"""

PERMISSIONS = {
    "queue.read": "see the review queue and run summaries (masked)",
    "person.read": "see people, golden records and evidence (masked)",
    "pii.unmask": "see unmasked identifiers; every use is logged with a reason",
    "judgment.write": "approve / reject / mark unsure / split / override a field / undo (own decisions)",
    "judgment.approve": "give the second approval on decisions that need four eyes",
    "config.read": "see rules, settings and their versions and history",
    "config.propose": "propose a new rule / setting version and run its dry run",
    "config.approve": "approve or decline a proposed version",
    "config.activate": "make an approved version live, or roll back",
    "runs.read": "see pipeline runs and their status",
    "runs.start": "start a pipeline run",
    "sources.manage": "connect sources and edit field mappings",
    "logs.read": "read the security / access log and verify its integrity",
    "project.access": "grant or revoke who may work in a project",
    "projects.all": "open every project, including members-only ones (administration and oversight)",
    "users.manage": "create users, set their roles, disable them and reset their passwords",
    "roles.manage": "create, change and delete custom roles (which permissions each role holds)",
    "projects.create": "create projects (the creator becomes the project's owner)",
    "results.export": "download a run's results, or write them to a database (personal data leaves the platform: every export is logged)",
    "data.delete": "delete runs and whole projects (their files with personal data are erased; the audit trail keeps who deleted what and when)",
}

_READ = {"queue.read", "person.read", "runs.read", "config.read"}

ROLES: dict[str, frozenset] = {
    "viewer": frozenset(_READ),
    "steward": frozenset(_READ | {"judgment.write", "pii.unmask"}),
    "approver": frozenset(_READ | {"judgment.approve", "config.approve", "config.activate"}),
    "rule_admin": frozenset(_READ | {"config.propose"}),
    "auditor": frozenset(_READ | {"pii.unmask", "logs.read", "projects.all"}),
    "platform_admin": frozenset({"runs.read", "runs.start", "sources.manage", "config.read", "logs.read", "project.access", "projects.all", "projects.create",
                                  "data.delete"}),
    "analyst": frozenset(set(PERMISSIONS) - {"users.manage", "roles.manage", "logs.read", "projects.all", "project.access"}),    # everything in one's own projects
    "admin": frozenset(PERMISSIONS),          # everything, including managing users and roles; the four-eyes checks still compare identities
}
BUILTIN_DESCRIPTIONS = {
    "viewer": "Reads the review queue, people, rules and runs (masked).",
    "steward": "Decides pairs, splits and overrides; can see unmasked identifiers (logged).",
    "approver": "Gives the second approval on decisions and rule or connection changes, and makes them live.",
    "rule_admin": "Proposes rule and setting changes.",
    "auditor": "Reads everything including logs and unmasked identifiers; changes nothing.",
    "platform_admin": "Starts, cancels and deletes runs, connects sources, controls who works in each project, deletes projects.",
    "analyst": "Works in their own projects: connects data, runs, reviews, decides, approves, manages rules, exports results and deletes runs. No user, role or log administration.",
    "admin": "Every permission, plus creating users and roles.",
}

if not all(p in PERMISSIONS for perms in ROLES.values() for p in perms):
    raise RuntimeError("a role grants a permission that is not defined")


def permissions_for(roles) -> frozenset:
    return frozenset().union(*(ROLES[r] for r in roles if r in ROLES)) if roles else frozenset()


def roles_allowed(permission: str) -> frozenset:
    return frozenset(r for r, perms in ROLES.items() if permission in perms)
