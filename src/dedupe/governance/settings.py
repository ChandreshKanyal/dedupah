"""Run-level matching settings that business users may change (and that must therefore be versioned and audited)."""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dedupe.blocking.rules import ALL_RULES, DEFAULT_RULES
from dedupe.custom import BlockingRule, CustomField, build_spec
from dedupe.scoring.comparisons import MatchSpec


class MatchingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # which blocking rules generate candidate pairs (a pair no rule proposes is never compared); None = the defaults
    blocking_rules: list[str] = Field(default_factory=lambda: [r.name for r in DEFAULT_RULES])
    blocking_cap: int = Field(50, ge=2, le=1000)         # a key shared by more records than this is treated as generic and skipped
    household_threshold: float = Field(5.0, gt=0)        # evidence needed (in bits) to link two people into a household
    max_cluster_size: int = Field(15, ge=2, le=1000)     # a customer group larger than this is flagged for a person to check (never cut blindly)
    review_min_probability: float = Field(0.0, ge=0, lt=1)   # queue items whose best pair scores below this are dropped
    review_cap: int | None = Field(None, ge=1)           # keep only the top N queue items
    # ---- what is compared, beyond the built-in customer fields (dedupe.custom) ----
    custom_fields: list[CustomField] = Field(default_factory=list, max_length=40)     # the project's own fields: any column, compared its own way
    custom_blocking: list[BlockingRule] = Field(default_factory=list, max_length=40)  # its own rules for finding candidate pairs
    # comparison -> level (as worded in the model, or "*" for all) -> bits: a weight set by hand instead of learned ({"phone": {"*": 0}}
    # means the phone never counts)
    fixed_weights: dict[str, dict[str, float]] = Field(default_factory=dict)
    # what one record of this project is called on screen ("vendor", "product"); empty = customer for person data, record otherwise
    record_name: str = Field("", max_length=30, pattern=r"^[A-Za-z][A-Za-z ]*$|^$")
    compare_within_source: bool = True                   # False: only records of different sources are compared (linking two files)

    @field_validator("blocking_rules")
    @classmethod
    def _rules(cls, v):
        unknown = [n for n in v if n not in ALL_RULES]
        if unknown:
            raise ValueError(f"unknown blocking rule(s) {unknown}; valid: {sorted(ALL_RULES)}")
        if len(set(v)) != len(v):
            raise ValueError("blocking_rules must not repeat a rule")
        return v

    @model_validator(mode="after")
    def _fits_together(self):
        spec = self.spec()                    # raises when the project's fields, rules and weights do not fit together
        if not self.blocking_rules and not spec.blocking:
            raise ValueError("no blocking rule is left: no two records would ever be compared")
        return self

    def spec(self) -> MatchSpec:
        return build_spec(self.custom_fields, self.custom_blocking, self.fixed_weights, self.compare_within_source)

    @classmethod
    def load(cls, path: str | Path) -> "MatchingSettings":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
