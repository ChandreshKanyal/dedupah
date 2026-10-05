import re
from datetime import date

_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _ok(y, m, d, today):
    try:
        v = date(y, m, d)
    except ValueError:
        return None
    return v if 1900 <= y <= today.year and v <= today else None


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
