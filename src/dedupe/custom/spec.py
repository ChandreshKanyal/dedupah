"""The spec one run works under, built from the project's matching settings."""
import json
from pathlib import Path

from dedupe.scoring.comparisons import BUILTIN, BUILTIN_COLUMNS, STRONG_BITS, MatchSpec

from .fields import PREFIX, STRONG, BlockingRule, CustomField

ALL_LEVELS = "*"            # in fixed_weights: every level of the comparison ("phone": {"*": 0} = the phone never counts)
MAX_BITS = 30.0


def build_spec(custom_fields: list | None = None, custom_blocking: list | None = None, fixed_weights: dict | None = None,
               compare_within_source: bool = True) -> MatchSpec:
    """Raises ValueError (in plain words) for anything that does not fit together: a name used twice, a rule on an unknown field, a weight
    for a level that does not exist."""
    fields = [f if isinstance(f, CustomField) else CustomField.model_validate(f) for f in custom_fields or []]
    by_name = {f.name: f for f in fields}
    if len(by_name) != len(fields):
        raise ValueError("two fields of the project have the same name")
    comps = BUILTIN + tuple(f.comparison() for f in fields)
    columns = BUILTIN_COLUMNS + tuple(c for f in fields for c in f.columns())
    strong = {**STRONG_BITS, **{f.col: STRONG for f in fields if f.identifier}}
    rules = [r for f in fields for r in f.default_blocking()]
    written = [r if isinstance(r, BlockingRule) else BlockingRule.model_validate(r) for r in custom_blocking or []]
    if len({r.name for r in written}) != len(written):
        raise ValueError("two blocking rules of the project have the same name")
    rules += [r.compile(by_name) for r in written]
    if len({r.name for r in rules}) != len(rules):
        raise ValueError("a blocking rule of the project has the name of one a field already brings; choose another name")
    fixed = {}
    for name, levels in (fixed_weights or {}).items():
        comp = next((c for c in comps if c.name in (name, PREFIX + name)), None)
        if comp is None:
            raise ValueError(f"fixed_weights: unknown comparison {name!r}; valid: {[c.name for c in comps]}")
        by_label = {label: lv for lv, label in comp.labels().items()}
        out = {}
        for label, bits in levels.items():
            if isinstance(bits, bool) or not isinstance(bits, (int, float)) or not -MAX_BITS <= bits <= MAX_BITS:
                raise ValueError(f"fixed_weights: the weight of {name!r} / {label!r} must be a number between -{MAX_BITS:g} and {MAX_BITS:g}")
            if label == ALL_LEVELS:
                out = {**dict.fromkeys(by_label.values(), float(bits)), **out}
            elif label not in by_label:
                raise ValueError(f"fixed_weights: comparison {name!r} has no level {label!r}; valid: {sorted(by_label)}")
            else:
                out[by_label[label]] = float(bits)
        fixed[comp.name] = out
    return MatchSpec(comparisons=comps, record_columns=columns, strong_bits=strong, fixed_bits=fixed, custom=tuple(fields),
                     blocking=tuple(rules), within_source=compare_within_source)


def spec_of_run(work: Path) -> MatchSpec:
    """The spec a finished run was made with (its settings are kept in the run folder); the built-in one for runs older than custom fields
    or when the file cannot be read."""
    try:
        used = json.loads((Path(work) / "config_used" / "settings.json").read_text(encoding="utf-8"))
        return build_spec(used.get("custom_fields"), used.get("custom_blocking"), used.get("fixed_weights"), used.get("compare_within_source", True))
    except (OSError, ValueError):
        return MatchSpec()
