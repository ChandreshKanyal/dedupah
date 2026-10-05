"""Realistic data corruption: the mess that makes the same customer look different across systems."""
import random
from datetime import date

from .reference import ADDRESS_ABBR, HONORIFIC, NAME_VARIANTS

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_TRANSLIT = [("v", "w"), ("w", "v"), ("th", "t"), ("ee", "i"), ("oo", "u"), ("sh", "s"), ("ph", "f"),
             ("aa", "a"), ("kh", "k"), ("ck", "k"), ("z", "j"), ("y", "i"), ("ai", "ay"), ("dh", "d")]
_OCR = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S", "B": "8", "8": "B", "Z": "2", "2": "Z", "G": "6"}


def miss(rng: random.Random, p: float) -> bool:
    return rng.random() < p


def typo(rng: random.Random, s: str) -> str:
    if len(s) < 4:
        return s
    i = rng.randrange(1, len(s) - 1)
    op = rng.choice(["swap", "drop", "dup", "sub"])
    if op == "swap":
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if op == "drop":
        return s[:i] + s[i + 1:]
    if op == "dup":
        return s[:i] + s[i] + s[i:]
    return s[:i] + rng.choice("aeiounrst") + s[i + 1:]


def translit(rng: random.Random, s: str) -> str:
    known = NAME_VARIANTS.get(s.lower())
    if known and rng.random() < 0.7:
        return rng.choice(known)
    low = s.lower()
    rules = [r for r in _TRANSLIT if r[0] in low]
    if not rules:
        return s
    a, b = rng.choice(rules)
    out = low.replace(a, b, 1)
    return out.capitalize()


def _noisy_token(rng: random.Random, tok: str, noise: float) -> str:
    if tok and rng.random() < 0.12 * noise:
        tok = translit(rng, tok)
    if tok and rng.random() < 0.06 * noise:
        tok = typo(rng, tok)
    return tok


DEFAULT_STYLES = {"full": 0.25, "fl": 0.35, "initials": 0.08, "lastfirst": 0.07, "fi_last": 0.05, "honorific": 0.1}


def render_name(rng, p, noise=1.0, styles=None, upper_p=0.15, maiden_p=0.0, first_only_p=0.0) -> str:
    """Render a person's name the way one particular system might store it."""
    styles = styles or DEFAULT_STYLES
    last = p.maiden_last if (p.gender == "F" and p.maiden_last and rng.random() < maiden_p) else p.last
    first, middle = p.first, p.middle
    if first_only_p and rng.random() < first_only_p:
        s = _noisy_token(rng, first, noise)
        return s.upper() if rng.random() < upper_p else s
    first, last = _noisy_token(rng, first, noise), _noisy_token(rng, last, noise)
    if middle:
        middle = _noisy_token(rng, middle, noise)
    style = rng.choices(list(styles), weights=list(styles.values()))[0]
    if style == "full":
        parts = [first, middle, last]
    elif style == "fl":
        parts = [first, last]
    elif style == "initials":
        parts = [first[0], middle[0] if middle else None, last]
        parts = [x + "." if x and rng.random() < 0.4 and len(x) == 1 else x for x in parts]
    elif style == "fi_last":
        parts = [first[0] + ".", last]
    elif style == "lastfirst":
        s = f"{last}, {first}" + (f" {middle}" if middle else "")
        return s.upper() if rng.random() < upper_p else s
    else:  # honorific
        married = p.marital_status == "Married"
        key = (p.gender, True) if p.gender == "M" else (p.gender, married)
        parts = [rng.choice(HONORIFIC[key]), first, last]
    s = " ".join(x for x in parts if x)
    if rng.random() < 0.02 * noise:
        s = s.replace(" ", "  ", 1)
    if rng.random() < upper_p:
        return s.upper()
    if rng.random() < 0.03:
        return s.lower()
    return s


def render_dob(rng, d: date, primary: str = "dmy_slash", mix_p: float = 0.12, noise: float = 1.0):
    r = rng.random()
    if r < 0.02 * noise:  # default / placeholder date
        d = date(d.year, 1, 1)
    elif r < 0.04 * noise and d.day <= 12:  # day/month swapped by a data-entry operator
        d = date(d.year, d.day, d.month)
    elif r < 0.05 * noise:  # year typo
        d = date(max(1900, d.year + rng.choice([-1, 1, -10, 10])), d.month, min(d.day, 28))
    fmt = primary if rng.random() > mix_p else rng.choice(["iso", "dmy_slash", "dmy_dash", "dmy_mon", "mdy_slash"])
    if rng.random() < 0.015 * noise:
        return str(d.year)
    return {
        "iso": d.isoformat(),
        "dmy_slash": f"{d.day:02d}/{d.month:02d}/{d.year}",
        "dmy_dash": f"{d.day:02d}-{d.month:02d}-{d.year}",
        "dmy_mon": f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}",
        "mdy_slash": f"{d.month:02d}/{d.day:02d}/{d.year}",
    }[fmt]


def render_date(rng, d: date, primary: str = "iso") -> str:
    return render_dob(rng, d, primary, mix_p=0.05, noise=0.0)


def render_phone(rng, m: str, noise: float = 1.0) -> str:
    fmts = [m, f"+91{m}", f"+91 {m[:5]} {m[5:]}", f"0{m}", f"{m[:5]}-{m[5:]}", f"91-{m}", f"+91-{m}", f"{m[:3]} {m[3:6]} {m[6:]}"]
    w = [0.45, 0.15, 0.12, 0.06, 0.05, 0.05, 0.07, 0.05]
    s = rng.choices(fmts, weights=w)[0]
    if rng.random() < 0.01 * noise:  # a dropped digit
        i = rng.randrange(len(m))
        s = s.replace(m, m[:i] + m[i + 1:])
    return s


def render_email(rng, e: str, noise: float = 1.0) -> str:
    if rng.random() < 0.04 * noise:
        e = typo(rng, e.split("@")[0]) + "@" + e.split("@")[1]
    if rng.random() < 0.05 * noise:
        e = e.upper()
    if rng.random() < 0.03 * noise:
        e = e.replace("gmail.com", "gmial.com").replace("yahoo.co.in", "yaho.co.in")
    return e


def _abbr(rng, s: str, p: float) -> str:
    for full, short in ADDRESS_ABBR:
        if full in s and rng.random() < p:
            s = s.replace(full, short)
    return s


def address_parts(rng, a: dict, noise: float = 1.0) -> dict:
    """Return noisy address components: line1, line2, locality, city, state, pin."""
    line1 = a["house"] + (f", {a['society']}" if a.get("society") else "")
    line2 = a["street"] + (f", {a['landmark']}" if a.get("landmark") else "")
    if rng.random() < 0.10 * noise:
        line1 = a["society"] or a["street"]
    line1, line2 = _abbr(rng, line1, 0.3 * noise), _abbr(rng, line2, 0.3 * noise)
    locality = None if rng.random() < 0.10 * noise else a["locality"]
    city = a["city"]
    if a["aliases"] and rng.random() < 0.22 * noise:
        city = rng.choice(a["aliases"])
    pin = a["pin"]
    if rng.random() < 0.15 * noise:
        pin = None
    elif rng.random() < 0.03 * noise:
        i = rng.randrange(6)
        pin = pin[:i] + str((int(pin[i]) + 1) % 10) + pin[i + 1:]
    out = {"line1": line1, "line2": line2, "locality": locality, "city": city, "state": a["state"], "pin": pin}
    if rng.random() < 0.2:
        out = {k: (v.upper() if isinstance(v, str) else v) for k, v in out.items()}
    return out


def address_oneline(rng, a: dict, noise: float = 1.0) -> str:
    p = address_parts(rng, a, noise)
    order = [p["line1"], p["line2"], p["locality"], p["city"], p["state"], p["pin"]]
    sep = rng.choice([", ", ", ", " ", ","])
    if rng.random() < 0.3:
        order = [x for x in order if x and x != p["state"]]  # state often omitted
    return sep.join(x for x in order if x)


def ocr_noise(rng, s: str, p: float) -> str:
    """Per-character OCR confusion (O/0, I/1, S/5, B/8 ...)."""
    out = []
    for ch in s:
        if ch in _OCR and rng.random() < p:
            out.append(_OCR[ch])
        else:
            out.append(ch)
    return "".join(out)
