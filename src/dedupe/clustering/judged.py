"""Steward judgments -> clustering constraints, and an audit of whether each one held.

Policy (docs/DEVELOPMENT_LOG.md section 25):
  * a steward decision beats automation: 'same person' is an edge heavier than any automatic one, 'different person' and
    'split' are hard separations (the records inside each split part are glued together so the cut falls between the parts); a never-merge veto on exactly the same pair is dropped when a steward says same person;
  * 'same person' joins the two PEOPLE the steward compared, not only the record pair it was stored on: a rule veto between any record of
    one and any record of the other (as the automatic links group them) is dropped too (agent review E2-02: a veto on another pair used to
    split a record off while the audit said "held");
  * the exception is a hard identifier conflict (two different valid PANs / Aadhaar numbers): a 'same person' decision that
    would join them is NOT applied and is reported, unless the steward explicitly confirmed it (`confirm_id_conflict`), in which
    case the guard treats exactly those two values as one (`id_aliases`); any third value still conflicts (E2-03: the records' identifiers
    used to be ignored altogether, so a record with yet another PAN merged in);
  * two judgments that truly contradict each other (A=B, B=C, A!=C): the newer one wins and the older one is reported;
  * judgments about records that are not in this run's data are ignored and reported (`missing`).
"""
from dataclasses import dataclass, field

STEWARD_WEIGHT = 2.0   # automatic edges are probabilities (<= 1)
PART_GLUE_WEIGHT = 1.5  # inside a split part: stronger than any automatic edge, weaker than an explicit 'same person' decision


@dataclass
class Constraints:
    must: list = field(default_factory=list)            # (rid_a, rid_b, weight)
    cannot: list = field(default_factory=list)          # (rid_a, rid_b)
    separations: dict = field(default_factory=dict)     # rid -> [(split_id, part_no)]
    confirmed: list = field(default_factory=list)       # (rid_a, rid_b) of 'same person' decisions that confirm an identifier conflict
    items: list = field(default_factory=list)           # what the audit checks: dicts with id, kind and rids
    missing: list = field(default_factory=list)         # [{id, kind, reason}]
    judged_pairs: set = field(default_factory=set)      # (min rid, max rid) with any pair decision, for the review queue

    def summary(self) -> dict:
        """Counted per DECISION a person made (one "different people" covers every record pair between the two: U2-08), not per pair."""
        actions = {(it["kind"], it.get("action", it["id"])) for it in self.items}
        kinds: dict = {}
        for kind, _ in actions:
            kinds[kind] = kinds.get(kind, 0) + 1
        return {"judgments_applied_to_run": len(actions), "by_kind": kinds, "missing_records": len(self.missing)}


def constraints_from_store(con, store) -> Constraints:
    """Needs table `records` (rid, source_id, source_record_id) in `con` and a JudgmentStore."""
    rid = {(s, r): i for i, s, r in con.execute("SELECT rid, source_id, source_record_id FROM records").fetchall()}
    cons = Constraints()

    def look(j, keys):
        found = [rid.get(tuple(k)) for k in keys]
        if any(x is None for x in found):
            cons.missing.append({"id": j.id, "kind": j.kind, "reason": "a record of this judgment is not in the current data"})
            return None
        return found

    for j in store.active():
        sub = j.subject
        if j.kind in ("same_person", "different_person", "unsure"):
            ab = look(j, [sub["a"], sub["b"]])
            if ab is None:
                continue
            a, b = ab
            cons.judged_pairs.add((min(a, b), max(a, b)))
            if j.kind == "unsure":
                continue
            cons.items.append({"id": j.id, "kind": j.kind, "a": a, "b": b, "action": j.evidence.get("action_id") or j.id})
            if j.kind == "same_person":
                cons.must.append((a, b, STEWARD_WEIGHT + j.id * 1e-9))
                if j.evidence.get("confirmed_id_conflict"):
                    cons.confirmed.append((a, b))
            else:
                cons.cannot.append((a, b))
        elif j.kind == "split":
            parts = []
            for part in sub["parts"]:
                got = [rid[tuple(k)] for k in part if tuple(k) in rid]
                if got:
                    parts.append(got)
            if len(parts) < 2:
                cons.missing.append({"id": j.id, "kind": "split", "reason": "fewer than two parts have records in the current data"})
                continue
            for n, part in enumerate(parts):
                for r in part:
                    cons.separations.setdefault(r, []).append((j.id, n))
                # the steward listed these records as one person: keep them together, so the guard cuts BETWEEN the parts
                cons.must.extend((part[0], r, PART_GLUE_WEIGHT) for r in part[1:])
            cons.items.append({"id": j.id, "kind": "split", "parts": parts, "action": j.evidence.get("action_id") or j.id})
    return _newer_wins_over_splits(cons)


def _newer_wins_over_splits(cons: Constraints) -> Constraints:
    """A 'same person' and a split that put the same two records on different sides contradict each other: the newer one wins and the older
    one is not applied (reported in `missing`). Both used to be applied, so neither held and the people were torn (agent review E2-06)."""
    splits = [it for it in cons.items if it["kind"] == "split"]
    if not splits:
        return cons
    side = {it["id"]: {r: n for n, part in enumerate(it["parts"]) for r in part} for it in splits}
    dropped = set()
    for it in [x for x in cons.items if x["kind"] == "same_person"]:
        for sp in splits:
            parts = side[sp["id"]]
            if it["a"] in parts and it["b"] in parts and parts[it["a"]] != parts[it["b"]] and not {it["id"], sp["id"]} & dropped:
                dropped.add(min(it["id"], sp["id"]))
    if not dropped:
        return cons
    old = {it["id"]: it for it in cons.items if it["id"] in dropped}
    cons.items = [it for it in cons.items if it["id"] not in dropped]
    cons.missing += [{"id": i, "kind": old[i]["kind"], "reason": "replaced by a newer decision about the same records"} for i in sorted(dropped)]
    glue = {(it["parts"][n][0], r) for it in old.values() if it["kind"] == "split" for n in range(len(it["parts"])) for r in it["parts"][n][1:]}
    pairs = {(it["a"], it["b"]) for it in old.values() if it["kind"] == "same_person"}
    cons.must = [m for m in cons.must if not ((m[0], m[1]) in pairs and m[2] >= STEWARD_WEIGHT) and not ((m[0], m[1]) in glue and m[2] == PART_GLUE_WEIGHT)]
    gone = {i for i in dropped if old[i]["kind"] == "split"}
    cons.separations = {r: kept for r, seps in cons.separations.items() if (kept := [x for x in seps if x[0] not in gone])}
    return cons


def drop_vetoes_overridden_by_stewards(cannot: list, cons: Constraints, edges=()) -> list:
    """Rule vetoes a steward's 'same person' overrides (steward beats automation): every veto between the two people the decision joins, as
    the automatic links (`edges`) group the records; a steward's own 'different person' always stays."""
    parent: dict = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    for a, b, *_ in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    joined = {frozenset((find(it["a"]), find(it["b"]))) for it in cons.items if it["kind"] == "same_person"}
    steward_cannot = {(min(a, b), max(a, b)) for a, b in cons.cannot}
    return [c for c in cannot if frozenset((find(c[0]), find(c[1]))) not in joined or (min(c), max(c)) in steward_cannot]


def id_aliases(cons: Constraints, ids: dict) -> dict:
    """{(kind, value): canonical value}: for each confirmed identifier conflict, the two values the steward said belong to one person count as
    one value in the contradiction guard (and nothing else does)."""
    parent: dict = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for a, b in cons.confirmed:
        for kind in ("pan", "aadhaar"):
            va, vb = ids.get(a, {}).get(kind), ids.get(b, {}).get(kind)
            if va and vb and va != vb:
                ra, rb = find((kind, va)), find((kind, vb))
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    return {k: find(k)[1] for k in parent}


def audit(cons: Constraints, labels: dict, ids: dict) -> dict:
    """Did each judgment hold in the final clusters? Returns counts and the list of unsatisfied ones with a reason."""
    bad = []
    for it in cons.items:
        if it["kind"] == "same_person":
            held = labels[it["a"]] == labels[it["b"]]
            why = None
            if not held:
                ia, ib = ids.get(it["a"], {}), ids.get(it["b"], {})
                if any(ia.get(k) and ib.get(k) and ia[k] != ib[k] for k in ("pan", "aadhaar")):
                    why = "identifier_conflict_not_confirmed"
                else:
                    why = "contradicts_a_newer_judgment_or_a_split"
        elif it["kind"] == "different_person":
            held, why = labels[it["a"]] != labels[it["b"]], "kept_together_by_automation"
        else:
            seen: dict = {}
            held = True
            for n, part in enumerate(it["parts"]):
                for r in part:
                    other = seen.setdefault(labels[r], n)
                    if other != n:
                        held = False
            why = "parts_still_together"
        if not held:
            bad.append({"id": it["id"], "kind": it["kind"], "reason": why, "action": it.get("action", it["id"])})
    # per decision, like the summary: a decision is kept when every record pair it covers is
    failed = {(b["kind"], b["action"]) for b in bad}
    decisions = {(it["kind"], it.get("action", it["id"])) for it in cons.items}
    ok = len(decisions - failed)
    return {"held": ok, "not_held": len(failed), "unsatisfied": bad, "missing": cons.missing}
