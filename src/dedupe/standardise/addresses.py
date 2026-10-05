"""Address / city / state / PIN normalisation. No parsing into fully structured addresses yet; we extract
the strong signals (PIN, city, house number) and a normalised token string for fuzzy comparison."""
import re
from dataclasses import dataclass

from .geo import ADDRESS_ABBR, CITY_ALIASES, CITY_STATE, STATE_ALIASES, STATES

_PIN_RE = re.compile(r"(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)")
_KNOWN_CITIES = set(CITY_STATE) | set(CITY_ALIASES)
_STOP = {"india", "the", "and", "of"}


def city_name(raw: str | None) -> str | None:
    c = re.sub(r"[^a-z ]", " ", (raw or "").lower())
    c = " ".join(c.split())
    c = CITY_ALIASES.get(c, c)
    return c or None


def state_name(raw: str | None, city: str | None = None) -> str | None:
    s = " ".join(re.sub(r"[^a-z ]", " ", (raw or "").lower()).split())
    s = STATE_ALIASES.get(s, s)
    if s in STATES:
        return s
    return CITY_STATE.get(city) if city else None


def pincode(raw: str | None) -> str | None:
    m = _PIN_RE.search(raw or "")
    return m[1] + m[2] if m else None


@dataclass
class Address:
    norm: str | None = None
    pin: str | None = None
    city: str | None = None
    state: str | None = None
    house: str | None = None


def parse_address(text: str | None, city_col: str | None = None, state_col: str | None = None,
                  pin_col: str | None = None) -> Address:
    pin = pincode(pin_col) or pincode(text)
    s = (text or "").lower()
    s = re.sub(r"(?<!\d)[1-9]\d{2}\s?\d{3}(?!\d)", " ", s)  # PIN is kept separately
    s = re.sub(r"\bh\.?\s?no\.?", "hno ", s)
    s = re.sub(r"[^a-z0-9/ ]", " ", s)
    toks = [ADDRESS_ABBR.get(t, t) for t in s.split()]
    toks = " ".join(toks).split()
    city = city_name(city_col)
    if not city or city not in CITY_STATE:
        found = None
        for n in (2, 1):  # bigrams first: "new delhi", "navi mumbai"
            for i in range(len(toks) - n, -1, -1):
                cand = " ".join(toks[i:i + n])
                if cand in _KNOWN_CITIES:
                    found = CITY_ALIASES.get(cand, cand)
                    break
            if found:
                break
        city = found or city
    state = state_name(state_col, city)
    # house number: first token containing a digit, e.g. 103/10
    house = next((t for t in toks if any(c.isdigit() for c in t)), None)
    drop = set(_STOP) | ({city} if city else set()) | ({state} if state else set())
    toks = [t for t in toks if t not in drop and t not in _KNOWN_CITIES and t not in STATES]
    return Address(norm=" ".join(toks) or None, pin=pin, city=city, state=state, house=house)
