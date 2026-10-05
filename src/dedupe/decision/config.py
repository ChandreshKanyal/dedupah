import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from .conditions import compile_condition

RuleType = Literal["never_merge", "auto_merge", "force_review", "not_enough_evidence"]
# Precedence when several rules fire: a veto always wins, then forced review, then auto-merge, then 'not enough evidence'
# (a soft reject: the pair is neither merged nor reviewed, but unlike a veto it does not forbid the two records from ending
# up together through other evidence), then the score zones.
PRECEDENCE = ("never_merge", "force_review", "auto_merge", "not_enough_evidence")


class Rule(BaseModel):
    id: str
    type: RuleType
    when: dict
    reason: str = ""
    enabled: bool = True
    priority_offset: float = 0.0   # force_review only: added to the pair's probability when the review queue is ranked (negative = look at later)

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", v):
            raise ValueError("rule id must be lowercase letters, digits, underscores")
        return v

    @model_validator(mode="after")
    def _compiles(self):
        compile_condition(self.when)  # fail at load time, not at run time
        return self


class DecisionConfig(BaseModel):
    auto_merge_at: float = 0.9
    review_at: float = 0.1
    rules: list[Rule] = []

    @model_validator(mode="after")
    def _check(self):
        if not 0 < self.review_at < self.auto_merge_at <= 1:
            raise ValueError("need 0 < review_at < auto_merge_at <= 1")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids")
        return self

    @classmethod
    def load(cls, path: str | Path) -> "DecisionConfig":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
