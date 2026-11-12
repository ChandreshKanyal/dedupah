"""One field a project defines itself: how its values are cleaned, compared and used to find candidate pairs.

A source column is mapped onto the field by name (mapping `custom`, or an extra column of the same name). Each kind has one cleaner (Python,
at standardisation) and one ladder of comparison levels (SQL, at scoring), the same shape the built-in comparisons have, so learning, rules,
review and the golden record treat the field like any other. Nothing here accepts SQL from a user: names are checked against a strict
pattern and every number is validated before it reaches a query.

    kind       cleaned value                      levels (best first)
    text       lower case, no punctuation         identical / same words in any order / same words spelled a little differently /
                                                  similar / different
    company    text without legal suffixes        as text ("Acme Pvt Ltd" = "ACME PRIVATE LIMITED")
    name       a person's name (titles dropped)   as text
    code       letters and digits, upper case     identical / one character apart / different
    number     a number                           equal / within the tolerance / different
    date       ISO date                           same day / within the tolerance (or day and month swapped) / same year / different
    category   lower case                         same / different
    phone      10-digit mobile                    same / different
    email      alias-normalised address           same / different
"""
import math
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dedupe.blocking.rules import Rule
from dedupe.scoring.comparisons import BUILTIN_COLUMNS, Comparison, Level
from dedupe.standardise import identifiers as ids
from dedupe.standardise import oddities as odd
from dedupe.standardise.dates import parse_record_date
from dedupe.standardise.names import parse_name, phonetic

Kind = Literal["text", "company", "name", "code", "number", "date", "category", "phone", "email"]
KINDS = {
    "text": "Free text (a title, a description): compared word by word and by spelling",
    "company": "Organisation name: like text, ignoring Pvt, Ltd, Inc and similar endings",
    "name": "A person's name: titles dropped, compared by sound",
    "code": "A code or reference number (GSTIN, SKU, registration number): letters and digits only",
    "number": "A number (amount, quantity): equal or within a tolerance",
    "date": "A date: the same day or within some days",
    "category": "One of a few values (type, status, branch)",
    "phone": "A phone number",
    "email": "An email address",
}
NAME = r"[a-z][a-z0-9_]{0,39}"
PREFIX = "x_"                       # every column and comparison of a custom field starts with this: never collides with a built-in one
_TEXTUAL = ("text", "company", "name")
LEGAL_WORDS = frozenset("pvt private ltd limited llp llc inc incorporated co company corp corporation plc gmbh the and m s ms messrs".split())
_FAR_FUTURE = date(2200, 1, 1)
BLOCK_WORDS = 6                     # the two-word blocking rule reads this many words of a value (15 keys at most)
STRONG = {1: 13.2, 0: -4.0}         # an identifier field: the same evidence as a government document number
_SQL_TYPE = {"number": "DOUBLE"}
EMPTY = frozenset({"na", "n/a", "n.a.", "null", "none", "nil", "-", "--", "unknown", "not available", "not applicable", "#n/a"})


class CustomField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str = ""
    kind: Kind = "text"
    identifier: bool = False        # code only: the same value means the same entity (learning is seeded from it; it never needs a weight)
    ignore_words: list[str] = []    # text and company: words left out before comparing ("traders", "enterprises")
    tolerance: float = Field(0.0, ge=0)                       # number: how far apart still counts as close; date: days
    tolerance_is_percent: bool = False                        # number: the tolerance is a percentage of the larger value
    similar_at: float = Field(0.88, gt=0.5, lt=1)             # text, company, name: Jaro-Winkler similarity counted as "similar"
    # weigh an agreement by how common the value is ("Sharma" counts for less than a rare surname). Right for values that many different
    # records share; wrong for values that are nearly unique (a company's name, a code), where a value is "common" only because it has
    # duplicates, the very thing being looked for. None = yes for a person's name, no for the rest
    common_values: bool | None = None
    block: bool | None = None       # find candidate pairs through this field; None = yes for names, texts and codes, no for the rest

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not re.fullmatch(NAME, v):
            raise ValueError("a field name is lower-case letters, digits and underscores, starting with a letter (at most 40)")
        return v

    @field_validator("ignore_words")
    @classmethod
    def _words(cls, v):
        out = sorted({w.strip().lower() for w in v if w and w.strip()})
        if len(out) > 100 or any(not re.fullmatch(r"[a-z0-9]{1,30}", w) for w in out):
            raise ValueError("ignore_words: at most 100 single words of letters and digits")
        return out

    @model_validator(mode="after")
    def _fits(self):
        if self.identifier and self.kind != "code":
            raise ValueError(f"field {self.name!r}: only a code can be an identifier")
        if self.tolerance and self.kind not in ("number", "date"):
            raise ValueError(f"field {self.name!r}: a tolerance applies to numbers and dates")
        if self.tolerance_is_percent and (self.kind != "number" or self.tolerance > 100):
            raise ValueError(f"field {self.name!r}: a percentage tolerance applies to numbers and is at most 100")
        if not math.isfinite(self.tolerance):
            raise ValueError(f"field {self.name!r}: the tolerance must be a number")
        return self

    # ---- names ----------------------------------------------------------------------------------------------------------------
    @property
    def col(self) -> str:
        return PREFIX + self.name

    @property
    def label(self) -> str:
        return self.title.strip() or self.name.replace("_", " ")

    def columns(self) -> dict[str, str]:
        """Standardised columns of this field: name -> DuckDB type."""
        cols = {self.col: _SQL_TYPE.get(self.kind, "VARCHAR")}
        if self.kind in _TEXTUAL:
            cols[self.col + "_words"] = "VARCHAR"      # the words sorted: equal when the same words come in another order
            cols[self.col + "_phon"] = "VARCHAR"       # the words' sounds sorted: equal for spelling variants
        return cols

    # ---- cleaning (standardisation) ---------------------------------------------------------------------------------------------
    def clean(self, raw) -> dict:
        out = dict.fromkeys(self.columns())
        s = "" if raw is None else str(raw).strip()
        if not s or s.lower() in EMPTY:
            return out
        if self.kind in ("text", "company"):
            drop = set(self.ignore_words) | (LEGAL_WORDS if self.kind == "company" else set())
            words = [w for w in re.sub(r"[^a-z0-9]+", " ", _plain(s)).split()]
            kept = [w for w in words if w not in drop] or words            # a value made only of ignored words keeps them
            self._textual(out, kept)
        elif self.kind == "name":
            self._textual(out, parse_name(s).tokens)
        elif self.kind == "code":
            v = re.sub(r"[^A-Z0-9]", "", _plain(s).upper())
            out[self.col] = v if len(v) >= 2 and len(set(v)) > 1 else None   # 0000, XXXX: a filler, not a code
        elif self.kind == "number":
            out[self.col] = _number(s)
        elif self.kind == "date":
            d = parse_record_date(s, today=_FAR_FUTURE)                     # any date, also one still to come (an expiry date)
            out[self.col] = d.isoformat() if d else None
        elif self.kind == "category":
            out[self.col] = " ".join(_plain(s).split()) or None
        elif self.kind == "phone":
            found = odd.phones(s)[0]
            out[self.col] = found[0] if found else None
        elif self.kind == "email":
            out[self.col] = ids.email(odd.email(s)[0])[1]
        return out

    def _textual(self, out: dict, words: list[str]) -> None:
        if not words:
            return
        out[self.col] = " ".join(words)
        out[self.col + "_words"] = " ".join(sorted(set(words)))
        out[self.col + "_phon"] = " ".join(sorted({phonetic(w) if len(w) > 1 and not w.isdigit() else w for w in words} - {""}))

    # ---- comparing (scoring) ----------------------------------------------------------------------------------------------------
    def comparison(self) -> Comparison:
        c, l, r = self.col, "l_" + self.col, "r_" + self.col
        tf = f"SELECT {c} AS key, count(*)::DOUBLE / (SELECT count({c}) FROM records) AS tf FROM records WHERE {c} IS NOT NULL GROUP BY 1"
        missing = f"{l} IS NULL OR {r} IS NULL"
        primary, agree, else_label = False, 1, "different"
        weigh = self.common_values if self.common_values is not None else self.kind == "name"
        if self.kind in _TEXTUAL:
            # "Pioneer Summit Foods" and "Pioneer Summit Motors" share most of their letters and are different companies: a whole-string
            # similarity alone calls them close. So the upper levels go word by word; the whole-string score only makes the weakest one.
            levels = [Level(4, "identical", f"{l} = {r}", l),
                      Level(3, "same words in any order", f"{l}_words = {r}_words", l),
                      Level(2, "same words, spelled a little differently", f"{l}_phon = {r}_phon OR ({_each_word(l, r)} AND {_each_word(r, l)})"),
                      Level(1, "similar", f"jaro_winkler_similarity({l}, {r}) >= {self.similar_at!r}")]
            primary, agree = True, 2
        elif self.kind == "code" and self.identifier:
            levels, tf = [Level(1, "identical", f"{l} = {r}")], None
        elif self.kind == "code":
            levels = [Level(2, "identical", f"{l} = {r}", l),
                      Level(1, "one character apart", f"length({l}) >= 6 AND levenshtein({l}, {r}) = 1")]
            agree = 2
        elif self.kind == "number":
            levels, tf = [Level(2, "equal", f"{l} = {r}")], None
            if self.tolerance:
                gap = (f"abs({l} - {r}) <= greatest(abs({l}), abs({r})) * {self.tolerance / 100!r}" if self.tolerance_is_percent
                       else f"abs({l} - {r}) <= {self.tolerance!r}")
                levels.append(Level(1, "close", gap))
            agree = 2
        elif self.kind == "date":
            dl, dr = f"CAST({l} AS DATE)", f"CAST({r} AS DATE)"
            swapped = f"(year({dl}) = year({dr}) AND month({dl}) = day({dr}) AND day({dl}) = month({dr}))"
            near = f"abs(date_diff('day', {dl}, {dr})) <= {int(self.tolerance)} OR {swapped}" if self.tolerance else swapped
            levels = [Level(3, "same day", f"{l} = {r}", l),
                      Level(2, f"within {int(self.tolerance)} days or day and month swapped" if self.tolerance else "day and month swapped", near),
                      Level(1, "same year", f"substr({l}, 1, 4) = substr({r}, 1, 4)")]
            agree = 2
        else:                       # category, phone, email
            levels = [Level(1, "same", f"{l} = {r}", None if self.kind == "category" else l)]
            if self.kind == "category":
                tf, agree = None, None          # a shared category is weak evidence: never part of the starting guess
        if not weigh or tf is None:
            tf, levels = None, [Level(lv.value, lv.label, lv.sql) for lv in levels]
        return Comparison(self.col, missing, tuple(levels), tf_table_sql=tf, else_label=else_label, title=self.label,
                          agree_from=agree, primary=primary)

    # ---- finding candidate pairs (blocking) ---------------------------------------------------------------------------------------
    def default_blocking(self) -> list[Rule]:
        """The rules a field gets without the project writing any: enough for its duplicates to be found at all."""
        on = self.block if self.block is not None else self.kind in (*_TEXTUAL, "code", "phone", "email")
        if not on:
            return []
        c, what = self.col, self.label
        if self.kind in _TEXTUAL:
            # any two words: survives a misspelt or missing word and any word order (a one-word value has only the first rule)
            two = (f"SELECT rid, w[i] || '|' || w[j] AS key FROM (SELECT rid, string_split({c}_phon, ' ')[1:{BLOCK_WORDS}] AS w FROM records "
                   f"WHERE {c}_phon IS NOT NULL), range(1, len(w)) t(i), range(i + 1, len(w) + 1) u(j)")
            return [Rule(f"{c}_sounds", _key(f"{c}_phon"), f"{what}: the same sounds in any order"),
                    Rule(f"{c}_two_words", two, f"{what}: any two words that sound the same")]
        if self.kind == "date":
            return [Rule(f"{c}_same", _key(c), f"{what}: the same day")]
        if self.kind == "number":
            return [Rule(f"{c}_same", _key(f"CAST({c} AS VARCHAR)", c), f"{what}: the same number")]
        return [Rule(f"{c}_same", _key(c), f"{what}: the same value", one_person_key=self.identifier)]


def _each_word(a: str, b: str) -> str:
    """SQL: every word of `a` has a counterpart in `b`, equal or (for words of five letters or more) one typing slip apart."""
    return (f"list_bool_and(list_transform(string_split({a}_words, ' '), x -> list_bool_or(list_transform(string_split({b}_words, ' '), "
            "y -> x = y OR (length(x) >= 5 AND damerau_levenshtein(x, y) <= 1)))))")


def _key(expr: str, *required: str) -> str:
    cond = " AND ".join(f"{c} IS NOT NULL" for c in (required or (expr,)))
    return f"SELECT rid, {expr} AS key FROM records WHERE {cond}"


def _plain(s: str) -> str:
    """Lower case without accents: 'Société' -> 'societe'."""
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch))


def _number(s: str) -> float | None:
    """'1,25,000.50', 'Rs. 1250', '12 %', '(300)' (an accountant's minus) -> a number; anything else -> None."""
    neg = s.startswith("(") and s.endswith(")")
    t = re.sub(r"(?i)^(rs\.?|inr|usd|eur|gbp|[₹$€£])\s*|\s*(%|/-)$", "", s.strip("() ").strip())
    t = t.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d{1,3})?", t):
        return None
    v = float(t)
    return (-v if neg else v) if math.isfinite(v) else None


# ---- blocking rules a project writes itself -----------------------------------------------------------------------------------
How = Literal["exact", "prefix", "first_word", "sounds", "words", "year"]
HOW = {"exact": "the whole value", "prefix": "the first characters", "first_word": "the first word", "sounds": "how it sounds (any word order)",
       "words": "the same words in any order", "year": "the year of a date"}
# built-in columns a rule may use (already cleaned; their own sound columns are separate entries)
BUILTIN_BLOCKABLE = tuple(c for c in BUILTIN_COLUMNS if c not in ("name_first_is_initial", "dob_placeholder", "address_norm", "aadhaar_last4")) + ("state",)


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str                      # a custom field's name, or a built-in column (BUILTIN_BLOCKABLE)
    how: How = "exact"
    chars: int = Field(3, ge=1, le=20)      # prefix: how many characters


class BlockingRule(BaseModel):
    """Two records become a candidate pair when every part gives the same key for both ("company sounds the same AND the same PIN")."""
    model_config = ConfigDict(extra="forbid")
    name: str
    parts: list[Part] = Field(min_length=1, max_length=4)
    title: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not re.fullmatch(NAME, v):
            raise ValueError("a rule name is lower-case letters, digits and underscores, starting with a letter (at most 40)")
        return v

    def compile(self, fields: dict[str, CustomField]) -> Rule:
        exprs, needed, said = [], [], []
        for p in self.parts:
            f = fields.get(p.field)
            if f is None and p.field not in BUILTIN_BLOCKABLE:
                raise ValueError(f"blocking rule {self.name!r}: unknown field {p.field!r}; valid: {sorted(fields) + list(BUILTIN_BLOCKABLE)}")
            col = f.col if f else p.field
            kind = f.kind if f else ("date" if p.field == "dob" else "text")
            numeric = (f is not None and f.kind == "number") or (f is None and p.field in ("dob_year", "dob_month", "dob_day", "birth_year_min", "birth_year_max"))
            if p.how in ("sounds", "words") and (f is None or f.kind not in _TEXTUAL):
                raise ValueError(f"blocking rule {self.name!r}: '{p.how}' applies to a text, company or name field of the project, not {p.field!r}")
            if p.how == "year" and kind != "date":
                raise ValueError(f"blocking rule {self.name!r}: 'year' applies to a date, not {p.field!r}")
            if p.how in ("prefix", "first_word") and (numeric or kind == "date"):
                raise ValueError(f"blocking rule {self.name!r}: '{p.how}' applies to text, not {p.field!r}")
            text = f"CAST({col} AS VARCHAR)" if numeric else col
            expr = {"exact": text, "prefix": f"substr({text}, 1, {p.chars})", "first_word": f"split_part({text}, ' ', 1)",
                    "sounds": f"{col}_phon", "words": f"{col}_words", "year": f"substr({text}, 1, 4)"}[p.how]
            exprs.append(expr)
            needed.append(f"{col}_phon" if p.how == "sounds" else f"{col}_words" if p.how == "words" else col)
            said.append(f"{f.label if f else p.field}: {HOW[p.how]}" + (f" ({p.chars})" if p.how == "prefix" else ""))
        one = len(self.parts) == 1 and self.parts[0].how == "exact" and (f := fields.get(self.parts[0].field)) is not None and f.identifier
        return Rule(PREFIX + self.name, _key(" || '|' || ".join(exprs), *needed), self.title.strip() or " + ".join(said), one_person_key=one)
