"""Column types recognised from the values (Phase A step 1), the mapping suggested from them, and warnings for mislabelled columns."""
import pyarrow as pa
import pytest

from dedupe.mapping.detect import _gstin_ok, detect_values, profile
from dedupe.mapping.suggest import record_id_column, suggest, warnings_for
from dedupe.standardise.identifiers import phone

# Aadhaar numbers with a valid Verhoeff check digit (made up: the published test numbers)
AADHAAR = ["234123412346", "2341 2341 2346", "999941057058", "9999 4105 7058", "496858245152", "234123412346"]


def kind(values):
    return detect_values(values)["type"]


@pytest.mark.parametrize("values,expected", [
    (["ABCPS1234K", "AAAPZ9999Q", "bbbps1111c", "CDEPK4321L", "XYZPM0001A"], "pan"),
    (AADHAAR, "aadhaar"),
    (["XXXX XXXX 1234", "xxxx-xxxx-9876", "XXXXXXXX4321", "XXXX XXXX 0001", "****-****-5555"], "aadhaar_masked"),
    (["+91-98765-10017", "091-98765-10028", "919876510024", "98111 22222", "7012345678", "6000000001"], "mobile"),
    (["a.b@example.com", "riya. menon@example. com", "X@Y.IN", "rahul_s@gmail.com", "p@q.co.in"], "email"),
    (["12-05-1985", "1990-01-31", "03/11/1972", "15-Aug-1960", "29/02/1988", "01-01-1950"], "dob"),
    (["2023-04-01 10:22:00", "2024-01-15T09:00:00Z", "01/04/2023 10:22", "2025-07-09 00:00:00", "2022-12-31 23:59:59"], "record_date"),
    (["M", "F", "Male", "female", "F", "M", "Other"], "gender"),
    (["Maharashtra", "KA", "tamil nadu", "Goa", "Sikkim", "MH"], "state"),
    (["Mumbai", "Bangalore", "pune", "New Delhi", "Chennai", "Kolkata"], "city"),
    (["400001", "560034", "110016", "600028", "700019", "411045"], "pincode"),
    (["Rahul Sharma", "Anita Rao", "S. K. Gupta", "Priya", "Mohammed Irfan Khan", "Lakshmi Narayanan"], "person_name"),
    (["Sharma Traders Pvt Ltd", "Rao Enterprises", "Gupta & Sons LLP", "City Hospital", "Shree Textiles", "Rahul Sharma"], "organisation_name"),
    (["12 MG Road, Pune", "Flat 4B, Sea View Apts", "52 Garden View", "H.No 7, Sector 15, Noida 201301", "3 heritage lane"], "address"),
    (["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "27AAPFU0939F1ZV"], "gstin"),
    (["SBIN0001234", "HDFC0000123", "ICIC0001000", "UTIB0000004", "KKBK0000958"], "ifsc"),
    (["MH12AB1234", "KA01MJ2022", "DL3CAB0001", "TN09Z1234", "GJ05XY9999"], "vehicle"),
    (["35", "42", "50-59", "60+", "28", "71"], "age"),
    (["8.75", "10.5", "12.25", "9.9", "11.0", "13.4"], "amount"),
    (["500000", "250000", "1000000", "500000", "750000", "250000"], "amount"),
    (["1234", "0042", "9876", "5555", "0001", "4321"], "four_digits"),
    (["scan_001.jpg", "scan_002.JPG", "doc/a.pdf", "x.png", "front.jpeg"], "file_ref"),
    (["ABCPS1234K", "ABC1234567", "A1234567", "BCDPK4321L", "XYZ7654321"], "mixed_ids"),
    (["C0001", "C0002", "C0003", "C0004", "C0005", "C0006"], "row_id"),
    (["Gold", "Silver", "Gold", "Gold", "Silver", "Platinum", "Gold", "Silver", "Gold", "Gold", "Silver"], "category"),
    (["ok", "bad", None, "", "x"], None),                                 # fewer than five real values: no verdict
])
def test_each_type_is_recognised_from_its_values(values, expected):
    assert kind(values) == expected


def test_checksums_decide_not_just_the_shape():
    bad_aadhaar = ["234123412345", "999941057057", "496858245151", "234123412341", "999941057051"]
    assert kind(bad_aadhaar) != "aadhaar"                                  # right length, wrong check digit
    assert _gstin_ok("27AAPFU0939F1ZV") and not _gstin_ok("27AAPFU0939F1ZW")


def test_trunk_prefixed_phones_are_now_kept_by_the_cleaning():
    assert phone("091-98765-10028") == "9876510028" == phone("0091 98765 10028") == phone("+91 98765 10028")
    assert phone("0912345678901") is None


def test_only_shares_leave_the_detector_never_values():
    d = detect_values(["ABCPS1234K", "AAAPZ9999Q", "BBBPS1111C", "CDEPK4321L", "XYZPM0001A", None])
    assert d["filled"] == pytest.approx(5 / 6, abs=0.001) and d["distinct"] == 1.0 and d["share"] == 1.0
    assert "ABCPS1234K" not in repr(d)


def test_values_drive_the_mapping_when_the_names_say_nothing():
    t = pa.table({"c1": ["R0001", "R0002", "R0003", "R0004", "R0005", "R0006"],
                  "c2": ["Rahul Sharma", "Anita Rao", "Priya Nair", "Mohan Das", "Irfan Khan", "Asha Rani"],
                  "c3": ["98111 22222", "+91 9822233333", "7012345678", "6000000001", "9123456789", "8888812345"],
                  "c4": ["ABCPS1234K", "AAAPZ9999Q", "BBBPS1111C", "CDEPK4321L", "XYZPM0001A", "LMNPQ1234R"],
                  "c5": ["12-05-1985", "1990-01-31", "03/11/1972", "15-Aug-1960", "29/02/1988", "01-01-1950"]})
    got = {s["field"]: (s["column"], s["why"]) for s in suggest(t)}
    assert got == {"full_name": ("c2", "values"), "mobile": ("c3", "values"), "pan": ("c4", "values"), "dob": ("c5", "values")}
    assert record_id_column(t, profile(t)) == "c1"


def test_a_column_named_pan_holding_phones_is_not_mapped_as_pan_and_is_flagged():
    phones = ["98111 22222", "+91 9822233333", "7012345678", "6000000001", "9123456789", "8888812345"]
    t = pa.table({"pan": phones, "name": ["Rahul Sharma", "Anita Rao", "Priya Nair", "Mohan Das", "Irfan Khan", "Asha Rani"]})
    got = {s["field"]: s["column"] for s in suggest(t)}
    assert got.get("mobile") == "pan" and "pan" not in got
    assert warnings_for({"pan": "pan"}, profile(t)) == [{"field": "pan", "column": "pan", "looks_like": "Indian mobile numbers"}]
    assert warnings_for({"mobile": "pan"}, profile(t)) == []


def test_names_still_rule_out_people_who_are_not_the_customer():
    names = ["Rahul Sharma", "Anita Rao", "Priya Nair", "Mohan Das", "Irfan Khan", "Asha Rani"]
    t = pa.table({"nominee_name": names, "applicant_name": list(reversed(names))})
    got = {s["field"]: s["column"] for s in suggest(t)}
    assert got["full_name"] == "applicant_name" and got["related_name"] == "nominee_name"
