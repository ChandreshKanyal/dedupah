"""Deliberately naive matcher: records sharing an exact PAN or mobile number are the same person.

It exists only to give the first benchmark number to beat (and to show why shared family mobiles hurt).
"""
import csv
import re
from pathlib import Path

from .loaders import record_key


def _mobile(v):
    d = re.sub(r"\D", "", v or "")
    return d[-10:] if len(d) >= 10 else None


def _pan(v):
    v = (v or "").strip().upper()
    return v if len(v) == 10 else None


def _csv_rows(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _xlsx_rows(path: Path):
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True).active
    it = ws.values
    header = next(it)
    for row in it:
        yield {h: ("" if v is None else str(v)) for h, v in zip(header, row)}


def iter_records(data_dir: Path):
    """Yield (record_key, pan, mobile) for every source record."""
    for r in _csv_rows(data_dir / "bank_crm.csv"):
        yield record_key("bank_crm", r["cust_id"]), _pan(r["pan"]), _mobile(r["mobile"])
    for r in _xlsx_rows(data_dir / "insurance_policies.xlsx"):
        yield record_key("insurance", r["row_id"]), _pan(r["pan"]), _mobile(r["mobile"])
    for r in _csv_rows(data_dir / "loans.csv"):
        yield record_key("loans", r["loan_id"]), _pan(r["pan"]), _mobile(r["mobile"])
    for r in _csv_rows(data_dir / "marketing_leads.csv"):
        yield record_key("leads", r["lead_id"]), None, _mobile(r["phone"])
    for r in _csv_rows(data_dir / "kyc_documents.csv"):
        pan = _pan(r["doc_number"]) if r["doc_type"] == "PAN" else None
        yield record_key("kyc_documents", r["doc_id"]), pan, None


def run(data_dir: Path) -> dict:
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    keys = []
    for key, pan, mob in iter_records(data_dir):
        keys.append(key)
        find(key)
        if pan:
            union(key, f"pan:{pan}")
        if mob:
            union(key, f"mob:{mob}")
    return {k: find(k) for k in keys}
