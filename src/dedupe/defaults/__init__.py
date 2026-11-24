"""The shipped starting points: the standard matching rules, the golden-record rules, the starter rule for records without identifiers, and
the three ready-made settings the portal offers (measured on synthetic data, docs/DEVELOPMENT_LOG.md section 104).

The JSON files here are the packaged copies of configs/decision_default.json and configs/survivorship_default.json (a test keeps them equal),
so an installed package (pip install) works without the repository's configs folder.
"""
import copy
import json
from importlib import resources
from pathlib import Path

FILES = {"decision": "decision_default.json", "survivorship": "survivorship_default.json"}

_SIMILAR_NAME = {"comparison": "name", "in": ["identical cleaned name", "same name sounds (any order)", "first+last sound equal", "initial + same surname",
                                              "one side first-name only, first names agree", "fuzzy full-name match (JW>=0.90)"]}
_SAME_BIRTH_DATE = {"comparison": "dob", "in": ["exact date", "exact date, but a 1-Jan default", "day and month swapped"]}
# One rule, not three: the name and the birth date are the core, and any one contact or the same house confirms it. The engine evaluates it
# exactly as three separate rules (name + birth date + phone / email / house) did.
STARTER_RULES = [
    {"id": "match_name_dob_contact", "type": "auto_merge",
     "reason": "Name is similar, and date of birth is the same, and phone, email or the house address agrees",
     "when": {"all": [_SIMILAR_NAME, _SAME_BIRTH_DATE, {"any": [{"comparison": "phone", "in": ["same primary number", "number matches an alternate number"]},
                                                                {"comparison": "email", "is": "same email"},
                                                                {"comparison": "address", "is": "same PIN + house number + similar text"}]}]}},
]
# The portal's ready-made settings. careful: rules only; balanced: the match score decides what no rule decides (recommended);
# catch_more: merges and asks from lower scores.
PRESETS = {"careful": {"use_score": False}, "balanced": {"use_score": True, "auto_merge_at": 0.9, "review_at": 0.1},
           "catch_more": {"use_score": True, "auto_merge_at": 0.8, "review_at": 0.05}}


def shipped(name: str) -> dict:
    """The shipped file as it is: the repository's configs folder when there is one (a development checkout), else the packaged copy."""
    for base in (Path.cwd(), Path(__file__).resolve().parents[3]):
        f = base / "configs" / FILES[name]
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    return json.loads(resources.files(__package__).joinpath(FILES[name]).read_text(encoding="utf-8"))


def decision(preset: str = "careful") -> dict:
    """The matching rules a new project starts with: the shipped rules plus the starter rule, under one of the ready-made settings."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose one of: {', '.join(PRESETS)}")
    content = shipped("decision")
    return {**content, **PRESETS[preset], "rules": content["rules"] + copy.deepcopy(STARTER_RULES)}


def survivorship() -> dict:
    return shipped("survivorship")


__all__ = ["decision", "survivorship", "shipped", "PRESETS", "STARTER_RULES"]
