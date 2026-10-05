"""A small, safe condition language for business rules, compiled to SQL. No raw SQL is ever accepted.

Nodes:
    {"all": [node, ...]}   {"any": [node, ...]}   {"not": node}
    {"comparison": "pan", "is": "same PAN"}              level label equals
    {"comparison": "name", "is_not": "...", "in": [...]}  (also "not_in"); labels are those printed in the model file
    {"comparison": "phone", "missing": true}             value missing on either side ("not comparable")
    {"field": "dob_year", "op": "abs_diff_gt", "value": 10}  numeric difference (both sides present)
    {"field": "gender", "op": "differs" | "same"}        both sides present and different / equal
    {"field": "dob_year", "op": "both_gte" | "both_lte", "value": 2008}  both sides at least / at most the value
    {"score": "probability", "op": ">=", "value": 0.97}   (also match_weight; ops: > >= < <= =)
Fields are the standardised record columns (l_<field> vs r_<field>); comparisons are the scoring comparisons.
"""
from dedupe.scoring.comparisons import COMPARISONS, RECORD_COLUMNS

_BY_NAME = {c.name: c for c in COMPARISONS}
_SCORES = {"probability", "match_weight"}
_CMP_OPS = {">", ">=", "<", "<=", "="}


class ConditionError(ValueError):
    pass


def _label_value(comp: str, label: str) -> int:
    if comp not in _BY_NAME:
        raise ConditionError(f"unknown comparison {comp!r}; valid: {sorted(_BY_NAME)}")
    labels = {v: k for k, v in _BY_NAME[comp].labels().items()}
    if label not in labels:
        raise ConditionError(f"comparison {comp!r} has no level {label!r}; valid: {sorted(labels)}")
    return labels[label]


def _num(v) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConditionError(f"expected a number, got {v!r}")
    return repr(float(v)) if isinstance(v, float) else str(int(v))


def compile_condition(node: dict) -> str:
    if not isinstance(node, dict) or not node:
        raise ConditionError(f"bad condition: {node!r}")
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        parts = node[key]
        if not isinstance(parts, list) or not parts:
            raise ConditionError(f"'{key}' needs a non-empty list")
        return "(" + (" AND " if key == "all" else " OR ").join(compile_condition(p) for p in parts) + ")"
    if "not" in node:
        return f"(NOT {compile_condition(node['not'])})"
    if "comparison" in node:
        comp = node["comparison"]
        if comp not in _BY_NAME:
            raise ConditionError(f"unknown comparison {comp!r}; valid: {sorted(_BY_NAME)}")
        col = f"lv_{comp}"
        if node.get("missing") is True:
            return f"({col} = -1)"
        if "is" in node:
            return f"({col} = {_label_value(comp, node['is'])})"
        if "is_not" in node:
            return f"({col} >= 0 AND {col} <> {_label_value(comp, node['is_not'])})"
        for key, neg in (("in", ""), ("not_in", "NOT ")):
            if key in node:
                vals = ", ".join(str(_label_value(comp, x)) for x in node[key])
                return f"({col} >= 0 AND {neg}{col} IN ({vals}))" if neg else f"({col} IN ({vals}))"
        raise ConditionError(f"comparison condition needs one of is/is_not/in/not_in/missing: {node!r}")
    if "field" in node:
        f, op = node["field"], node.get("op")
        if f not in RECORD_COLUMNS:
            raise ConditionError(f"unknown field {f!r}; valid: {RECORD_COLUMNS}")
        both = f"l_{f} IS NOT NULL AND r_{f} IS NOT NULL"
        if op == "differs":
            return f"({both} AND l_{f} <> r_{f})"
        if op == "same":
            return f"({both} AND l_{f} = r_{f})"
        if op == "abs_diff_gt":
            return f"({both} AND abs(l_{f} - r_{f}) > {_num(node['value'])})"
        if op == "both_gte":
            return f"({both} AND l_{f} >= {_num(node['value'])} AND r_{f} >= {_num(node['value'])})"
        if op == "both_lte":
            return f"({both} AND l_{f} <= {_num(node['value'])} AND r_{f} <= {_num(node['value'])})"
        raise ConditionError(f"unknown field op {op!r} (differs, same, abs_diff_gt, both_gte, both_lte)")
    if "score" in node:
        if node["score"] not in _SCORES:
            raise ConditionError(f"unknown score {node['score']!r}; valid: {sorted(_SCORES)}")
        if node.get("op") not in _CMP_OPS:
            raise ConditionError(f"score op must be one of {sorted(_CMP_OPS)}")
        return f"({node['score']} {node['op']} {_num(node['value'])})"
    raise ConditionError(f"unrecognised condition: {node!r}")
