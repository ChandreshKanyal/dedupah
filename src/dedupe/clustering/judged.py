"""Steward judgments -> clustering constraints, and an audit of whether each one held.

Policy (PROGRESS.md section 25):
  * a steward decision beats automation: 'same person' is an edge heavier than any automatic one, 'different person' and
    'split' are hard separations (the records inside each split part are glued together so the cut falls between the parts); a never-merge veto on exactly the same pair is dropped when a steward says same person;
  * the exception is a hard identifier conflict (two different valid PANs / Aadhaar numbers): a 'same person' decision that
    would join them is NOT applied and is reported, unless the steward explicitly confirmed it (`confirm_id_conflict`), in which
    case the identifiers of those two records are ignored by the contradiction guard;
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
    neutralise: set = field(default_factory=set)        # rids whose identifiers the guard must ignore (confirmed id conflicts)
    items: list = field(default_factory=list)           # what the audit checks: dicts with id, kind and rids
    missing: list = field(default_factory=list)         # [{id, kind, reason}]
    judged_pairs: set = field(default_factory=set)      # (min rid, max rid) with any pair decision, for the review queue

    def summary(self) -> dict:
        kinds: dict = {}
        for it in self.items:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
        return {"judgments_applied_to_run": len(self.items), "by_kind": kinds, "missing_records": len(self.missing)}


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
            cons.items.append({"id": j.id, "kind": j.kind, "a": a, "b": b})
            if j.kind == "same_person":
                cons.must.append((a, b, STEWARD_WEIGHT + j.id * 1e-9))
                if j.evidence.get("confirmed_id_conflict"):
                    cons.neutralise.update((a, b))
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
            cons.items.append({"id": j.id, "kind": "split", "parts": parts})
    return cons


def drop_vetoes_overridden_by_stewards(cannot: list, cons: Constraints) -> list:
    """A rule veto on exactly the pair a steward declared 'same person' is dropped (steward beats automation)."""
    must_pairs = {(min(a, b), max(a, b)) for a, b, _ in cons.must}
    steward_cannot = {(min(a, b), max(a, b)) for a, b in cons.cannot}
    return [c for c in cannot if (min(c), max(c)) not in must_pairs or (min(c), max(c)) in steward_cannot]


def audit(cons: Constraints, labels: dict, ids: dict) -> dict:
    """Did each judgment hold in the final clusters? Returns counts and the list of unsatisfied ones with a reason."""
    ok, bad = 0, []
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
        if held:
            ok += 1
        else:
            bad.append({"id": it["id"], "kind": it["kind"], "reason": why})
    return {"held": ok, "not_held": len(bad), "unsatisfied": bad, "missing": cons.missing}
