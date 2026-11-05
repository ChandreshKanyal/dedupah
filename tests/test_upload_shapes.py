"""Files as people really save them: another separator, a title above the header, a cover sheet, a stray extra value, midnight on every date.
Each used to load as one column, the wrong sheet, or lose data without a word."""
import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine

from dedupe.mapping.detect import detect_values
from dedupe.workspace.uploads import UploadError, Uploads

HEADER = "id,name,dob,phone\n"
ROWS = "".join(f"{i},Sunita Yadav{i},14-09-1985,90000362{i:02d}\n" for i in range(1, 8))
COLUMNS = ["id", "name", "dob", "phone"]


@pytest.fixture
def up(tmp_path):
    return Uploads(create_engine(f"sqlite:///{tmp_path / 'u.db'}"), tmp_path / "uploads")


def store(up, name, data: bytes):
    raw = up.incoming()
    raw.write_bytes(data)
    return up.store(raw, name, "tester")


@pytest.mark.parametrize("sep", [";", "|", "\t"])
def test_a_csv_uses_the_separator_it_really_has(up, sep):
    r = store(up, "people.csv", (HEADER + ROWS).replace(",", sep).encode())
    assert r["columns"] == COLUMNS and r["rows"] == 7


@pytest.mark.parametrize("title", ["Customer report March 2026\n", "Customer report March 2026,,,\n", "\n\nCustomer report\n"])
def test_lines_above_the_header_are_skipped(up, title):
    r = store(up, "people.csv", (title + HEADER + ROWS).encode())
    assert r["columns"] == COLUMNS and r["rows"] == 7


def test_a_value_beyond_the_header_is_refused_not_dropped(up):
    with pytest.raises(UploadError, match="row 2 has more values than the header"):
        store(up, "people.csv", (HEADER + "1,A B,14-09-1985,9000036201,EXTRA\n").encode())
    assert store(up, "people.csv", (HEADER + "1,A B,14-09-1985,9000036201,\n").encode())["rows"] == 1      # an empty trailing cell is fine


def _sheet_file(tmp_path, cover=True, title=False):
    wb = Workbook()
    ws = wb.active
    if cover:
        ws.title = "Cover"
        ws["A1"] = "Customer extract - confidential"
        ws = wb.create_sheet("Customers")
    if title:
        ws.append(["Customer extract March 2026"])
        ws.append([])
    ws.append(COLUMNS)
    for i in range(1, 8):
        ws.append([i, f"Sunita Yadav{i}", "14-09-1985", 9000036200 + i])
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return path.read_bytes()


def test_the_first_sheet_with_data_is_read_when_a_cover_sheet_comes_first(up, tmp_path):
    r = store(up, "book.xlsx", _sheet_file(tmp_path))
    assert r["columns"] == COLUMNS and r["rows"] == 7
    assert store(up, "book.xlsx", _sheet_file(tmp_path, cover=False, title=True))["columns"] == COLUMNS     # a title row and a blank row above the header


def test_midnight_on_every_date_does_not_turn_birth_dates_into_record_dates():
    dobs = [f"{1960 + i % 40}-0{1 + i % 9}-1{i % 9} 00:00:00" for i in range(30)]
    assert detect_values(dobs)["type"] == "dob"
    stamps = [f"2026-0{1 + i % 9}-1{i % 9} 1{i % 9}:30:15" for i in range(30)]
    assert detect_values(stamps)["type"] == "record_date"


def test_a_quote_that_is_never_closed_is_refused_not_merged(up):
    """Portal review U2-20: an unclosed quote ran every later row into one cell, silently."""
    data = b'id,name,city\n1,Asha,Pune\n2,"Ravi,Delhi\n3,Meena,Chennai\n4,Anil,Jaipur\n'
    with pytest.raises(UploadError, match="never closed"):
        store(up, "q.csv", data)
    ok = store(up, "q2.csv", b'id,name,address\n1,Asha,"12 MG Road\nPune"\n2,Ravi,Delhi\n')        # a quoted cell over two lines is fine
    assert ok["rows"] == 2


def test_a_text_file_saved_as_csv_is_not_taken_for_a_table(up):
    data = b"Dear team, please find the customer list for March attached below\nRegards\nAsha\nFinance\nPune office\n"
    with pytest.raises(UploadError, match="looks like text"):
        store(up, "letter.csv", data)
