import re
from datetime import date

_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _ok(y, m, d, today):
    try:
        v = date(y, m, d)
    except ValueError:
        return None
    return v if 1900 <= y <= today.year and v <= today else None


def parse_age(raw: str | None):
    """'35' -> (35, 35); '50-59' -> (50, 59); '60+' -> (60, 85); anything else (or an absurd age) -> None."""
    s = (raw or "").strip().replace("\u2013", "-").replace(" ", "")
    if re.fullmatch(r"\d{1,3}", s):
        lo = hi = int(s)
    elif m := re.fullmatch(r"(\d{1,3})-(\d{1,3})", s):
        lo, hi = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"(\d{1,3})\+", s):
        lo, hi = int(m[1]), int(m[1]) + 25
    else:
        return None
    return (lo, hi) if 0 < lo <= hi <= 110 else None


def birth_year_range(dob_year, age_raw, record_date_raw, today: date = date(2026, 10, 1)):
    """(min, max) possible birth year. An exact birth year wins; otherwise the age (band) is counted back from the year of the record's
    date (or the reference year when the record has no usable date). One year of slack because the birthday may not have passed yet."""
    if dob_year is not None:
        return dob_year, dob_year
    ages = parse_age(age_raw)
    if ages is None:
        return None, None
    ref = parse_dob(record_date_raw, today)[1] or today.year
    return ref - ages[1] - 1, ref - ages[0]


def parse_dob(raw: str | None, today: date = date(2026, 10, 1)):
    """Returns (iso, year, month, day, precision, placeholder_suspect). Day-first (Indian) unless month-first is forced."""
    s = (raw or "").strip()
    none = (None, None, None, None, None, False)
    if not s:
        return none
    if re.fullmatch(r"(19|20)\d{2}", s):
        y = int(s)
        return (None, y, None, None, "year", False) if 1900 <= y <= today.year else none
    d = None
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        d = _ok(int(m[1]), int(m[2]), int(m[3]), today)
    elif m := re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        d = _ok(y, b, a, today) if b <= 12 else _ok(y, a, b, today)  # second part >12 means month-first
    elif m := re.fullmatch(r"(\d{1,2})[\s\-/]([A-Za-z]{3})[A-Za-z]*[\s\-/,]*(\d{4})", s):
        if m[2].lower() in _MON:
            d = _ok(int(m[3]), _MON[m[2].lower()], int(m[1]), today)
    if d is None:
        return none
    return d.isoformat(), d.year, d.month, d.day, "full", (d.month == 1 and d.day == 1)
