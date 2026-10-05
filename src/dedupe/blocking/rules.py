"""Blocking rules. Each rule is a SQL query over the `records` view returning (rid, key) rows;
two records sharing a key become a candidate pair. Rules are data, so users can add their own later."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    name: str
    sql: str  # SELECT rid, key FROM records ... ; NULL keys are ignored
    description: str = ""


def _simple(col: str) -> str:
    return f"SELECT rid, {col} AS key FROM records WHERE {col} IS NOT NULL"


def _combo(expr: str, *required: str) -> str:
    cond = " AND ".join(f"{c} IS NOT NULL" for c in required)
    return f"SELECT rid, {expr} AS key FROM records WHERE {cond}"


DEFAULT_RULES = [
    Rule("pan", _simple("pan"), "same PAN"),
    Rule("aadhaar", _simple("aadhaar_token"), "same Aadhaar token"),
    Rule("passport", _simple("passport_no"), "same passport number"),
    Rule("driving_licence", _simple("dl_no"), "same driving licence"),
    Rule("voter_id", _simple("voter_id"), "same voter ID"),
    Rule("phone", "SELECT rid, key FROM (SELECT rid, unnest([mobile, mobile_alt]) AS key FROM records) WHERE key IS NOT NULL",
         "same number in either phone field"),
    Rule("email", _simple("email_key"), "same email (alias-normalised)"),
    Rule("surname_dob", _combo("name_last_phon || '|' || dob", "name_last_phon", "dob"), "surname sound + full DOB"),
    Rule("first_dob", _combo("name_first_phon || '|' || dob", "name_first_phon", "dob"), "first-name sound + full DOB"),
    Rule("names_birthyear", _combo("name_tokens_phon || '|' || dob_year", "name_tokens_phon", "dob_year"),
         "all name sounds (order-free) + birth year; survives swapped day/month"),
    Rule("surname_initial_birthyear",
         _combo("name_last_phon || '|' || substr(name_first, 1, 1) || '|' || dob_year", "name_last_phon", "name_first", "dob_year"),
         "surname sound + first initial + birth year; survives initials"),
    Rule("name_pin3", _combo("name_first_phon || '|' || name_last_phon || '|' || substr(address_pin, 1, 3)",
                             "name_first_phon", "name_last_phon", "address_pin"), "first+last sound + PIN prefix"),
    Rule("name_city", _combo("name_first_phon || '|' || name_last_phon || '|' || city", "name_first_phon", "name_last_phon", "city"),
         "first+last sound + city; links ID-less records (leads) to documents"),
    Rule("dob_pin", _combo("dob || '|' || address_pin", "dob", "address_pin"), "full DOB + PIN"),
]
