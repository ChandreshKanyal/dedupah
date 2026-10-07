"""Shared helpers for sources that deliver records (JSON, XML, APIs, MongoDB) rather than tables, and for drivers that may not be installed.

Nested objects become dotted column names ("address.city"); lists of plain values are joined with " | "; lists of objects are kept as JSON text,
because a person's record rarely needs them for matching and a fixed set of columns is what the mapping step works with. The column set is
taken from the first records read (DISCOVER records), and every later batch is conformed to it, so the mapping never meets a surprise column.
"""
import importlib
import json
from typing import Iterable, Iterator

import pyarrow as pa

from .base import to_string_table

DISCOVER = 1000
MAX_COLUMNS = 500


def flatten(obj, prefix: str = "", out: dict | None = None, depth: int = 0) -> dict:
    out = {} if out is None else out
    if isinstance(obj, dict) and depth < 8:
        for k, v in obj.items():
            flatten(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(obj, list):
        if all(not isinstance(v, (dict, list)) for v in obj):
            out[prefix] = " | ".join("" if v is None else str(v) for v in obj) or None
        else:
            out[prefix] = json.dumps(obj, default=str, ensure_ascii=False)
    elif isinstance(obj, dict):
        out[prefix] = json.dumps(obj, default=str, ensure_ascii=False)
    else:
        out[prefix or "value"] = obj
    return out


def discover_columns(records: list[dict]) -> list[str]:
    cols: dict[str, None] = {}
    for r in records:
        for k in r:
            cols.setdefault(k, None)
    if len(cols) > MAX_COLUMNS:
        raise ValueError(f"the records have more than {MAX_COLUMNS} different fields")
    return list(cols)


def batches_of(records: Iterable[dict], batch_size: int, columns: list[str] | None = None) -> Iterator[pa.Table]:
    """Flattened records as string tables with one fixed column list (discovered from the first records unless given)."""
    it = iter(records)
    head: list[dict] = []
    if columns is None:
        for r in it:
            head.append(flatten(r))
            if len(head) >= DISCOVER:
                break
        columns = discover_columns(head)
    buf = [[r.get(c) for c in columns] for r in head]
    sent = False
    while len(buf) >= batch_size:
        yield to_string_table(columns, buf[:batch_size])
        buf, sent = buf[batch_size:], True
    for r in it:
        f = flatten(r)
        buf.append([f.get(c) for c in columns])
        if len(buf) >= batch_size:
            yield to_string_table(columns, buf)
            buf, sent = [], True
    if buf or not sent:
        yield to_string_table(columns, buf)


def columns_of(records: Iterable[dict]) -> list[str]:
    head = []
    for r in records:
        head.append(flatten(r))
        if len(head) >= DISCOVER:
            break
    return discover_columns(head)


class DriverMissing(RuntimeError):
    """A connector whose Python driver is not installed on this server."""


def need(module: str, extra: str):
    """Import a driver module or explain which optional package to install."""
    try:
        return importlib.import_module(module)
    except ImportError:
        raise DriverMissing(f"the driver for this source is not installed on the server (ask the administrator to install: pip install \"dedupe[{extra}]\")") from None


def available(module: str | None) -> bool:
    if not module:
        return True
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        return False
