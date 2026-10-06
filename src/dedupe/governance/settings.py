"""Run-level matching settings that business users may change (and that must therefore be versioned and audited)."""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dedupe.blocking.rules import ALL_RULES, DEFAULT_RULES


class MatchingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # which blocking rules generate candidate pairs (a pair no rule proposes is never compared); None = the defaults
    blocking_rules: list[str] = Field(default_factory=lambda: [r.name for r in DEFAULT_RULES])
    blocking_cap: int = Field(50, ge=2, le=1000)         # a key shared by more records than this is treated as generic and skipped
    household_threshold: float = Field(5.0, gt=0)        # evidence needed (in bits) to link two people into a household
    max_cluster_size: int = Field(15, ge=2, le=1000)     # a customer group larger than this is split by the contradiction guard
    review_min_probability: float = Field(0.0, ge=0, lt=1)   # queue items whose best pair scores below this are dropped
    review_cap: int | None = Field(None, ge=1)           # keep only the top N queue items

    @field_validator("blocking_rules")
    @classmethod
    def _rules(cls, v):
        unknown = [n for n in v if n not in ALL_RULES]
        if unknown:
            raise ValueError(f"unknown blocking rule(s) {unknown}; valid: {sorted(ALL_RULES)}")
        if not v or len(set(v)) != len(v):
            raise ValueError("blocking_rules must be a non-empty list without duplicates")
        return v

    @classmethod
    def load(cls, path: str | Path) -> "MatchingSettings":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
