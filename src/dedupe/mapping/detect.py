"""Recognise what a column holds from its VALUES, not its name (Phase A step 1).

Each value is tested with the same checks the cleaning uses (PAN structure, Aadhaar checksum, Indian mobile rules, email shape, dates
that parse), plus checks for values we do not match on but should recognise (GSTIN with its checksum, IFSC, vehicle numbers, tokens).
A column gets the type that most of its filled values pass. Only counts and shares leave this module, never values.

Types that map to a canonical field carry `field`; the rest (GSTIN, IFSC, vehicle number, amount, row id, token, free text) are
recognised so the screen can say what they are and the engine can keep them as extra columns or refuse them as person keys.
"""
import re
import statistics
from collections import Counter

import pyarrow as pa

from ..standardise import identifiers as ids
from ..standardise import oddities as odd
from ..standardise.dates import parse_age, parse_record_date, reference_date
from ..standardise.geo import CITY_ALIASES, CITY_STATE, STATE_ALIASES, STATES
from ..standardise.oddities import ORGANISATION_WORDS, PLACEHOLDER_NAMES
from ..standardise.verhoeff import verhoeff_valid

MIN_VALUES = 5          # fewer filled values than this: no verdict
MIN_SHARE = 0.8         # share of filled values that must pass a strong check
MIN_SHARE_SOFT = 0.7    # for the softer checks (names, addresses, places)

_ALL_STATES = STATES | {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand",
    "karnataka", "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland", "odisha", "punjab", "rajasthan",
    "sikkim", "tamil nadu", "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal", "andaman and nicobar islands",
    "chandigarh", "dadra and nagar haveli and daman and diu", "delhi", "jammu and kashmir", "ladakh", "lakshadweep", "puducherry",
    "pondicherry", "nct of delhi", "j&k"}
_GENDER = {"m", "f", "male", "female", "o", "other", "others", "t", "tg", "transgender", "u", "unknown", "man", "woman"}
_MARITAL = {"single", "married", "unmarried", "divorced", "widowed", "widow", "widower", "separated", "s", "d", "w"}
_ADDRESS_WORDS = {"road", "rd", "street", "st", "nagar", "colony", "sector", "flat", "floor", "house", "no", "near", "opp", "lane", "marg",
                  "society", "soc", "apartment", "apartments", "apt", "block", "plot", "village", "post", "dist", "taluka", "tehsil", "chowk",
                  "phase", "layout", "cross", "main", "building", "bldg", "tower", "wing", "behind", "gali", "mohalla", "ward", "po", "ps"}

_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_VEHICLE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$")
_AADHAAR_MASKED = re.compile(r"^[X*x]{4}[\s-]?[X*x]{4}[\s-]?\d{4}$")
_TOKEN = re.compile(r"^[0-9a-f]{32}$|^[0-9a-f]{40}$|^[0-9a-f]{64}$|^[A-Za-z0-9+/]{43}=$")
_PINCODE = re.compile(r"^[1-9]\d{5}$")
_NUMBER = re.compile(r"^-?\d{1,3}(,\d{2,3})*(\.\d+)?$|^-?\d+(\.\d+)?$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z.' -]*$")
_HAS_TIME = re.compile(r"\d{1,2}:\d{2}")

# type -> (canonical field or None, short plain description shown on screen)
TYPES: dict[str, tuple[str | None, str]] = {
    "aadhaar": ("aadhaar", "Aadhaar numbers (checksum valid)"),
    "aadhaar_masked": ("aadhaar_last4", "Masked Aadhaar (only the last four digits)"),
    "pan": ("pan", "PAN numbers"),
    "gstin": (None, "GST numbers (a business, not a person)"),
    "ifsc": (None, "Bank branch codes (IFSC): not a person"),
    "passport": ("passport_no", "Passport numbers"),
    "voter_id": ("voter_id", "Voter ID (EPIC) numbers"),
    "driving_licence": ("dl_no", "Driving licence numbers"),
    "vehicle": (None, "Vehicle registration numbers"),
    "mobile": ("mobile", "Indian mobile numbers"),
    "email": ("email", "Email addresses"),
    "pincode": ("pincode", "PIN codes"),
    "dob": ("dob", "Dates of birth"),
    "record_date": ("record_date", "Recent dates (created or updated)"),
    "date": (None, "Dates"),
    "gender": ("gender", "Gender"),
    "marital_status": ("marital_status", "Marital status"),
    "age": ("age", "Ages or age bands"),
    "state": ("state", "Indian states"),
    "city": ("city", "Indian cities"),
    "person_name": ("full_name", "Names of people"),
    "organisation_name": (None, "Names of businesses or organisations"),
    "address": ("address", "Addresses"),
    "token": (None, "Tokens or hashes (already scrambled values)"),
    "row_id": (None, "A different value on every row (a row id)"),
    "amount": (None, "Numbers or amounts"),
    "mixed_ids": (None, "A mix of identity numbers (PAN, passport, voter ID ...) in one column"),
    "file_ref": (None, "Names or links of image or document files"),
    "four_digits": (None, "Four-digit numbers (for example the last four digits of Aadhaar)"),
    "category": (None, "A short list of repeated values (a category)"),
    "text": (None, "Free text"),
}


def _clean(values) -> list[str]:
    return [s for s in (str(v).strip() for v in values if v is not None) if s and s.lower() not in ("nan", "null", "none", "na", "n/a", "-")]


def _gstin_ok(s: str) -> bool:
    """GSTIN check character: weights 1,2 alternating over base-36 digits (the published GSTN algorithm)."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for i, c in enumerate(s[:14]):
        p = chars.index(c) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return chars[(36 - total % 36) % 36] == s[14]


def _upper(s: str) -> str:
    return re.sub(r"[\s-]", "", s).upper()


def _is_aadhaar(s):
    d = re.sub(r"[\s-]", "", s)
    return bool(re.fullmatch(r"[2-9]\d{11}", d)) and verhoeff_valid(d)


def _is_person_name(s):
    low = s.lower()
    if not _NAME.match(s) or low in PLACEHOLDER_NAMES or low in _ALL_STATES or low in CITY_STATE or low in _GENDER or low in _MARITAL:
        return False
    words = re.split(r"[\s.]+", low.strip(". "))
    return 1 <= len(words) <= 6 and not (set(words) & ORGANISATION_WORDS) and not (set(words) & _ADDRESS_WORDS)


def _is_org_name(s):
    return bool(set(re.sub(r"[^a-z0-9]+", " ", s.lower()).split()) & ORGANISATION_WORDS)


def _is_address(s):
    words = re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
    if len(s) < 8 or len(words) < 2 or "@" in s:
        return False
    house_first = bool(re.match(r"^(no\.?\s*)?\d+[a-z]?\s*[,/ -]\s*[a-z]", s.lower()))    # "52 Garden View", "12/B, MG Road"
    return house_first or ((any(c.isdigit() for c in s) or "," in s) and len(words) >= 3 and (
        bool(set(words) & _ADDRESS_WORDS) or "," in s or bool(re.search(r"\b[1-9]\d{5}\b", s))))


def _is_city(s):
    low = re.sub(r"\s+", " ", s.lower().strip())
    return low in CITY_STATE or low in CITY_ALIASES


def _is_state(s):
    low = re.sub(r"\s+", " ", s.lower().strip())
    return low in _ALL_STATES or low in STATE_ALIASES


# Checks in order of how specific they are: a value passing a specific check is not counted for a vague one at the same share.
_CHECKS = [
    ("aadhaar", _is_aadhaar),
    ("gstin", lambda s: bool(_GSTIN.match(_upper(s))) and _gstin_ok(_upper(s))),
    ("pan", lambda s: bool(re.fullmatch(r"[A-Za-z]{5}\d{4}[A-Za-z]", re.sub(r"[\s-]", "", s))) and ids.pan(s)[0] is not None),
    ("ifsc", lambda s: bool(_IFSC.match(_upper(s)))),
    ("email", lambda s: "@" in s and ids.email(odd.email(s)[0])[0] is not None),
    ("aadhaar_masked", lambda s: bool(_AADHAAR_MASKED.match(s))),
    ("token", lambda s: bool(_TOKEN.match(s))),
    ("mobile", lambda s: bool(re.fullmatch(r"[\d\s+()./,;|-]{10,40}|\d(\.\d+)?[eE]\+?\d{1,2}", s)) and bool(odd.phones(s)[0])),
    ("voter_id", lambda s: bool(re.fullmatch(r"[A-Za-z]{3}\d{7}", _upper(s)))),
    ("passport", lambda s: bool(re.fullmatch(r"[A-Za-z]\d{7}", _upper(s)))),
    ("driving_licence", lambda s: bool(re.fullmatch(r"[A-Za-z]{2}\d{11,14}", _upper(s)))),
    ("vehicle", lambda s: bool(_VEHICLE.match(_upper(s)))),
    ("pincode", lambda s: bool(_PINCODE.match(s))),
    ("gender", lambda s: s.lower() in _GENDER),
    ("marital_status", lambda s: s.lower() in _MARITAL),
    ("state", _is_state),
    ("city", _is_city),
]
_SOFT = {"state", "city", "person_name", "organisation_name", "address"}
_ID_TYPES = {"aadhaar", "aadhaar_masked", "pan", "voter_id", "passport", "driving_licence", "gstin"}
_FILE = re.compile(r"\.(jpe?g|png|gif|bmp|tiff?|webp|heic|pdf)$", re.I)


def _dates(vals: list[str]) -> tuple[str, float] | None:
    today = reference_date()
    parsed = [(v, parse_record_date(v, today)) for v in vals if re.search(r"\d", v) and not re.fullmatch(r"\d+(\.\d+)?", v)]
    hits = [(v, d) for v, d in parsed if d is not None]
    share = len(hits) / len(vals)
    if share < MIN_SHARE:
        return None
    ages = [(today - d).days / 365.25 for _, d in hits]
    timed = sum(bool(_HAS_TIME.search(v)) for v, _ in hits) / len(hits)
    old = sum(a >= 15 for a in ages) / len(ages)
    recent = sum(a < 15 for a in ages) / len(ages)
    if timed < 0.2 and old >= 0.9:
        return "dob", share
    if timed >= 0.5 or recent >= 0.9:
        return "record_date", share
    return "date", share


def _numeric(vals: list[str], distinct: float) -> tuple[str, float] | None:
    ages = [parse_age(v) for v in vals]
    age_share = sum(a is not None for a in ages) / len(vals)
    fractions = sum(bool(re.search(r"\.\d*[1-9]", v)) for v in vals) / len(vals)
    if age_share >= 0.9 and fractions <= 0.1 and 10 <= statistics.median((a[0] + a[1]) / 2 for a in ages if a) <= 90:
        return "age", age_share                           # whole numbers 1-110 or bands ("50-59", "60+"); rates like 8.75 are not ages
    nums = [v for v in vals if _NUMBER.match(v)]
    if len(nums) / len(vals) < MIN_SHARE:
        return None
    if sum(bool(re.fullmatch(r"\d{4}", v)) for v in nums) / len(vals) >= MIN_SHARE:
        return "four_digits", len(nums) / len(vals)
    if distinct >= 0.99 and all("." not in v for v in nums):
        return "row_id", 1.0
    return "amount", len(nums) / len(vals)


def detect_values(values) -> dict:
    """The detected type of one column's values: {type, field, share, filled, distinct, description, alternatives}. `type` is None when
    no check is convincing. `filled` is the share of rows with a value; `distinct` the share of filled values that differ."""
    raw = list(values)
    vals = _clean(raw)
    out = {"type": None, "field": None, "share": 0.0, "filled": round(len(vals) / len(raw), 3) if raw else 0.0,
           "distinct": round(len(set(vals)) / len(vals), 3) if vals else 0.0, "description": None, "alternatives": []}
    if len(vals) < MIN_VALUES:
        return out
    scores: Counter = Counter()
    for name, check in _CHECKS:
        scores[name] = sum(1 for v in vals if check(v)) / len(vals)
    if sum(v.endswith("000") for v in vals) / len(vals) >= 0.5:
        scores["pincode"] = 0.0                          # 500000, 250000: round amounts, not PIN codes
    if (d := _dates(vals)) is not None:
        scores[d[0]] = d[1]
    if (n := _numeric(vals, out["distinct"])) is not None and not any(scores[t] >= MIN_SHARE for t in ("aadhaar", "mobile", "pincode")):
        scores[n[0]] = n[1]
    text_like = [v for v in vals if not re.search(r"\d", v)]
    scores["person_name"] = sum(_is_person_name(v) for v in vals) / len(vals)
    scores["organisation_name"] = sum(_is_org_name(v) for v in vals) / len(vals)
    scores["address"] = sum(_is_address(v) for v in vals) / len(vals)
    best = None
    for name, share in scores.most_common():
        need = MIN_SHARE_SOFT if name in _SOFT else MIN_SHARE
        if share >= need:
            best = (name, share)
            break
    # Names that are mostly people with a few businesses stay "person_name"; a column of mostly businesses is an organisation column.
    if best and best[0] in ("person_name", "organisation_name") and scores["organisation_name"] >= 0.5:
        best = ("organisation_name", scores["organisation_name"])
    if best and best[0] == "person_name" and (scores["city"] + scores["state"] >= 0.5):
        best = None
    if best and best[0] == "person_name" and out["distinct"] < 0.5:
        best = ("category", 1.0)                          # a few words repeated over and over: a list of options, not people's names
    files = sum(bool(_FILE.search(v)) for v in vals) / len(vals)
    if files >= MIN_SHARE:
        best = ("file_ref", files)
    if best is None:
        some_id = sum(any(check(v) for name, check in _CHECKS if name in _ID_TYPES) for v in vals) / len(vals)
        if some_id >= MIN_SHARE:
            best = ("mixed_ids", some_id)
    if best is None and out["distinct"] < 0.2:
        best = ("category", 1.0)
    if best is None and len(text_like) / len(vals) >= MIN_SHARE and statistics.mean(len(v) for v in vals) >= 25:
        best = ("text", len(text_like) / len(vals))
    if best is None and out["distinct"] >= 0.99 and all(len(v) <= 40 for v in vals):
        best = ("row_id", 1.0)
    if best:
        out.update(type=best[0], field=TYPES[best[0]][0], share=round(best[1], 3), description=TYPES[best[0]][1])
        if best[0] != "category":
            out["alternatives"] = [n for n, s in scores.most_common(4) if n != best[0] and s >= 0.3]
    return out


def profile(sample: pa.Table) -> dict[str, dict]:
    """Detected type of every column of a sample, keyed by column name."""
    return {col: detect_values(sample[col].to_pylist()) for col in sample.column_names}
