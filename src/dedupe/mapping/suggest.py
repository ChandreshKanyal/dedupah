"""Suggest a column -> canonical field mapping from column names and sample values (a human confirms it)."""
import re

import pyarrow as pa

from .detect import profile

_NAME_RULES: dict[str, tuple[list[str], list[str]]] = {
    # field: (synonyms, words that disqualify a column)
    "full_name": (["fullname", "name", "applicantname", "partyname", "customername", "leadname", "nameondoc"],
                  ["father", "nominee", "coapplicant", "employer", "product", "branch", "agent", "manager", "file", "campaign"]),
    "father_name": (["fathername", "fatherhusbandname", "fathernameondoc"], []),
    "dob": (["dob", "dateofbirth", "birthdate", "applicantdob", "dobondoc"], []),
    "gender": (["gender", "sex", "applicantgender", "genderondoc"], []),
    "marital_status": (["maritalstatus"], []),
    "mobile": (["mobile", "phone", "mobileno", "phoneno", "contactno", "cell"], ["alt", "coapplicant", "secondary", "old", "branch"]),
    "mobile_alt": (["altmobile", "alternatemobile", "secondaryphone", "mobile2"], []),
    "email": (["email", "emailid", "mail"], []),
    "address": (["address", "currentaddress", "addressline1", "addr", "addressondoc"], ["permanent", "email"]),
    "address_alt": (["permanentaddress", "previousaddress"], []),
    "city": (["city", "town"], []),
    "state": (["state"], ["status"]),
    "pincode": (["pincode", "pin", "zip", "postalcode"], []),
    "customer_number": (["cif", "cifno", "cifnumber", "ucic", "ucicno", "customernumber", "customerno", "clientid", "clientno", "custno"], ["agent", "branch"]),
    "pan": (["pan", "panno", "pannumber"], ["coapplicant"]),
    "aadhaar": (["aadhaar", "aadhaarno", "aadhar", "uid"], ["last4"]),
    "aadhaar_last4": (["aadhaarlast4"], []),
    "passport_no": (["passport", "passportno"], []),
    "dl_no": (["dl", "dlno", "drivinglicence", "drivinglicense"], []),
    "voter_id": (["voterid", "epic"], []),
    "occupation": (["occupation", "profession"], ["class"]),
    "annual_income": (["annualincome"], []),
    "related_name": (["nomineename", "coapplicantname"], []),
    "party_role": (["partyrole"], []),
    "relation_to_proposer": (["relationtoproposer"], []),
}

SOFT_TYPES = {"person_name", "address", "city", "state", "age", "category"}
# Types the values prove beyond doubt (checksums, fixed shapes): a column named for another field but holding these is flagged.
STRONG_TYPES = {"aadhaar", "pan", "gstin", "ifsc", "email", "mobile", "voter_id", "passport", "driving_licence", "vehicle", "token",
                "file_ref", "aadhaar_masked"}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_score(field: str, col: str) -> float:
    syn, neg = _NAME_RULES[field]
    n = _norm(col)
    if any(x in n for x in neg):
        return 0.0
    if n in syn:
        return 1.0
    if any(len(s) >= 4 and s in n for s in syn):
        return 0.7
    return 0.0


def _value_score(field: str, col: str, det: dict) -> float:
    """What the values say: 0.85 when the detected type is this field (0.75 for the softer types), unless the column's name rules the field
    out ("nominee_name" holds people's names but is not the customer's name)."""
    if det.get("field") != field:
        return 0.0
    if field in _NAME_RULES and any(x in _norm(col) for x in _NAME_RULES[field][1]):
        return 0.0
    return 0.75 if det["type"] in SOFT_TYPES else 0.85


def suggest(sample: pa.Table, threshold: float = 0.6, profiles: dict[str, dict] | None = None) -> list[dict]:
    """Greedy best-first assignment of columns to canonical fields from column names AND detected value types.
    Returns [{field, column, confidence, why}]; `why` is "name", "values" or "name and values"."""
    profiles = profiles if profiles is not None else profile(sample)
    scored = []
    for col in sample.column_names:
        det = profiles.get(col) or {}
        strong_other = det.get("type") in STRONG_TYPES and det.get("share", 0) >= 0.9
        for field in set(_NAME_RULES) | ({det["field"]} if det.get("field") else set()):
            ns = _name_score(field, col) if field in _NAME_RULES else 0.0
            if ns and strong_other and det.get("field") != field:
                ns /= 2                        # named "pan" but the values are clearly something else (phones, GST numbers ...)
            vs = _value_score(field, col, det)
            s = max(ns, vs) + (0.1 if ns and vs else 0.0)
            if s >= threshold:
                scored.append((s, field, col, "name and values" if ns and vs else "name" if ns >= vs else "values"))
    out, used_cols, used_fields = [], set(), set()
    for s, field, col, why in sorted(scored, key=lambda x: (-x[0], x[2])):
        if col in used_cols or field in used_fields:
            continue
        used_cols.add(col)
        used_fields.add(field)
        out.append({"field": field, "column": col, "confidence": round(min(s, 1.0), 2), "why": why})
    return sorted(out, key=lambda d: d["field"])


def warnings_for(mapping: dict[str, str], profiles: dict[str, dict]) -> list[dict]:
    """Mapped columns whose values clearly look like something else: [{field, column, looks_like}] (no values, only the detected type)."""
    out = []
    for field, col in mapping.items():
        det = profiles.get(col) if isinstance(col, str) else None
        if det and det.get("type") in STRONG_TYPES and det.get("share", 0) >= 0.9 and det.get("field") != field:
            out.append({"field": field, "column": col, "looks_like": det["description"]})
    return out


def record_id_column(sample: pa.Table, profiles: dict[str, dict]) -> str | None:
    """The column most likely to be each row's own id: a different value on every row, preferring names that say "id"."""
    cands = [c for c in sample.column_names if (profiles.get(c) or {}).get("type") == "row_id" and (profiles[c].get("filled") or 0) >= 0.99]
    cands.sort(key=lambda c: (not re.search(r"(^|_|\b)(id|no|number|key)$|id$", c.lower()), sample.column_names.index(c)))
    return cands[0] if cands else None
