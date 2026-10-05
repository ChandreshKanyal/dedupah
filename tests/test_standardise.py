import json
import os
import random

import duckdb
import pytest

from dedupe.ingest import ingest
from dedupe.standardise import dates
from dedupe.standardise import identifiers as ids
from dedupe.standardise.addresses import parse_address
from dedupe.standardise.names import parse_name, phonetic
from dedupe.standardise.quality import field_report
from dedupe.standardise.run import run as standardise_run
from dedupe.synthetic import generate
from dedupe.synthetic.identifiers import aadhaar as make_aadhaar


def test_names_honorifics_order_initials_ocr():
    n = parse_name("Mr. Rajesh Kumar Sharma")
    assert (n.first, n.middle, n.last) == ("rajesh", "kumar", "sharma")
    assert parse_name("SHARMA, RAJESH").clean == "rajesh sharma"
    assert parse_name("R. K. Sharma").first_is_initial
    assert parse_name("ROH1T AGARWAL").clean == "rohit agarwal"
    assert parse_name("Smt. Anita Verma").clean == "anita verma"
    assert parse_name("  ").clean is None and parse_name(None).clean is None
    assert parse_name("Madonna").last is None


@pytest.mark.parametrize("group", [
    ["Mohd", "Muhammad", "Md", "Mohammed", "Mohamad"],
    ["Chandresh", "Chandresha", "Chandersh"],
    ["Sharma", "Sarma"],
    ["Agarwal", "Aggarwal", "Agrawal"],
    ["Srivastava", "Shrivastava"],
    ["Krishna", "Krishnaa"],
])
def test_phonetic_groups_collide(group):
    assert len({phonetic(x) for x in group}) == 1


def test_phonetic_keeps_different_names_apart():
    assert phonetic("Rahul") != phonetic("Rohit") and phonetic("Sharma") != phonetic("Verma")


def test_phone():
    assert ids.phone("+91 81303 65307") == ids.phone("08130365307") == ids.phone("81303-65307") == "8130365307"
    for bad in ["1234567890", "5130365307", "9999999999", "022-12345678", "", None, "98765"]:
        assert ids.phone(bad) is None


def test_email_key_collapses_aliases():
    assert ids.email("John.Doe+promo@Gmail.com")[1] == ids.email("johndoe@gmail.com")[1] == "johndoe@gmail.com"
    assert ids.email("not an email") == (None, None, None) and ids.email("a@b") == (None, None, None)


def test_pan_validation_and_ocr_repair():
    assert ids.pan("ABCPD1234E") == ("ABCPD1234E", False)
    assert ids.pan(" abcpd1234e ") == ("ABCPD1234E", False)
    assert ids.pan("ABCPDI234E") == ("ABCPD1234E", True)  # I -> 1 in a digit position
    assert ids.pan("ABCPD1234E"[:-1] + "5") == ("ABCPD1234S", True)  # 5 -> S in a letter position
    assert ids.pan("ABCXD1234E") == (None, False)  # 4th char X is not a valid holder type
    assert ids.pan("ABC") == (None, False) and ids.pan(None) == (None, False)


def test_aadhaar_tokenised_and_validated(monkeypatch):
    monkeypatch.setenv("DEDUPE_TOKEN_KEY", "k1")
    a = make_aadhaar(random.Random(3))
    tok, last4 = ids.aadhaar(a)
    assert tok and last4 == a[-4:] and a not in tok
    assert ids.aadhaar(f"{a[:4]} {a[4:8]} {a[8:]}")[0] == tok  # formatting does not change the token
    bad = a[:-1] + str((int(a[-1]) + 1) % 10)
    assert ids.aadhaar(bad) == (None, None) and ids.aadhaar("123") == (None, None)
    monkeypatch.setenv("DEDUPE_TOKEN_KEY", "k2")
    assert ids.aadhaar(a)[0] != tok  # different key -> different token


def test_other_ids():
    assert ids.passport("a1234567") == "A1234567" and ids.passport("12345678") is None
    assert ids.driving_licence("MH-01 2014 3504454") == "MH0120143504454" and ids.driving_licence("XX") is None
    assert ids.voter_id("abc1234567") == "ABC1234567" and ids.voter_id("1234567890") is None


def test_dob_formats_and_edge_cases():
    full = ("1985-03-14", 1985, 3, 14, "full", False)
    for s in ["14/03/1985", "14-03-1985", "14-Mar-1985", "1985-03-14", "03/14/1985", "14.03.1985"]:
        assert dates.parse_dob(s) == full, s
    assert dates.parse_dob("1985") == (None, 1985, None, None, "year", False)
    assert dates.parse_dob("01/01/1990")[5] is True  # default-date suspicion
    for bad in ["31/02/1990", "1850", "", None, "garbage", "14/03/2999"]:
        assert dates.parse_dob(bad)[0] is None


def test_address_extraction_and_normalisation():
    a = parse_address("H.No 23/19, Shree Residency, Station Rd, Rajendra Nagar, Patna, Bihar, 800 947")
    assert (a.pin, a.city, a.state, a.house) == ("800947", "patna", "bihar", "23/19")
    b = parse_address("H.No 23/19, Shree Residency, Station Road, Rajendra Nagar, Patna, 800947")
    assert a.norm == b.norm
    assert parse_address("12 MG Road, Bombay").city == "mumbai"
    assert parse_address("Flat 4, Janakpuri, New Delhi 110058").city == "delhi"
    assert parse_address("x", city_col="Bangalore").city == "bengaluru"
    assert parse_address(None).norm is None


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    d = tmp_path_factory.mktemp("p")
    generate(80, seed=9, out=d / "syn")
    cfg = json.loads(open("configs/synthetic_sources.json", encoding="utf-8").read())
    for s in cfg["sources"]:
        s["connector"]["path"] = str(d / "syn" / os.path.basename(s["connector"]["path"]))
    (d / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    ingest(d / "cfg.json", d / "canon.parquet")
    stats = standardise_run(d / "canon.parquet", d / "std.parquet")
    return d, stats


def test_standardise_keeps_every_record_and_never_leaks_raw_aadhaar(pipeline):
    d, stats = pipeline
    n = duckdb.sql(f"select count(*) from '{d / 'canon.parquet'}'").fetchone()[0]
    assert duckdb.sql(f"select count(*) from '{d / 'std.parquet'}'").fetchone()[0] == n == sum(stats["records"].values())
    cols = [r[0] for r in duckdb.sql(f"describe select * from '{d / 'std.parquet'}'").fetchall()]
    assert "aadhaar" not in cols
    raw = [r[0] for r in duckdb.sql(f"select aadhaar from '{d / 'canon.parquet'}' where aadhaar is not null").fetchall()]
    assert raw
    blob = " ".join(str(v) for row in duckdb.sql(f"select * from '{d / 'std.parquet'}'").fetchall() for v in row)
    assert not any(a in blob for a in raw)
    assert duckdb.sql(f"select count(aadhaar_token) from '{d / 'std.parquet'}'").fetchone()[0] > 0


def test_standardisation_improves_agreement_on_same_person(pipeline):
    d, _ = pipeline
    rows = {r["field"]: r for r in field_report(d / "canon.parquet", d / "std.parquet", d / "syn")}
    for f in ("mobile", "dob", "surname", "name (whole)", "city"):
        assert rows[f]["std_agree"] > rows[f]["raw_agree"], f
