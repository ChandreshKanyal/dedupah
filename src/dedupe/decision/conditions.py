"""A small, safe condition language for business rules, compiled to SQL. No raw SQL is ever accepted.

Nodes:
    {"all": [node, ...]}   {"any": [node, ...]}   {"not": node}
    {"comparison": "pan", "is": "same PAN"}              level label equals
    {"comparison": "name", "is_not": "...", "in": [...]}  (also "not_in"); labels are those printed in the model file
    {"comparison": "phone", "missing": true}             value missing on either side ("not comparable")
    {"field": "dob_year", "op": "abs_diff_gt", "value": 10}  numeric difference (both sides present; for dob_year an age-based birth-year
                                                                range counts, by the distance between the two ranges)
    {"field": "gender", "op": "differs" | "same"}        both sides present and different / equal
    {"field": "dob_year", "op": "both_gte" | "both_lte", "value": 2008}  both sides at least / at most the value
    {"field": "dob_year", "op": "both_born_within_last", "value": 18}  both born in the last N years (relative to today, so it
                                                                        does not go stale)
    {"score": "probability", "op": ">=", "value": 0.97}   (also match_weight; ops: > >= < <= =)
    {"own_fields": "agree"}                              one of the project's own fields (dedupe.custom) agrees; never true for a
                                                         project without such fields
  values of a column (the rule builder's checks; a pair has no left and right, so each says "both", "at least one" or "neither"):
    {"field": "x_segment", "op": "both_in" | "either_in" | "neither_in", "values": ["Corporate", "SME"]}   the cleaned values, any case
    {"field": "x_note", "op": "both_contain" | "either_contains", "value": "trust"}                       text containing a piece, any case
    {"field": "x_amount", "op": "within", "value": 500}  /  "within_pct" (percent of the larger)  /  "within_days" (dates)
    {"field": "x_gstin", "op": "either_empty" | "both_empty" | "both_filled"}
    {"field": "source_id", "op": "differs" | "same" | "both_in" | ...}  which data source each record came from (also state, record_date)
  two different columns:
    {"cross": ["x_billing_phone", "x_phone"], "op": "same" | "similar"}  one record's first column against the other record's second
                                                         (either way round); "same" also finds such pairs at the next run (cross_blocking)
Fields are the standardised record columns (l_<field> vs r_<field>) and the rule-only columns (comparisons.RULE_ONLY_COLUMNS); comparisons
are the scoring comparisons of the spec in force (dedupe.scoring.comparisons.spec): the built-in ones plus the project's own fields, named
x_<field>. Typed values (values, value) are kept in string literals with quotes doubled, at most 50 values of 100 printable characters.
"""

from dedupe.scoring.comparisons import comparison, rule_columns, spec
from dedupe.standardise.dates import reference_date

_SCORES = {"probability", "match_weight"}
_CMP_OPS = {">", ">=", "<", "<=", "="}


class ConditionError(ValueError):
    pass


class NotInUse(ConditionError):
    """The condition names one of a project's own fields (x_...) that the spec in force does not define: the rule cannot fire. Checked
    where the project's fields are known (a run); elsewhere the rule is accepted as written."""


def _comparison(comp):
    c = comparison(comp) if isinstance(comp, str) else None
    if c is None:
        valid = sorted(x.name for x in spec().comparisons)
        raise (NotInUse if isinstance(comp, str) and comp.startswith("x_") else ConditionError)(f"unknown comparison {comp!r}; valid: {valid}")
    return c


def _label_value(comp: str, label: str) -> int:
    labels = {v: k for k, v in _comparison(comp).labels().items()}
    if label not in labels:
        raise ConditionError(f"comparison {comp!r} has no level {label!r}; valid: {sorted(labels)}")
    return labels[label]


def _num(v) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConditionError(f"expected a number, got {v!r}")
    return repr(float(v)) if isinstance(v, float) else str(int(v))


_VALUE_OPS = {"both_in", "either_in", "neither_in", "both_contain", "either_contains", "within", "within_pct", "within_days", "either_empty",
              "both_empty", "both_filled"}
SIMILAR_AT = 0.9                    # cross "similar": Jaro-Winkler similarity of the two values


def _column(f) -> str:
    if not isinstance(f, str) or f not in rule_columns():
        raise (NotInUse if isinstance(f, str) and f.startswith("x_") else ConditionError)(f"unknown field {f!r}; valid: {list(rule_columns())}")
    return f


def _text(v) -> str:
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ConditionError("a value must be text or a number")
    t = str(v).strip()
    if not t or len(t) > 100 or not t.isprintable():
        raise ConditionError("a value is 1 to 100 printable characters")
    return t


def _lit(t: str) -> str:
    return "'" + t.replace("'", "''") + "'"


def _clean_values(f: str, values) -> list[str]:
    """The typed values as the column holds them: cleaned like the field's own values (a code in upper case without spaces, a phone as
    its ten digits), then lower case; a value that cleans to nothing is dropped."""
    if not isinstance(values, list) or not values or len(values) > 50:
        raise ConditionError("'values' is a list of 1 to 50 values")
    own = next((c for c in spec().custom if c.col == f), None)
    out = []
    for v in values:
        t = _text(v)
        if own is not None:
            cleaned = own.clean(t).get(own.col)
            t = str(cleaned) if cleaned is not None else ""
        if t:
            out.append(t.lower())
    if not out:
        raise ConditionError(f"none of the values typed is a value the field {f!r} can hold")
    return out


def _value_op(f: str, op: str, node: dict) -> str:
    l, r = f"l_{f}", f"r_{f}"
    both = f"{l} IS NOT NULL AND {r} IS NOT NULL"
    low = lambda x: f"lower(trim(CAST({x} AS VARCHAR)))"            # noqa: E731
    if op in ("both_in", "either_in", "neither_in"):
        vals = ", ".join(_lit(v) for v in _clean_values(f, node.get("values")))
        hit = lambda x: f"coalesce({low(x)} IN ({vals}), false)"     # noqa: E731
        return {"both_in": f"({hit(l)} AND {hit(r)})", "either_in": f"({hit(l)} OR {hit(r)})",
                "neither_in": f"(NOT {hit(l)} AND NOT {hit(r)})"}[op]
    if op in ("both_contain", "either_contains"):
        piece = _lit(_text(node.get("value")).lower())
        has = lambda x: f"coalesce(contains({low(x)}, {piece}), false)"   # noqa: E731
        return f"({has(l)} AND {has(r)})" if op == "both_contain" else f"({has(l)} OR {has(r)})"
    if op in ("within", "within_pct"):
        n = node.get("value")
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 <= n <= (100 if op == "within_pct" else 1e12):
            raise ConditionError(f"'{op}' needs a number" + (" from 0 to 100" if op == "within_pct" else " of 0 or more"))
        a, b = f"try_cast({l} AS DOUBLE)", f"try_cast({r} AS DOUBLE)"
        gap = f"greatest(abs({a}), abs({b})) * {_num(n)} / 100" if op == "within_pct" else _num(n)
        return f"({a} IS NOT NULL AND {b} IS NOT NULL AND abs({a} - {b}) <= {gap})"
    if op == "within_days":
        n = node.get("value")
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 100_000:
            raise ConditionError("'within_days' needs a whole number of days from 0 to 100000")
        a, b = f"try_cast({l} AS DATE)", f"try_cast({r} AS DATE)"
        return f"({a} IS NOT NULL AND {b} IS NOT NULL AND abs(date_diff('day', {a}, {b})) <= {n})"
    return {"either_empty": f"({l} IS NULL OR {r} IS NULL)", "both_empty": f"({l} IS NULL AND {r} IS NULL)", "both_filled": f"({both})"}[op]


def _cross(node: dict) -> str:
    pair = node.get("cross")
    if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
        raise ConditionError("'cross' names two different fields")
    a, b = (_column(x) for x in pair)
    op = node.get("op")
    s = lambda x: f"CAST({x} AS VARCHAR)"                           # noqa: E731
    if op == "same":
        way = lambda x, y: f"(l_{x} IS NOT NULL AND r_{y} IS NOT NULL AND {s('l_' + x)} = {s('r_' + y)})"   # noqa: E731
    elif op == "similar":
        way = lambda x, y: (f"(l_{x} IS NOT NULL AND r_{y} IS NOT NULL AND "                              # noqa: E731
                            f"jaro_winkler_similarity({s('l_' + x)}, {s('r_' + y)}) >= {SIMILAR_AT!r})")
    else:
        raise ConditionError("a cross condition's op is 'same' or 'similar'")
    return f"({way(a, b)} OR {way(b, a)})"


def cross_pairs(node) -> list[tuple[str, str]]:
    """The two-column "same" conditions a rule needs true (not under a 'not'): the pairs they describe must be compared at all, so a run adds
    a way of finding them (blocking)."""
    if not isinstance(node, dict):
        return []
    if "cross" in node and node.get("op") == "same" and isinstance(node["cross"], list) and len(node["cross"]) == 2:
        return [tuple(node["cross"])]
    return [x for key in ("all", "any") for p in node.get(key) or [] for x in cross_pairs(p)]


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
        _comparison(comp)
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
    if "cross" in node:
        return _cross(node)
    if "field" in node:
        f, op = _column(node["field"]), node.get("op")
        if op in _VALUE_OPS:
            return _value_op(f, op, node)
        both = f"l_{f} IS NOT NULL AND r_{f} IS NOT NULL"
        if op == "differs":
            return f"({both} AND l_{f} <> r_{f})"
        if op == "same":
            return f"({both} AND l_{f} = r_{f})"
        if op == "abs_diff_gt" and f == "dob_year":
            # a record with only an age has a birth-year RANGE: the gap is the distance between the two ranges (E1-13: such records were skipped)
            lo, hi = ("coalesce({0}_dob_year, {0}_birth_year_min)", "coalesce({0}_dob_year, {0}_birth_year_max)")
            gap = f"greatest({lo.format('l')} - {hi.format('r')}, {lo.format('r')} - {hi.format('l')})"
            return f"({lo.format('l')} IS NOT NULL AND {lo.format('r')} IS NOT NULL AND {gap} > {_num(node['value'])})"
        if op == "abs_diff_gt":
            return f"({both} AND abs(l_{f} - r_{f}) > {_num(node['value'])})"
        if op == "both_gte":
            return f"({both} AND l_{f} >= {_num(node['value'])} AND r_{f} >= {_num(node['value'])})"
        if op == "both_lte":
            return f"({both} AND l_{f} <= {_num(node['value'])} AND r_{f} <= {_num(node['value'])})"
        if op == "both_born_within_last":
            if f != "dob_year":
                raise ConditionError("both_born_within_last applies to dob_year only")
            since = reference_date().year - int(_num(node["value"]))
            return f"({both} AND l_{f} >= {since} AND r_{f} >= {since})"
        raise ConditionError(f"unknown field op {op!r} (differs, same, abs_diff_gt, both_gte, both_lte, both_born_within_last, "
                             f"{', '.join(sorted(_VALUE_OPS))})")
    if "own_fields" in node:
        if node["own_fields"] != "agree":
            raise ConditionError("own_fields takes 'agree'")
        hits = [f"lv_{c.name} >= {c.agree_from}" for c in spec().comparisons if c.agree_from is not None]
        return "(" + " OR ".join(hits) + ")" if hits else "(FALSE)"
    if "score" in node:
        if node["score"] not in _SCORES:
            raise ConditionError(f"unknown score {node['score']!r}; valid: {sorted(_SCORES)}")
        if node.get("op") not in _CMP_OPS:
            raise ConditionError(f"score op must be one of {sorted(_CMP_OPS)}")
        return f"({node['score']} {node['op']} {_num(node['value'])})"
    raise ConditionError(f"unrecognised condition: {node!r}")
