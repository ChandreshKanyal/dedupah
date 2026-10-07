"""Handles for data that arrives in awkward shapes, before the normal cleaning sees it.

Each function returns what to clean plus the names of what it noticed (flags). Flags are kept per record (names only, never values)
so the run page can show, per source, how much odd data there was and what was done with it:

  multi_phone / multi_email   two values in one cell ("98xxxxxxxx / 99xxxxxxxx"): the first becomes the main value, the next the alternate
  phone_digits_lost           a phone stored as a rounded number (9.87654E+09): the digits are gone, so it is not used
  placeholder_name / _email   "Unknown", "Test", "NA", test@..., none@...: not a value, never matched on
  joint_name                  "Rahul & Anita Sharma": the first person is kept
  relation_in_name            "Rahul S/O Ramesh": the name is Rahul, Ramesh becomes the father's name when none is given
  organisation                "Sharma Traders Pvt Ltd": not a person; kept out of person matching (this product matches individuals)
  header_row                  a header line repeated inside the data: kept out of matching
"""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from . import identifiers as ids
from .dates import reference_date

_SPLIT_PHONES = re.compile(r"[,;/|&]|\s{2,}|\s+(?:or|and)\s+", re.I)
_SPLIT_EMAILS = re.compile(r"[,;\s/|]+")
_SCI = re.compile(r"\d(?:\.\d+)?[eE]\+?\d{1,2}")
PLACEHOLDER_NAMES = {"test", "testing", "unknown", "na", "n a", "not available", "nil", "null", "none", "customer", "dummy", "xxx", "xx", "x",
                     "abc", "sample", "no name", "noname", "name", "self", "demo", "tbd", "nan", "not known", "unk"}
PLACEHOLDER_EMAIL_LOCAL = {"test", "na", "none", "nil", "null", "noemail", "no-email", "no.email", "abc", "xyz", "dummy", "sample", "notavailable",
                           "na.na", "nomail", "noreply", "no-reply", "unknown", "xxx", "asdf"}
ORGANISATION_WORDS = {"pvt", "ltd", "limited", "llp", "inc", "incorporated", "corporation", "corp", "enterprises", "enterprise", "industries",
                      "traders", "trading", "agencies", "agency", "associates", "solutions", "technologies", "services", "foundation", "trust",
                      "society", "hospital", "clinic", "school", "college", "university", "bank", "motors", "logistics", "constructions", "builders",
                      "pharma", "pharmaceuticals", "textiles", "jewellers", "electricals", "electronics", "stores", "mart", "huf"}
HEADER_NAMES = {"name", "full name", "full_name", "customer name", "party name", "party_name", "applicant name", "name of customer", "customer_name"}
_RELATION = re.compile(r"\s+(?:s/o|d/o|w/o|c/o|s\.o\.|d\.o\.|w\.o\.|son of|daughter of|wife of|husband of|care of)\s+", re.I)
_JOINT = re.compile(r"\s*(?:&|\band\b|\+)\s*", re.I)
_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).split()


def phones(raw) -> tuple[list[str], set]:
    """All valid mobiles in a cell, in order, without repeats."""
    flags: set = set()
    s = str(raw or "").strip()
    if not s:
        return [], flags
    out = []
    parts = [p for p in _SPLIT_PHONES.split(s) if p and p.strip()]
    for p in parts:
        p = p.strip().lstrip("'")
        if _SCI.fullmatch(p):
            mantissa = p.lower().split("e")[0].replace(".", "")
            if len(mantissa) < 9:                       # Excel shows about 6 digits: the rest of the number is lost for good
                flags.add("phone_digits_lost")
                continue
            try:
                p = str(int(Decimal(p)))
            except (InvalidOperation, ValueError):
                continue
        p = re.sub(r"\.0+$", "", p)                     # 9876543210.0: a number column read as decimals
        v = ids.phone(p)
        if v and v not in out:
            out.append(v)
    if len(out) > 1:
        flags.add("multi_phone")
    return out, flags


def email(raw) -> tuple[str | None, set]:
    """The first real email in a cell (None for placeholders)."""
    flags: set = set()
    s = str(raw or "").strip()
    if not s:
        return None, flags
    s = re.sub(r"\s*([.@])\s*", r"\1", s)            # 'name@example. com': a stray space around a dot or the @
    found = [p for p in _SPLIT_EMAILS.split(s) if "@" in p]
    if len(found) > 1:
        flags.add("multi_email")
    for p in found:
        e = ids.email(p)[0]
        if not e:
            continue
        # judged by the part before the @ only: a test file may use example.com for every person, each with their own address
        if e.partition("@")[0] in PLACEHOLDER_EMAIL_LOCAL:
            flags.add("placeholder_email")
            continue
        return e, flags
    return (None if found else s), flags            # no '@' at all: let the normal check reject it


def name(raw) -> tuple[str | None, str | None, set]:
    """(name to parse, father's name found inside it, flags)."""
    flags: set = set()
    s = str(raw or "").strip()
    if not s:
        return None, None, flags
    words = _words(s)
    if " ".join(words) in HEADER_NAMES:
        return None, None, {"header_row"}
    if " ".join(words) in PLACEHOLDER_NAMES:
        return None, None, {"placeholder_name"}
    if ORGANISATION_WORDS & set(words) or re.search(r"&\s*co\b|\bco\.?\s*$|private\s+limited", s, re.I):
        return s, None, {"organisation"}
    father = None
    parts = _RELATION.split(s, maxsplit=1)
    if len(parts) == 2:
        s, father = parts[0].strip(), parts[1].strip() or None
        flags.add("relation_in_name")
    pieces = [p for p in _JOINT.split(s) if p.strip()]
    if len(pieces) > 1:
        flags.add("joint_name")
        first = pieces[0].split()
        last = pieces[-1].split()
        # "Rahul & Anita Sharma" -> "Rahul Sharma"; "Rahul Sharma and Anita Sharma" -> "Rahul Sharma"; "Mr & Mrs Sharma" -> "Sharma"
        s = " ".join(first if len(first) > 1 else first + last[-1:])
    return s, father, flags


def dob_text(raw, today: date | None = None) -> str | None:
    """Rewrites date forms the normal parser does not read into one it does: a time part, a two-digit year with a month name,
    'May 17, 1990', an Excel serial number (days since 1899-12-30)."""
    if raw is None:
        return None
    s = str(raw).strip()
    s = re.sub(r"[T\s]+\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(z|[ap]m|[+-]\d{2}:?\d{2})?$", "", s, flags=re.I)
    today = today or reference_date()
    if m := re.fullmatch(r"(\d{1,2})[\s\-/.]([A-Za-z]{3,9})[\s\-/.,]*(\d{2})", s):
        yy = int(m[3])
        return f"{m[1]} {m[2]} {(1900 if yy > today.year % 100 else 2000) + yy}"
    if m := re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s):
        return f"{m[2]} {m[1]} {m[3]}"
    if re.fullmatch(r"\d{5}(\.0+)?", s):
        d = date(1899, 12, 30) + timedelta(days=int(float(s)))
        return d.isoformat() if date(1900, 1, 1) <= d <= today else s
    return s


def header_row(raw_name) -> bool:
    return " ".join(_words(str(raw_name or ""))) in HEADER_NAMES
