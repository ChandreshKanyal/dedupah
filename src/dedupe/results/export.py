"""Taking results out of a run: files (Excel, CSV, Parquet) and database tables.

The datasets are read from the run's own files (decrypted when a data key is set) and cleaned for a person: the stable person id comes first, lists
become "a; b" text. This is personal data leaving the platform, so the callers (the API) require a permission and a reason and write an audit row
(dataset, format, row count: never a value).

Files for spreadsheets are made safe against formula injection (a cell starting with = + - @ would otherwise run as a formula when opened in
Excel): Excel cells are stored as text, CSV values get a leading apostrophe unless `safe_for_spreadsheets` is turned off for machine use.

Database writes happen in ONE transaction: a refused row, a wrong column or a lost connection leaves the target table exactly as it was.
"""
import csv
import os
from pathlib import Path

import pyarrow as pa
from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, MetaData, Table, Text, create_engine, delete, inspect

from dedupe import crypto

from .config import identifier_ok

EXCEL_MAX_ROWS = 1_048_575
BATCH = 20_000
DB_BATCH = 5_000
TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

DATASETS = {
    "golden_records": {"title": "Golden records", "file": "golden/golden_records.parquet",
                       "description": "One row per person: the best value of every field, with how many source records it was built from."},
    "person_records": {"title": "Person-to-record map", "file": "clusters/clusters.parquet", "exclude": ("rid",),
                       "description": "Which source record belongs to which person. Use it to carry the person id back into your own systems."},
    "households": {"title": "Households", "file": "households/household_members.parquet",
                   "description": "Which household each person belongs to."},
}


class ExportError(ValueError):
    """A request the export cannot satisfy; the message is a fixed sentence, safe to show."""


def _path(run_dir: Path, name: str) -> Path:
    spec = DATASETS.get(name)
    if spec is None:
        raise ExportError("unknown dataset")
    p = Path(run_dir) / spec["file"]
    if not p.exists():
        raise ExportError("this run has no such results")
    return p


def _select(con, path: Path, name: str) -> str:
    """SQL that reads the dataset with person ids first, lists as text and unwanted columns removed."""
    spec = DATASETS[name]
    cols = con.execute(f"DESCRIBE SELECT * FROM {crypto.rp(path)}").fetchall()
    parts = []
    for col, typ, *_ in cols:
        if col in spec.get("exclude", ()):
            continue
        q = '"' + col.replace('"', '""') + '"'
        expr = f"array_to_string({q}, '; ')" if str(typ).endswith("[]") else q
        alias = "person_id" if col == "cluster_id" else col
        parts.append((alias, f'{expr} AS "{alias}"'))
    parts.sort(key=lambda x: x[0] != "person_id")                   # the person id leads
    return f"SELECT {', '.join(p[1] for p in parts)} FROM {crypto.rp(path)}"


def describe(run_dir: Path) -> list[dict]:
    """The datasets this run can give, with row counts and column names."""
    out = []
    con = crypto.connect()
    try:
        for name, spec in DATASETS.items():
            p = Path(run_dir) / spec["file"]
            if not p.exists():
                continue
            sql = _select(con, p, name)
            rows = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
            cols = [c[0] for c in con.execute(f"DESCRIBE {sql}").fetchall()]
            out.append({"name": name, "title": spec["title"], "description": spec["description"], "rows": rows, "columns": cols})
    finally:
        con.close()
    return out


def _batches(run_dir: Path, name: str, size: int = BATCH):
    """(schema, iterator of Arrow tables) of the dataset, in order."""
    p = _path(run_dir, name)
    con = crypto.connect()
    sql = _select(con, p, name)
    reader = con.execute(sql).fetch_record_batch(size)

    def gen():
        try:
            for b in reader:
                yield pa.Table.from_batches([b])
        finally:
            con.close()
    return reader.schema, gen()


def _csv_cell(v, safe: bool):
    if v is None:
        return ""
    if safe and isinstance(v, str) and v.startswith(TRIGGERS):
        return "'" + v
    return v


def write_csv(run_dir: Path, name: str, out: Path, safe_for_spreadsheets: bool = True) -> int:
    schema, batches = _batches(run_dir, name)
    rows = 0
    with open(out, "w", encoding="utf-8-sig", newline="") as f:                 # with a byte-order mark so Excel reads the text as UTF-8
        w = csv.writer(f)
        w.writerow(schema.names)
        for t in batches:
            cols = [t.column(i).to_pylist() for i in range(t.num_columns)]
            for r in zip(*cols, strict=True):
                w.writerow([_csv_cell(v, safe_for_spreadsheets) for v in r])
            rows += t.num_rows
    return rows


def write_parquet(run_dir: Path, name: str, out: Path) -> int:
    import pyarrow.parquet as pq
    schema, batches = _batches(run_dir, name)
    rows = 0
    with pq.ParquetWriter(out, schema) as w:                                     # leaving the platform: written without the data key
        for t in batches:
            w.write_table(t)
            rows += t.num_rows
    return rows


def write_xlsx(run_dir: Path, name: str, out: Path) -> int:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    schema, batches = _batches(run_dir, name)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(DATASETS[name]["title"][:31])
    ws.append(list(schema.names))
    rows = 0
    for t in batches:
        rows += t.num_rows
        if rows > EXCEL_MAX_ROWS:
            raise ExportError(f"Excel holds at most {EXCEL_MAX_ROWS:,} rows: choose CSV, Parquet or a database for this dataset")
        cols = [t.column(i).to_pylist() for i in range(t.num_columns)]
        for r in zip(*cols, strict=True):
            row = []
            for v in r:
                if isinstance(v, str) and v.startswith(TRIGGERS):
                    c = WriteOnlyCell(ws, value=v)
                    c.data_type = "s"                                            # stored as text: never evaluated as a formula
                    row.append(c)
                else:
                    row.append(v)
            ws.append(row)
    wb.save(out)
    return rows


FORMATS = {"xlsx": ("Excel", ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
           "csv": ("CSV", ".csv", "text/csv; charset=utf-8"), "parquet": ("Parquet", ".parquet", "application/octet-stream")}


def write_file(run_dir: Path, name: str, fmt: str, out: Path, safe_for_spreadsheets: bool = True) -> int:
    if fmt == "xlsx":
        return write_xlsx(run_dir, name, out)
    if fmt == "csv":
        return write_csv(run_dir, name, out, safe_for_spreadsheets)
    if fmt == "parquet":
        return write_parquet(run_dir, name, out)
    raise ExportError("unknown format")


# ---- databases ---------------------------------------------------------------------------------------------------------------------
def _sa_type(t: pa.DataType):
    if pa.types.is_boolean(t):
        return Boolean
    if pa.types.is_integer(t):
        return BigInteger
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return Float
    if pa.types.is_date(t):
        return Date
    if pa.types.is_timestamp(t):
        return DateTime
    return Text


def database_url(url_env: str) -> str:
    url = os.environ.get(url_env, "")
    if not url:
        raise ExportError("the destination's environment variable is not set on the server")
    return url


MODES = ("create", "append", "replace")


def write_database(url: str, run_dir: Path, name: str, table: str, mode: str, schema: str | None = None, confirm_replace: bool = False) -> dict:
    """Write a dataset into a table. `create` makes a new table (refused if it exists), `append` adds rows to an existing table, `replace` empties an
    existing table first (needs `confirm_replace`). All of it is one transaction. Returns {"table", "schema", "mode", "rows"}."""
    if mode not in MODES:
        raise ExportError("mode must be create, append or replace")
    if not identifier_ok(table) or (schema is not None and not identifier_ok(schema)):
        raise ExportError("table and schema names are letters, digits and _, not starting with a digit")
    if mode == "replace" and not confirm_replace:
        raise ExportError("replacing a table deletes its current rows: confirm it first")
    arrow_schema, batches = _batches(run_dir, name, DB_BATCH)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            exists = inspect(conn).has_table(table, schema=schema)
            if mode == "create":
                if exists:
                    raise ExportError("that table already exists: choose another name, or add to it or replace it")
                target = Table(table, MetaData(schema=schema), *[Column(f.name, _sa_type(f.type)) for f in arrow_schema])
                target.create(conn)
            else:
                if not exists:
                    raise ExportError("that table does not exist yet: create it first")
                target = Table(table, MetaData(schema=schema), autoload_with=conn)
                missing = [f.name for f in arrow_schema if f.name not in target.columns]
                if missing:
                    raise ExportError("the table has no column for: " + ", ".join(missing[:8]))
                if mode == "replace":
                    conn.execute(delete(target))
            rows = 0
            for t in batches:
                data = t.to_pylist()
                if data:
                    conn.execute(target.insert(), data)
                rows += len(data)
    except ExportError:
        raise
    except Exception as e:  # noqa: BLE001 - a driver error can quote a value or the connection URL: report only its kind
        raise ExportError(f"the database refused the request ({type(e).__name__}); nothing was written") from None
    finally:
        engine.dispose()
    return {"table": table, "schema": schema, "mode": mode, "rows": rows}


def test_destination(url: str, schema: str | None = None) -> None:
    """Can the database be reached? Raises ExportError with a fixed sentence otherwise."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
            if schema is not None and schema not in inspect(conn).get_schema_names():
                raise ExportError("the schema does not exist in that database")
    except ExportError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ExportError(f"the database could not be reached ({type(e).__name__})") from None
    finally:
        engine.dispose()


__all__ = ["DATASETS", "ExportError", "FORMATS", "MODES", "describe", "write_file", "write_database", "database_url", "test_destination", "EXCEL_MAX_ROWS"]
