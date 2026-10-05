"""Survivorship settings: which source value becomes the golden value, per field. Pure data (JSON), editable by business users."""
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

Strategy = Literal["most_trusted_source", "most_recent", "most_complete", "most_frequent"]
# Golden fields and where their values come from: cleaned/validated values from standardisation (s.), raw ones only where there is no clean form (k.).
def _initcap(col: str) -> str:
    return f"array_to_string(list_transform(string_split(lower({col}), ' '), w -> upper(w[1:1]) || w[2:]), ' ')"


def _title(col: str) -> str:
    """Documents often shout (POOJA KUMAR MEHTA): show those in normal case, leave mixed-case values as written."""
    return f"CASE WHEN {col} = upper({col}) THEN {_initcap(col)} ELSE {col} END"


GOLDEN_FIELDS: dict[str, str] = {
    "full_name": _initcap("s.name_clean"), "father_name": _initcap("s.father_name_clean"),
    # a 1-Jan default or a bare year is not a date of birth
    "dob": "CASE WHEN s.dob_placeholder OR s.dob_precision IS DISTINCT FROM 'full' THEN NULL ELSE s.dob END",
    "gender": "s.gender", "marital_status": "k.marital_status",
    "mobile": "s.mobile", "mobile_alt": "s.mobile_alt", "email": "s.email",
    "address": _title("k.address"), "city": _initcap("s.city"), "state": _initcap("s.state"), "pincode": "s.address_pin",
    "pan": "s.pan", "aadhaar_token": "s.aadhaar_token", "aadhaar_last4": "s.aadhaar_last4",
    "passport_no": "s.passport_no", "dl_no": "s.dl_no", "voter_id": "s.voter_id",
    "occupation": "k.occupation", "annual_income": "k.annual_income",
}
_IDENT = re.compile(r"[A-Za-z0-9_]+")


class SurvivorshipConfig(BaseModel):
    source_trust: list[str] = []                 # most trusted source first; unlisted sources rank last
    source_dates: dict[str, str] = {}            # source_id -> name of the attribute holding that record's date (dd/mm/yyyy or yyyy-mm-dd)
    default_strategy: Strategy = "most_recent"
    fields: dict[str, Strategy] = {}             # field (or group) -> strategy; others use default_strategy
    groups: dict[str, list[str]] = {}            # fields that must come from ONE record; the FIRST member is the key field (a record without it
                                                 # is skipped unless no record of the person has it), e.g. address + city + state + pincode

    @model_validator(mode="after")
    def _check(self):
        for s in [*self.source_trust, *self.source_dates, *self.source_dates.values()]:
            if not _IDENT.fullmatch(s):
                raise ValueError(f"bad source or attribute name {s!r}")
        seen: set = set()
        for g, members in self.groups.items():
            if g in GOLDEN_FIELDS or not _IDENT.fullmatch(g):
                raise ValueError(f"group name {g!r} is invalid or clashes with a field")
            for m in members:
                if m not in GOLDEN_FIELDS:
                    raise ValueError(f"unknown field {m!r} in group {g!r}; valid: {sorted(GOLDEN_FIELDS)}")
                if m in seen:
                    raise ValueError(f"field {m!r} is in more than one group")
                seen.add(m)
            if not members:
                raise ValueError(f"group {g!r} is empty")
        valid = set(GOLDEN_FIELDS) | set(self.groups)
        for f in self.fields:
            if f not in valid:
                raise ValueError(f"unknown field or group {f!r} in 'fields'; valid: {sorted(valid)}")
            if f in seen:
                raise ValueError(f"{f!r} belongs to a group: set the strategy on the group")
        return self

    def units(self) -> list[tuple[str, list[str], str]]:
        """[(unit name, member fields, strategy)]: a group is one unit, every other field is its own unit."""
        grouped = {m for ms in self.groups.values() for m in ms}
        out = [(g, ms, self.fields.get(g, self.default_strategy)) for g, ms in self.groups.items()]
        out += [(f, [f], self.fields.get(f, self.default_strategy)) for f in GOLDEN_FIELDS if f not in grouped]
        return out

    @classmethod
    def load(cls, path: str | Path) -> "SurvivorshipConfig":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
