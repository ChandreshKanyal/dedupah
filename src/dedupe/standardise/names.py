"""Name cleaning: honorifics, ordering, OCR repair, initials and a phonetic skeleton tuned for Indian names."""
import re
from dataclasses import dataclass, field

HONORIFICS = {"mr", "mrs", "ms", "miss", "mister", "shri", "shree", "sri", "smt", "sh", "kumari", "km", "dr", "late",
              "master", "prof", "er"}
ALIASES = {"mohd": "mohammed", "md": "mohammed", "muhammad": "mohammed", "mohammad": "mohammed",
           "mohamed": "mohammed", "kr": "kumar"}
_OCR_DIGIT_TO_LETTER = str.maketrans("013458", "oiebsb")
_ASPIRATES = [("bh", "b"), ("gh", "g"), ("kh", "k"), ("dh", "d"), ("jh", "j"), ("th", "t"), ("ph", "f"),
              ("sh", "s"), ("ch", "c"), ("ck", "k")]
_SINGLE = str.maketrans({"q": "k", "x": "k", "z": "j", "w": "v", "y": "i", "c": "k"})


def phonetic(token: str) -> str:
    """Consonant skeleton: Chandresh/Chandresha -> kndrs, Mohd/Muhammad/Md -> md, Sharma/Sarma -> srm."""
    s = re.sub(r"[^a-z]", "", token.lower())
    if not s:
        return ""
    for a, b in _ASPIRATES:
        s = s.replace(a, b)
    s = s.translate(_SINGLE)
    out = s[0]
    for ch in s[1:]:
        if ch in "aeiouh":
            continue
        if ch != out[-1]:
            out += ch
    return out


@dataclass
class NameParts:
    clean: str | None = None
    first: str | None = None
    middle: str | None = None
    last: str | None = None
    first_is_initial: bool = False
    first_phon: str | None = None
    last_phon: str | None = None
    tokens_phon: str | None = None
    tokens: list = field(default_factory=list)


def _tokens(raw: str) -> list[str]:
    s = raw.strip()
    if "," in s:  # "Sharma, Rajesh Kumar" -> "Rajesh Kumar Sharma"
        last, _, rest = s.partition(",")
        s = f"{rest} {last}"
    s = s.lower().replace("'", "").replace("`", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    out = []
    for t in s.split():
        if any(c.isalpha() for c in t) and any(c.isdigit() for c in t):  # OCR: "roh1t" -> "rohit"
            t = t.translate(_OCR_DIGIT_TO_LETTER)
        if t.isdigit():
            continue
        out.append(t)
    while out and out[0] in HONORIFICS:
        out.pop(0)
    return [ALIASES.get(t, t) for t in out]


def parse_name(raw: str | None) -> NameParts:
    if not raw or not raw.strip():
        return NameParts()
    toks = _tokens(raw)
    if not toks:
        return NameParts()
    first, last = toks[0], (toks[-1] if len(toks) > 1 else None)
    middle = " ".join(toks[1:-1]) or None
    phon = [phonetic(t) if len(t) > 1 else t for t in toks]
    return NameParts(
        clean=" ".join(toks), first=first, middle=middle, last=last,
        first_is_initial=len(first) == 1,
        first_phon=phon[0], last_phon=phon[-1] if last else None,
        tokens_phon=" ".join(sorted(set(p for p in phon if p))), tokens=toks,
    )
