"""Read endpoints of a run (review queue, people, comparisons) and of a project (rules and their history).

Every response is built from an explicit model (property-level allow-list): a column that is added to an output file later does not reach a
client until someone lists it here. Identifiers (PAN, phone, email, document numbers) are masked unless the caller asks to see them AND holds
`pii.unmask` (and gives a reason when the deployment requires one); each such view is written to the security log (who, which person, which
fields, and the reason when given) and is rate limited.
Names, dates of birth and addresses are shown to everyone who may read people: that is a policy choice stewards need, not a masking guarantee.
"""
import json
from pathlib import Path as Path_
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from dedupe.governance.diff import describe_changes
from dedupe.governance.store import ConfigError
from dedupe.identity import successor
from dedupe.review.service import SENSITIVE, ReviewError

from .auth import Principal
from .guards import _context, _services, require
from .scope import PROJECT_ID, RUN_ID, project_scope, run_scope

PERSON_ID = r"^P\d{9}$"
RUN = "/projects/{project_id}/runs/{run_id}"
PROJ = "/projects/{project_id}"
router = APIRouter()
READ_QUEUE = Depends(require("queue.read"))
READ_PERSON = Depends(require("person.read"))
READ_CONFIG = Depends(require("config.read"))
MIN_REASON = 10
PID = Path(pattern=PROJECT_ID)
RID = Path(pattern=RUN_ID)
CONFIG_NAMES = ("decision", "survivorship", "settings")
ConfigName = Literal[CONFIG_NAMES]            # type: ignore[valid-type]


# ---- response models: the allow-list of what leaves the system ---------------------------------------------------------
class QueueItem(BaseModel):
    person_l: str
    person_r: str
    record_pairs: int
    top_probability: float
    mean_probability: float
    forced: bool
    size_l: int
    size_r: int
    priority: float
    state: str
    name_l: str | None = None          # the golden names of both people and the first one's city, for work lists
    name_r: str | None = None
    city_l: str | None = None


class RecordView(BaseModel):
    source_id: str
    source_record_id: str
    name_as_written: str | None = None
    name_clean: str | None = None
    dob: str | None = None
    gender: str | None = None
    mobile: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    pincode: str | None = None
    pan: str | None = None
    aadhaar_last4: str | None = None
    passport_no: str | None = None
    dl_no: str | None = None
    voter_id: str | None = None


class PersonView(BaseModel):
    person_id: str
    golden: dict[str, Any]
    record_count: int
    sources: list[str] | None = None
    conflict_fields: list[str] = []
    records: list[RecordView]
    extra: dict[str, Any] = {}          # the sources' own extra columns in the golden record (masked unless revealed)
    unmasked: bool = False


class Contribution(BaseModel):
    comparison: str
    level: str
    bits: float


class JudgmentView(BaseModel):
    id: int
    kind: str
    actor: str
    status: str
    reason: str


class Comparison(BaseModel):
    persons: list[PersonView]
    record_pairs: int
    why_match: list[Contribution]
    why_not: list[Contribution]
    probability: float | None = None
    decided_by: str | None = None
    reason: str | None = None
    hard_conflicts: list[str]
    existing_judgments: list[JudgmentView]
    unmasked: bool = False


class QueueSummary(BaseModel):
    queue_items_in_last_run: int
    open_now: int | None
    active_judgments: dict[str, int]
    pending_approval: int
    chain_ok: bool


class VersionView(BaseModel):
    name: str
    version: int
    status: str
    proposed_by: str
    proposed_at: str
    reason: str
    parent_version: int | None
    content_hash: str
    rollback: bool
    approved_by: list[str]
    declined_by: str | None
    declined_reason: str | None = None
    activated_at: str | None
    activated_by: str | None
    reviewed: bool = False                 # a second person approved this content (only such earlier versions may run or be restored when a second approval is required)


class ConfigHistoryRow(BaseModel):
    id: int
    created_at: str
    actor: str
    kind: str
    name: str
    version: int
    parent_version: int | None
    content_hash: str
    reason: str


# ---- helpers --------------------------------------------------------------------------------------------------------
def _not_found() -> HTTPException:
    return HTTPException(404, "Not found.")


def _unmask_gate(request: Request, p: Principal, unmask: bool, reason: str | None, object_ref: str) -> bool:
    """True when identifiers may be shown. Needs the permission and stays within the unmask rate limit; a written reason only when the deployment
    asks for one (DEDUPE_UNMASK_REASON_REQUIRED). Every view is logged by `_log_unmask` (who, when, what), with the reason when one was given."""
    if not unmask:
        return False
    sv = _services(request)
    if "pii.unmask" not in p.permissions:
        sv.seclog.record("permission_denied", actor=p.actor, roles=p.roles, status=403, object_ref=object_ref, detail={"needs": "pii.unmask"}, **_context(request))
        raise HTTPException(403, "You do not have permission to do this.")
    if sv.settings.unmask_reason_required and (not reason or len(reason.strip()) < MIN_REASON):
        raise HTTPException(422, f"Showing identifiers needs a reason of at least {MIN_REASON} characters.")
    if not sv.limiter.allow(f"unmask:{p.sub}", sv.settings.unmask_limit_per_min):
        sv.seclog.record("rate_limited", actor=p.actor, roles=p.roles, status=429, object_ref=object_ref, detail={"scope": "unmask"}, **_context(request))
        raise HTTPException(429, "Too many requests to see identifiers.", headers={"Retry-After": "60"})
    return True


def _log_unmask(request: Request, p: Principal, object_ref: str, reason: str | None, what: str) -> None:
    _services(request).seclog.record("pii_unmasked", actor=p.actor, roles=p.roles, status=200, object_ref=object_ref,
                                     detail={"fields": list(SENSITIVE), "reason": (reason or "").strip(), "view": what}, **_context(request))


# ---- queue -------------------------------------------------------------------------------------------------------------
@router.get(RUN + "/queue", response_model=list[QueueItem])
def queue(request: Request, project_id: str = PID, run_id: str = RID, limit: int = Query(50, ge=1), offset: int = Query(0, ge=0, le=100_000),
          status: Literal["open", "unsure", "all"] = "open", min_probability: float = Query(0.0, ge=0.0, le=1.0),
          max_probability: float = Query(1.0, ge=0.0, le=1.0), forced: bool | None = None, q: str | None = Query(None, max_length=64),
          p: Principal = READ_QUEUE):
    rs = run_scope(request, project_id, run_id)
    rows = rs.review.queue(limit=min(limit, _services(request).settings.max_page_size), offset=offset, status=status, min_probability=min_probability,
                           max_probability=max_probability, forced=forced, text=q)
    return [QueueItem(person_l=r["cluster_l"], person_r=r["cluster_r"], **{k: r[k] for k in (
        "record_pairs", "top_probability", "mean_probability", "forced", "size_l", "size_r", "priority", "state", "name_l", "name_r", "city_l") if k in r}) for r in rows]


@router.get(RUN + "/queue/summary", response_model=QueueSummary)
def queue_summary(request: Request, project_id: str = PID, run_id: str = RID, p: Principal = READ_QUEUE):
    return run_scope(request, project_id, run_id).review.summary()


# ---- people and comparisons --------------------------------------------------------------------------------------------
@router.get(RUN + "/people/{person_id}", response_model=PersonView)
def person(request: Request, project_id: str = PID, run_id: str = RID, person_id: str = Path(pattern=PERSON_ID), unmask: bool = False,
           reason: str | None = Query(None, max_length=300), p: Principal = READ_PERSON):
    rs = run_scope(request, project_id, run_id)
    ref = f"{project_id}/{run_id}/{person_id}"
    reveal = _unmask_gate(request, p, unmask, reason, ref)
    try:
        out = (rs.review_reveal if reveal else rs.review).person(person_id, reveal=reveal)
    except ReviewError:
        now = successor(_services(request).workspace.identity_dir(project_id), person_id)       # a retired ID: say where its records went
        raise (HTTPException(404, f"This customer was merged into {now}.") if now else _not_found()) from None
    out["unmasked"] = reveal
    if reveal:
        _log_unmask(request, p, ref, reason, "person")
    return out


@router.get(RUN + "/people/{person_l}/compare/{person_r}", response_model=Comparison)
def compare(request: Request, project_id: str = PID, run_id: str = RID, person_l: str = Path(pattern=PERSON_ID), person_r: str = Path(pattern=PERSON_ID),
            unmask: bool = False, reason: str | None = Query(None, max_length=300), p: Principal = READ_PERSON):
    rs = run_scope(request, project_id, run_id)
    if person_l == person_r:
        raise HTTPException(422, "Choose two different people.")
    ref = f"{project_id}/{run_id}/{person_l},{person_r}"
    reveal = _unmask_gate(request, p, unmask, reason, ref)
    try:
        out = (rs.review_reveal if reveal else rs.review).explain(person_l, person_r, reveal=reveal)
    except ReviewError:
        raise _not_found() from None
    out["unmasked"] = reveal
    for person_view in out["persons"]:
        person_view["unmasked"] = reveal
    if reveal:
        _log_unmask(request, p, ref, reason, "comparison")
    return out


# ---- the project's rules (read only) -----------------------------------------------------------------------------------
@router.get(PROJ + "/config/{name}", response_model=list[VersionView])
def config_versions(request: Request, name: ConfigName, project_id: str = PID, p: Principal = READ_CONFIG):
    return [VersionView(**{**v.__dict__, "approved_by": list(v.approved_by)}) for v in project_scope(request, project_id).configs.versions(name)]


@router.get(PROJ + "/config/{name}/versions/{version}")
def config_version(request: Request, name: ConfigName, project_id: str = PID, version: int = Path(ge=1, le=1_000_000), p: Principal = READ_CONFIG):
    try:
        return {"name": name, "version": version, "content": project_scope(request, project_id).configs.get(name, version)}
    except ConfigError:
        raise _not_found() from None


DEFAULT_FILES = {"decision": "decision_default.json", "survivorship": "survivorship_default.json"}


_SIMILAR_NAME = {"comparison": "name", "in": ["identical cleaned name", "same name sounds (any order)", "first+last sound equal", "initial + same surname",
                                              "one side first-name only, first names agree", "fuzzy full-name match (JW>=0.90)"]}
_SAME_BIRTH_DATE = {"comparison": "dob", "in": ["exact date", "exact date, but a 1-Jan default", "day and month swapped"]}
PROJECT_MATCHES = [
    {"id": "match_name_dob_phone", "type": "auto_merge", "reason": "Name is similar, and date of birth is the same, and phone shares any number",
     "when": {"all": [_SIMILAR_NAME, _SAME_BIRTH_DATE, {"comparison": "phone", "in": ["same primary number", "number matches an alternate number"]}]}},
    {"id": "match_name_dob_email", "type": "auto_merge", "reason": "Name is similar, and date of birth is the same, and email is the same",
     "when": {"all": [_SIMILAR_NAME, _SAME_BIRTH_DATE, {"comparison": "email", "is": "same email"}]}},
    {"id": "match_name_dob_house", "type": "auto_merge", "reason": "Name is similar, and date of birth is the same, and address is the same house",
     "when": {"all": [_SIMILAR_NAME, _SAME_BIRTH_DATE, {"comparison": "address", "is": "same PIN + house number + similar text"}]}},
]


def _default_content(name: str) -> dict:
    """What a run uses when the project has no live version: the shipped file (decision, survivorship) or the settings' own defaults."""
    if name == "settings":
        from dedupe.governance.settings import MatchingSettings
        return MatchingSettings().model_dump(mode="json")
    for base in (Path_.cwd(), Path_(__file__).resolve().parents[3]):
        f = base / "configs" / DEFAULT_FILES[name]
        if f.exists():
            content = json.loads(f.read_text(encoding="utf-8"))
            if name != "decision":
                return content
            # A project starts from rules only: a pair no rule catches stays apart until the project lets the score decide. So that
            # records without a PAN or Aadhaar can still match, the start adds three column matches (name, birth date and one contact).
            return {**content, "use_score": False, "rules": content["rules"] + PROJECT_MATCHES}
    raise _not_found()


@router.get(PROJ + "/config/{name}/default")
def config_default(request: Request, name: ConfigName, project_id: str = PID, p: Principal = READ_CONFIG):
    """The built-in content, as a starting point for a project's first version."""
    ps = project_scope(request, project_id)
    content = _default_content(name)
    own = project_spec(ps.configs).custom if name == "decision" else ()
    if own:
        # A project that matches on its own fields: the person rules alone would never merge its records. Each identifier of its own
        # decides like a PAN does, and the score decides the rest (sure pairs merge, doubtful ones go to a person).
        content = {**content, "use_score": True, "rules": content["rules"] + [
            {"id": f"match_own_{f.name}", "type": "auto_merge", "reason": f"{f.label} is the same", "when": {"comparison": f.col, "is": "identical"}}
            for f in own if f.identifier]}
    return {"name": name, "content": content}


# What the no-code rule builder offers: each comparison with its outcomes in words (the same words the rules are written in).
COMPARISON_TITLES = {"name": "Name", "dob": "Date of birth", "phone": "Phone", "email": "Email", "pan": "PAN", "aadhaar": "Aadhaar",
                     "gov_id": "Passport / licence / voter ID", "address": "Address", "gender": "Gender", "customer_number": "Customer number"}
RULE_TYPES = {"never_merge": "Never merge the two records (a veto: wins over everything else)",
              "force_review": "Always ask a person, never merge automatically",
              "auto_merge": "Always merge them (unless a veto applies)",
              "not_enough_evidence": "Treat as not enough evidence: neither merge nor ask"}


@router.get("/rules-catalogue")
def rules_catalogue(p: Principal = READ_CONFIG):
    from dedupe.scoring.comparisons import BUILTIN as COMPARISONS
    comps = [{"name": c.name, "title": COMPARISON_TITLES.get(c.name, c.name),
              "levels": [lv.label for lv in c.levels] + [c.else_label]} for c in COMPARISONS]
    return {"comparisons": comps, "rule_types": RULE_TYPES}


# Built-in columns a project's own search rule may combine with its fields, in words
BLOCK_COLUMNS = {"address_pin": "PIN code", "city": "City", "state": "State", "dob": "Date of birth", "dob_year": "Birth year", "name_last_phon": "Surname (by sound)",
                 "name_first_phon": "First name (by sound)", "mobile": "Mobile", "email_key": "Email", "gender": "Gender", "customer_number": "Customer number"}


def project_settings(configs) -> tuple[dict, bool]:
    """The project's matching settings as they will be once everything waiting is in use (the newest version that was not declined), and
    whether that version is in use already. Empty when the project never saved any."""
    vs = [v for v in configs.versions("settings") if v.status in ("proposed", "approved", "active")]
    return (configs.get("settings", vs[-1].version), vs[-1].status == "active") if vs else ({}, True)


def project_spec(configs):
    """The spec (dedupe.custom) of those settings; the built-in one when they cannot be read."""
    from dedupe.custom import build_spec
    from dedupe.scoring.comparisons import MatchSpec
    s, _ = project_settings(configs)
    try:
        return build_spec(s.get("custom_fields"), s.get("custom_blocking"), s.get("fixed_weights"), s.get("compare_within_source", True))
    except ValueError:
        return MatchSpec()


@router.get(PROJ + "/rules-catalogue")
def project_rules_catalogue(request: Request, project_id: str = PID, p: Principal = READ_CONFIG):
    """What the rule and field forms of one project offer: the built-in comparisons, the project's own fields (how two records can agree on
    each, in words) and what a search rule can be made of. No data, only names the project chose."""
    from dedupe.blocking.rules import ALL_RULES, DEFAULT_RULES
    from dedupe.custom import KINDS
    from dedupe.custom.fields import HOW
    ps = project_scope(request, project_id)
    spec = project_spec(ps.configs)
    settings, in_use = project_settings(ps.configs)
    own = {c.name for c in spec.comparisons} - {c["name"] for c in rules_catalogue(p)["comparisons"]}
    comps = [{"name": c.name, "title": c.title or COMPARISON_TITLES.get(c.name, c.name), "own": c.name in own,
              "levels": [lv.label for lv in c.levels] + [c.else_label]} for c in spec.comparisons]
    in_rules = {part.get("field") for r in settings.get("custom_blocking") or [] for part in r.get("parts") or []}
    finds = {f.name: bool(f.default_blocking()) or f.name in in_rules for f in spec.custom}     # can a rule on this field alone find anything
    fields = [{"name": f.name, "comparison": f.col, "title": f.label, "kind": f.kind, "identifier": f.identifier,
               "ways": [{"words": w, "levels": ls} for w, ls in f.ways()], "differs": "different", "finds_pairs": finds[f.name]} for f in spec.custom]
    return {"comparisons": comps, "rule_types": RULE_TYPES, "own_fields": fields, "settings_in_use": in_use, "kinds": KINDS,
            "search": {"built_in": [{"name": r.name, "title": r.description or r.name, "standard": r in DEFAULT_RULES} for r in ALL_RULES.values()],
                       "how": HOW, "columns": BLOCK_COLUMNS}}


@router.get(PROJ + "/golden-record-guide")
def golden_record_guide(request: Request, project_id: str = PID, p: Principal = READ_CONFIG):
    """What the golden-record rules screen needs: the fields and strategies, the project's sources (their extra columns, whether a record
    date is mapped) and how the latest run actually ranked the sources. No personal data."""
    from dedupe.survivorship.config import GOLDEN_FIELDS, STRATEGY_HELP
    ps = project_scope(request, project_id)
    sources = []
    for name in ps.configs.names("connection/"):
        act = ps.configs.active(name)
        if act is None:
            continue
        c = act.content
        carried = list(dict.fromkeys([*(c.get("attributes") or []), *(c.get("custom") or {})]))      # a project's own fields are carried too
        sources.append({"id": c["source_id"], "attributes": carried, "record_date_mapped": "record_date" in (c.get("fields") or {})})
    from dedupe.survivorship.config import EXTRA_NAME, extra_column
    names = sorted({a for s in sources for a in s["attributes"]})
    extras = [{"name": a, "column": extra_column(a), "carried": bool(EXTRA_NAME.fullmatch(a)),
               "sources": [s["id"] for s in sources if a in s["attributes"]]} for a in names]
    sv = _services(request)
    last, used = sv.jobs.latest_succeeded(project_id), None
    if last:
        f = sv.workspace.run_dir(project_id, last.id) / "pipeline_summary.json"
        g = (json.loads(f.read_text(encoding="utf-8")).get("golden_record") or {}) if f.exists() else {}
        used = {"run_id": last.id, **{k: g.get(k) for k in ("trust_order", "auto_ranked_sources", "sources_without_dates", "shared_values_passed_over")}}
    return {"fields": list(GOLDEN_FIELDS), "strategies": STRATEGY_HELP, "sources": sources, "extra_columns": extras, "last_run": used}


@router.get(PROJ + "/config/{name}/diff", response_model=list[str])
def config_diff(request: Request, name: ConfigName, project_id: str = PID, old: int = Query(ge=1, le=1_000_000), new: int = Query(ge=1, le=1_000_000),
                p: Principal = READ_CONFIG):
    store = project_scope(request, project_id).configs
    try:
        return describe_changes(name, store.get(name, old), store.get(name, new))
    except ConfigError:
        raise _not_found() from None


@router.get(PROJ + "/config/{name}/history", response_model=list[ConfigHistoryRow])
def config_history(request: Request, name: ConfigName, project_id: str = PID, p: Principal = READ_CONFIG):
    return project_scope(request, project_id).configs.history(name)


@router.get(PROJ + "/config-log/verify")
def config_log_verify(request: Request, project_id: str = PID, p: Principal = READ_CONFIG):
    return project_scope(request, project_id).configs.verify()
