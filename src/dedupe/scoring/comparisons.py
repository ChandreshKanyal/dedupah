"""Field comparisons for pair scoring. Each comparison is an ordered ladder of levels (first matching level wins).
Level -1 means "not comparable" (a value is missing on either side): it contributes nothing, never evidence against.
Conditions are SQL over a pairs table with l_<col> / r_<col> columns."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    value: int
    label: str
    sql: str
    tf_key: str | None = None  # SQL expr: the agreed value, looked up in the comparison's term-frequency table


@dataclass(frozen=True)
class Comparison:
    name: str
    null_sql: str  # when true the comparison is not applicable (-1)
    levels: tuple  # ordered high -> low; implicit final level 0 = "otherwise"
    tf_table_sql: str | None = None  # SELECT key, tf FROM records ...
    blocked_by: tuple = ()  # blocking rules that already use this field (EM must not train on those pairs)
    else_label: str = "different"

    @property
    def n_levels(self) -> int:
        return max(l.value for l in self.levels) + 1

    def labels(self) -> dict:
        return {**{l.value: l.label for l in self.levels}, 0: self.else_label}


def _both(col):
    return f"l_{col} IS NOT NULL AND r_{col} IS NOT NULL"


def _either_missing(col):
    return f"l_{col} IS NULL OR r_{col} IS NULL"


_FIRST_INITIAL_MATCH = ("((l_name_first_is_initial AND substr(r_name_first,1,1) = l_name_first)"
                        " OR (r_name_first_is_initial AND substr(l_name_first,1,1) = r_name_first))")

NAME = Comparison(
    "name", _either_missing("name_clean"),
    (
        Level(7, "identical cleaned name", "l_name_clean = r_name_clean", "'T|' || l_name_tokens_phon"),
        Level(6, "same name sounds (any order)", "l_name_tokens_phon = r_name_tokens_phon", "'T|' || l_name_tokens_phon"),
        Level(5, "first+last sound equal", "l_name_first_phon = r_name_first_phon AND l_name_last_phon = r_name_last_phon",
              "'F|' || l_name_first_phon || '|' || l_name_last_phon"),
        Level(4, "initial + same surname", f"l_name_last_phon = r_name_last_phon AND {_FIRST_INITIAL_MATCH}"),
        Level(3, "one side first-name only, first names agree",
              "(l_name_last IS NULL OR r_name_last IS NULL) AND l_name_first_phon = r_name_first_phon"),
        Level(2, "fuzzy full-name match (JW>=0.90)", "jaro_winkler_similarity(l_name_clean, r_name_clean) >= 0.90"),
        Level(1, "same surname, different first name", "l_name_last_phon = r_name_last_phon"),
    ),
    tf_table_sql="""SELECT 'T|' || name_tokens_phon AS key, count(*)::DOUBLE / (SELECT count(name_tokens_phon) FROM records) AS tf
                    FROM records WHERE name_tokens_phon IS NOT NULL GROUP BY 1
                    UNION ALL
                    SELECT 'F|' || name_first_phon || '|' || name_last_phon, count(*)::DOUBLE / (SELECT count(name_last_phon) FROM records)
                    FROM records WHERE name_last_phon IS NOT NULL GROUP BY 1""",
    blocked_by=("surname_dob", "first_dob", "names_birthyear", "surname_initial_birthyear", "name_pin3", "name_city"),
)

DOB = Comparison(
    "dob", "l_dob_year IS NULL OR r_dob_year IS NULL",
    (
        Level(5, "exact date", "l_dob = r_dob AND NOT l_dob_placeholder", "l_dob"),
        Level(4, "exact date, but a 1-Jan default", "l_dob = r_dob"),
        Level(3, "day and month swapped", "l_dob_year = r_dob_year AND l_dob_day = r_dob_month AND l_dob_month = r_dob_day"),
        Level(2, "same year (partial or differing day/month)", "l_dob_year = r_dob_year"),
        Level(1, "same day and month, different year", "l_dob_day = r_dob_day AND l_dob_month = r_dob_month"),
    ),
    tf_table_sql="SELECT dob AS key, count(*)::DOUBLE / (SELECT count(dob) FROM records) AS tf FROM records WHERE dob IS NOT NULL GROUP BY 1",
    blocked_by=("surname_dob", "first_dob", "names_birthyear", "surname_initial_birthyear", "dob_pin"),
)

PHONE = Comparison(
    "phone", "(l_mobile IS NULL AND l_mobile_alt IS NULL) OR (r_mobile IS NULL AND r_mobile_alt IS NULL)",
    (
        Level(2, "same primary number", "l_mobile = r_mobile", "l_mobile"),
        Level(1, "number matches an alternate number",
              "l_mobile = r_mobile_alt OR l_mobile_alt = r_mobile OR l_mobile_alt = r_mobile_alt",
              "CASE WHEN l_mobile = r_mobile_alt THEN l_mobile ELSE l_mobile_alt END"),
    ),
    tf_table_sql="""SELECT v AS key, count(*)::DOUBLE / (SELECT count(*) FROM records WHERE mobile IS NOT NULL OR mobile_alt IS NOT NULL) AS tf
                    FROM (SELECT unnest([mobile, mobile_alt]) AS v FROM records) WHERE v IS NOT NULL GROUP BY 1""",
    blocked_by=("phone",),
)

EMAIL = Comparison(
    "email", _either_missing("email_key"),
    (Level(1, "same email", "l_email_key = r_email_key", "l_email_key"),),
    tf_table_sql="SELECT email_key AS key, count(*)::DOUBLE / (SELECT count(email_key) FROM records) AS tf FROM records WHERE email_key IS NOT NULL GROUP BY 1",
    blocked_by=("email",),
)

PAN = Comparison("pan", _either_missing("pan"), (Level(1, "same PAN", "l_pan = r_pan"),), blocked_by=("pan",),
                 else_label="different PANs")

AADHAAR = Comparison(
    "aadhaar",
    "NOT ((l_aadhaar_token IS NOT NULL AND r_aadhaar_token IS NOT NULL) OR (l_aadhaar_last4 IS NOT NULL AND r_aadhaar_last4 IS NOT NULL))",
    (
        Level(3, "same Aadhaar token", "l_aadhaar_token IS NOT NULL AND r_aadhaar_token IS NOT NULL AND l_aadhaar_token = r_aadhaar_token"),
        Level(2, "same last-4 (no full number on one side)",
              "NOT (l_aadhaar_token IS NOT NULL AND r_aadhaar_token IS NOT NULL) AND l_aadhaar_last4 = r_aadhaar_last4"),
    ),
    blocked_by=("aadhaar",), else_label="different Aadhaar",
)

GOV_ID = Comparison(
    "gov_id",
    "NOT ((l_passport_no IS NOT NULL AND r_passport_no IS NOT NULL) OR (l_dl_no IS NOT NULL AND r_dl_no IS NOT NULL)"
    " OR (l_voter_id IS NOT NULL AND r_voter_id IS NOT NULL))",
    (Level(1, "same passport / licence / voter ID",
           "l_passport_no = r_passport_no OR l_dl_no = r_dl_no OR l_voter_id = r_voter_id"),),
    blocked_by=("passport", "driving_licence", "voter_id"), else_label="different document numbers",
)

ADDRESS = Comparison(
    "address", "(l_address_pin IS NULL AND l_city IS NULL) OR (r_address_pin IS NULL AND r_city IS NULL)",
    (
        Level(4, "same PIN + house number + similar text",
              "l_address_pin = r_address_pin AND l_address_house = r_address_house AND jaro_winkler_similarity(l_address_norm, r_address_norm) >= 0.85"),
        Level(3, "same PIN + similar text",
              "l_address_pin = r_address_pin AND jaro_winkler_similarity(l_address_norm, r_address_norm) >= 0.85"),
        Level(2, "same PIN", "l_address_pin = r_address_pin"),
        Level(5, "same city, PIN missing on one side", "l_city = r_city AND (l_address_pin IS NULL OR r_address_pin IS NULL)"),
        Level(1, "same city or PIN area, different PINs",
              "l_city = r_city OR substr(l_address_pin, 1, 3) = substr(r_address_pin, 1, 3)"),
    ),
    blocked_by=("name_pin3", "name_city", "dob_pin"),
)

GENDER = Comparison("gender", _either_missing("gender"), (Level(1, "same gender", "l_gender = r_gender"),),
                    else_label="different gender")

COMPARISONS = [NAME, DOB, PHONE, EMAIL, PAN, AADHAAR, GOV_ID, ADDRESS, GENDER]
STRONG_ID = ("pan", "aadhaar", "gov_id")  # used to seed EM: agreement ~ same person, disagreement ~ different people

RECORD_COLUMNS = [
    "name_clean", "name_first", "name_last", "name_first_is_initial", "name_first_phon", "name_last_phon", "name_tokens_phon",
    "dob", "dob_year", "dob_month", "dob_day", "dob_placeholder", "mobile", "mobile_alt", "email_key", "pan",
    "aadhaar_token", "aadhaar_last4", "passport_no", "dl_no", "voter_id", "address_pin", "address_house",
    "address_norm", "city", "gender",
]


def level_case_sql(c: Comparison) -> str:
    whens = " ".join(f"WHEN {l.sql} THEN {l.value}" for l in c.levels)
    return f"CASE WHEN {c.null_sql} THEN -1 {whens} ELSE 0 END"


def tf_key_case_sql(c: Comparison) -> str:
    whens = " ".join(f"WHEN {l.sql} THEN {l.tf_key}" for l in c.levels if l.tf_key)
    return f"CASE WHEN {c.null_sql} THEN NULL {whens} ELSE NULL END" if whens else "NULL"


def pairs_view_sql(src_pairs: str, records: str = "records") -> str:
    """SQL producing the wide pairs table (l_*/r_* columns) from a table with l_rid, r_rid."""
    cols = ", ".join(f"a.{c} AS l_{c}, b.{c} AS r_{c}" for c in RECORD_COLUMNS)
    return (f"SELECT p.*, {cols} FROM {src_pairs} p "
            f"JOIN {records} a ON a.rid = p.l_rid JOIN {records} b ON b.rid = p.r_rid")
