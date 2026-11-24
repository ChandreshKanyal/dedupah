"""What the reviewers' decisions teach about the matching rules: suggestions a person can accept, never changes made on their own.

Every decision a reviewer makes is kept in force for the records it names, in every later run (dedupe.clustering.judged); that part is not
a suggestion. This module looks at all the decisions together: of the pairs a rule sent to people, or of the pairs whose score fell in a band,
how many did people call the same? When people (almost) always say the same, the rule or the band could merge without asking; when they
(almost) always say different, it could keep the pairs apart. Nothing changes until someone edits the rules (with approval, as always):
the decisions are few and are the hard cases, so learning on them blindly would skew the engine.
"""
from dataclasses import dataclass

BANDS = ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))
MIN_DECISIONS = 20          # fewer decisions in a group say too little to suggest anything
SURE = 0.95                 # share of one answer above which a suggestion is made


@dataclass(frozen=True)
class Decided:
    kind: str               # same_person | different_person
    decided_by: str | None  # the rule that sent the pair to people, or "score"
    probability: float | None


def _share(same: int, diff: int) -> float:
    return same / (same + diff) if same + diff else 0.0


def lessons(decided: list[Decided], content: dict) -> dict:
    """Counts by rule and by score band, and the suggestions they support. `content` is the project's matching rules in use."""
    rules = {r["id"]: r for r in content.get("rules", []) if isinstance(r, dict) and "id" in r}
    by_rule: dict[str, list[int]] = {}
    by_band = [[lo, min(hi, 1.0), 0, 0] for lo, hi in BANDS]
    for d in decided:
        same = d.kind == "same_person"
        key = d.decided_by or "score"
        by_rule.setdefault(key, [0, 0])[0 if same else 1] += 1
        if key == "score" and d.probability is not None:
            for b, (lo, hi) in zip(by_band, BANDS, strict=True):
                if lo <= d.probability < hi:
                    b[2 if same else 3] += 1
    tips = []
    for rid, (same, diff) in sorted(by_rule.items()):
        r = rules.get(rid)
        if rid == "score" or r is None or same + diff < MIN_DECISIONS or r.get("type") != "force_review":
            continue
        if _share(same, diff) >= SURE:
            tips.append({"kind": "rule_could_merge", "rule": rid, "same": same, "different": diff,
                         "text": f"People said the same for {same} of {same + diff} pairs this rule sent them. If that holds, the rule could merge such pairs without asking."})
        elif _share(diff, same) >= SURE:
            tips.append({"kind": "rule_could_keep_apart", "rule": rid, "same": same, "different": diff,
                         "text": f"People said different for {diff} of {same + diff} pairs this rule sent them. If that holds, the rule could keep such pairs apart."})
    auto, low = float(content.get("auto_merge_at", 0.9)), float(content.get("review_at", 0.1))
    if content.get("use_score", True):
        # the lowest band people always merge (counting down from the auto-merge level), and the highest band they always keep apart
        for lo, hi, same, diff in sorted(by_band, key=lambda b: -b[0]):
            if lo >= auto or hi <= low:            # bands wholly above merging or below asking were never people's to decide
                continue
            if same + diff >= MIN_DECISIONS and _share(same, diff) >= SURE:
                tips.append({"kind": "lower_auto_merge", "value": lo, "same": same, "different": diff,
                             "text": f"Of the pairs scored {round(lo * 100)}% to {round(hi * 100)}%, people said the same for {same} of {same + diff}. "
                                     f"Merging automatically from {round(lo * 100)}% (now {round(auto * 100)}%) would decide such pairs without asking."})
            else:
                break
        for lo, hi, same, diff in sorted(by_band, key=lambda b: b[0]):
            if lo >= auto or hi <= low:
                continue
            if same + diff >= MIN_DECISIONS and _share(diff, same) >= SURE:
                tips.append({"kind": "raise_review", "value": hi, "same": same, "different": diff,
                             "text": f"Of the pairs scored {round(lo * 100)}% to {round(hi * 100)}%, people said different for {diff} of {same + diff}. "
                                     f"Asking a person only from {round(hi * 100)}% (now {round(low * 100)}%) would keep such pairs apart without asking."})
            else:
                break
    total = sum(s + d for s, d in by_rule.values())
    return {"decisions": total, "min_decisions": MIN_DECISIONS,
            "by_rule": [{"rule": k, "same": s, "different": d} for k, (s, d) in sorted(by_rule.items(), key=lambda x: -sum(x[1]))],
            "by_band": [{"from": lo, "to": hi, "same": s, "different": d} for lo, hi, s, d in by_band if s + d],
            "suggestions": tips}


__all__ = ["Decided", "lessons", "MIN_DECISIONS"]
