import os
import re
from datetime import date, datetime

_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MAX_AGE = 110
# An age without a record date may have been stated years ago (old leads are rarely re-dated): allow this many extra years on the
# early side of the birth-year range so a stale age is not scored as a contradiction.
UNDATED_AGE_SLACK = 3


def reference_date() -> date:
    """'Today' for date checks and for turning ages into birth years. `DEDUPE_REFERENCE_DATE` (YYYY-MM-DD) pins it for
    reproducible re-runs; otherwise the real current date, so nothing drifts as years pass."""
    v = os.environ.get("DEDUPE_REFERENCE_DATE", "").strip()
    return date.fromisoformat(v) if v else date.today()


def _ok(y, m, d, today):
    try:
        v = date(y, m, d)
    except ValueError:
        return None
    return v if 1900 <= y <= today.year and v <= today else None


def parse_age(raw) -> tuple[int, int] | None:
    """'35', '35.0', '35 yrs' -> (35, 35); '50-59', '50 to 59' -> (50, 59); '60+' -> (60, 85); '90+' -> (90, 110) (open bands span
    25 years, capped at MAX_AGE); anything else (or an absurd age) -> None."""
    s = str(raw if raw is not None else "").strip().lower().replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r"\s*(years?|yrs?\.?|y)\s*(old)?$", "", s)
    s = re.sub(r"^age\s*:?\s*", "", s).replace(" to ", "-").replace(" ", "")
    num = r"(\d{1,3})(?:\.\d+)?"
    if m := re.fullmatch(num, s):
        lo = hi = int(m[1])
    elif m := re.fullmatch(f"{num}-{num}", s):
        lo, hi = int(m[1]), int(m[2])
    elif m := re.fullmatch(f"{num}\\+", s):
        lo, hi = int(m[1]), min(int(m[1]) + 25, MAX_AGE)
    else:
        return None
    return (lo, hi) if 0 < lo <= hi <= MAX_AGE else None


def parse_record_date(raw, today: date | None = None) -> date | None:
    """The date a record was created/updated. Accepts what source systems emit: plain dates in any format parse_dob takes, plus
    timestamps ('2023-04-01 00:00:00', '2023-04-01T10:22:00Z', '01/04/2023 10:22')."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = re.sub(r"[T\s]+\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(z|[ap]m|[+-]\d{2}:?\d{2})?$", "", str(raw).strip(), flags=re.I)
    iso, year = parse_dob(s, today)[:2]
    return date.fromisoformat(iso) if iso else date(year, 7, 1) if year else None


def birth_year_range(dob_year, age_raw, record_date_raw, today: date | None = None):
    """(min, max) possible birth year. An exact birth year wins; otherwise the age (band) is counted back from the year of the record's
    date. One year of slack because the birthday may not have passed yet. Without a usable record date the age is counted back from
    today's year with UNDATED_AGE_SLACK extra years, because the age may be stale."""
    if dob_year is not None:
        return dob_year, dob_year
    ages = parse_age(age_raw)
    if ages is None:
        return None, None
    today = today or reference_date()
    rec = parse_record_date(record_date_raw, today)
    ref, slack = (rec.year, 0) if rec else (today.year, UNDATED_AGE_SLACK)
    return ref - ages[1] - 1 - slack, ref - ages[0]


def parse_dob(raw: str | None, today: date | None = None):
    """Returns (iso, year, month, day, precision, placeholder_suspect). Day-first (Indian) unless month-first is forced."""
    today = today or reference_date()
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
