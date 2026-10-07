"""Suggest a column -> canonical field mapping from column names and sample values (a human confirms it)."""
import re

import pyarrow as pa

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

_PATTERNS: dict[str, re.Pattern] = {
    "pan": re.compile(r"^[A-Z]{5}\d{4}[A-Z]$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "mobile": re.compile(r"^(\+?91[\s-]?)?0?[6-9]\d{4}[\s-]?\d{5}$|^(\+?91[\s-]?)?[6-9]\d{2}\s\d{3}\s\d{4}$"),
    "aadhaar": re.compile(r"^[2-9]\d{3}\s?\d{4}\s?\d{4}$"),
    "pincode": re.compile(r"^[1-9]\d{5}$"),
    "dob": re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4}|\d{2}-[A-Za-z]{3}-\d{4})$"),
    "gender": re.compile(r"^(M|F|Male|Female|MALE|FEMALE|Other)$"),
}


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


def _pattern_score(field: str, values: list[str]) -> float:
    pat = _PATTERNS.get(field)
    vals = [v.strip() for v in values if v and v.strip()]
    if not pat or len(vals) < 5:
        return 0.0
    return 0.9 if sum(bool(pat.match(v)) for v in vals) / len(vals) >= 0.8 else 0.0


def suggest(sample: pa.Table, threshold: float = 0.6) -> list[dict]:
    """Greedy best-first assignment of columns to canonical fields. Returns [{field, column, confidence}]."""
    scored = []
    for col in sample.column_names:
        values = sample[col].to_pylist()
        for field in _NAME_RULES:
            ns, ps = _name_score(field, col), _pattern_score(field, values)
            s = max(ns, ps) + (0.1 if ns and ps else 0.0)
            if s >= threshold:
                scored.append((s, field, col))
    out, used_cols, used_fields = [], set(), set()
    for s, field, col in sorted(scored, key=lambda x: -x[0]):
        if col in used_cols or field in used_fields:
            continue
        used_cols.add(col)
        used_fields.add(field)
        out.append({"field": field, "column": col, "confidence": round(min(s, 1.0), 2)})
    return sorted(out, key=lambda d: d["field"])
