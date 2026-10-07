"""Phones, email, PAN, Aadhaar (tokenised), passport, driving licence, voter ID. Valid value or None."""
import hashlib
import hmac
import os
import re
import warnings

from .verhoeff import verhoeff_valid

_TO_LETTER = str.maketrans("015826", "OISBZG")
_TO_DIGIT = str.maketrans("OISBZGL", "0158261")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_PAN_HOLDER = set("ABCFGHLJPT")
_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)+$")
_DEV_KEY = b"dev-only-insecure-key-set-DEDUPE_TOKEN_KEY"
_warned = False


def phone(raw: str | None) -> str | None:
    """Indian mobile -> 10 digits starting 6-9, else None (landlines/garbage rejected)."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) != 10 or d[0] not in "6789" or len(set(d)) == 1 or d in ("9876543210", "6789012345"):
        return None
    return d


def email(raw: str | None):
    """Returns (email, matching_key, domain). Key drops +tags and gmail dots so aliases collide."""
    e = (raw or "").strip().lower()
    if not _EMAIL_RE.match(e):
        return None, None, None
    local, domain = e.split("@")
    key_local = local.split("+")[0]
    if domain in ("gmail.com", "googlemail.com"):
        key_local, domain_key = key_local.replace(".", ""), "gmail.com"
    else:
        domain_key = domain
    return e, f"{key_local}@{domain_key}", domain


def pan(raw: str | None):
    """Returns (pan, repaired). Fixes OCR letter/digit confusion by position: AAAAA9999A."""
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if len(s) != 10:
        return None, False
    fixed = "".join(c.translate(_TO_LETTER) if i in (0, 1, 2, 3, 4, 9) else c.translate(_TO_DIGIT) for i, c in enumerate(s))
    if not _PAN_RE.match(fixed) or fixed[3] not in _PAN_HOLDER:
        return None, False
    return fixed, fixed != s


def _token_key() -> bytes:
    global _warned
    k = os.environ.get("DEDUPE_TOKEN_KEY")
    if k:
        return k.encode()
    if os.environ.get("DEDUPE_ENV", "").lower() == "production":
        raise RuntimeError("DEDUPE_TOKEN_KEY must be set when DEDUPE_ENV=production (refusing to use the development key)")
    if not _warned:
        warnings.warn("DEDUPE_TOKEN_KEY not set: using an insecure development key for Aadhaar tokens", stacklevel=2)
        _warned = True
    return _DEV_KEY


_REF_KEY = re.compile(r"[A-Za-z0-9_-]{8,128}")


def aadhaar_reference(raw: str | None) -> str | None:
    """A reference key issued by the customer's own Aadhaar Data Vault. Two records match when they carry the same key (that is, only within
    one organisation's vault). Prefixed so it can never equal a token we derive ourselves."""
    s = (raw or "").strip()
    return "ref:" + s if _REF_KEY.fullmatch(s) else None


def aadhaar(raw: str | None):
    """Returns (token, last4). Only a keyed hash leaves this function, never the number itself."""
    s = re.sub(r"[\s\-]", "", (raw or "").upper()).translate(_TO_DIGIT)
    if not re.fullmatch(r"[2-9]\d{11}", s) or not verhoeff_valid(s):
        return None, None
    token = hmac.new(_token_key(), b"aadhaar:" + s.encode(), hashlib.sha256).hexdigest()[:32]
    return token, s[-4:]


def customer_number(raw) -> str | None:
    """An organisation's own customer number (CIF, UCIC, client id): upper case, no spaces or separators; leading zeros dropped from
    all-digit numbers (a spreadsheet often drops them); placeholders and very short values refused."""
    s = re.sub(r"[\s\-/._]", "", str(raw or "")).upper()
    if s.endswith("0") and re.fullmatch(r"\d+\.0+", str(raw or "").strip()):
        s = str(raw).strip().split(".")[0]
    if s.isdigit():
        s = s.lstrip("0")
    if len(s) < 3 or s in ("NULL", "NONE", "NIL") or not s.isalnum() or len(set(s)) == 1:
        return None
    return s


def passport(raw: str | None) -> str | None:
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if len(s) == 8:
        s = s[0] + s[1:].translate(_TO_DIGIT)
    return s if re.fullmatch(r"[A-Z]\d{7}", s) else None


def driving_licence(raw: str | None) -> str | None:
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if len(s) < 13 or len(s) > 16:
        return None
    s = s[:2] + s[2:].translate(_TO_DIGIT)
    return s if re.fullmatch(r"[A-Z]{2}\d{11,14}", s) else None


def voter_id(raw: str | None) -> str | None:
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if len(s) != 10:
        return None
    s = s[:3].translate(_TO_LETTER) + s[3:].translate(_TO_DIGIT)
    return s if re.fullmatch(r"[A-Z]{3}\d{7}", s) else None
