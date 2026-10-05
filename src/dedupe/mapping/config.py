"""Mapping config -> DuckDB SQL.

A field expression is one of:
    "col"                                         a source column
    {"coalesce": ["a", "b"]}                      first non-empty
    {"concat": ["a", "b"], "sep": ", "}           join non-empty parts
    {"column": "c", "when": {"doc_type": "PAN"}}  value only when other columns equal given values
    {"const": "x"}                                a constant
"""
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from .canonical import CANONICAL_FIELDS, OUTPUT_COLUMNS


class MappingError(ValueError):
    pass


class SourceConfig(BaseModel):
    source_id: str
    connector: dict
    record_id: str | None = None  # column holding the source's own primary key; row number if absent
    fields: dict[str, Any] = {}
    attributes: list[str] = []  # source-specific columns carried along untouched as JSON

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, v):
        unknown = set(v) - set(CANONICAL_FIELDS)
        if unknown:
            raise ValueError(f"unknown canonical fields: {sorted(unknown)}; valid: {sorted(CANONICAL_FIELDS)}")
        return v


class IngestConfig(BaseModel):
    sources: list[SourceConfig]

    @classmethod
    def load(cls, path: str | Path) -> "IngestConfig":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _col(name: str) -> str:
    return f"NULLIF(TRIM(CAST({_q(name)} AS VARCHAR)), '')"


def _expr(e, used: set[str]) -> str:
    if isinstance(e, str):
        used.add(e)
        return _col(e)
    if not isinstance(e, dict):
        raise MappingError(f"bad field expression: {e!r}")
    if "const" in e:
        return _lit(e["const"])
    if "coalesce" in e:
        used.update(e["coalesce"])
        return "COALESCE(" + ", ".join(_col(c) for c in e["coalesce"]) + ")"
    if "concat" in e:
        used.update(e["concat"])
        return f"NULLIF(concat_ws({_lit(e.get('sep', ' '))}, " + ", ".join(_col(c) for c in e["concat"]) + "), '')"
    if "column" in e:
        used.add(e["column"])
        sql = _col(e["column"])
        if e.get("when"):
            used.update(e["when"])
            cond = " AND ".join(f"CAST({_q(c)} AS VARCHAR) = {_lit(v)}" for c, v in e["when"].items())
            sql = f"CASE WHEN {cond} THEN {sql} END"
        return sql
    raise MappingError(f"bad field expression: {e!r}")


def compile_select(cfg: SourceConfig, source_columns: list[str], table: str = "batch", row_offset: int = 0) -> str:
    """SQL turning a batch of raw source rows into canonical rows. Raises MappingError on missing columns."""
    used: set[str] = set(cfg.attributes)
    if cfg.record_id:
        used.add(cfg.record_id)
    parts = {"source_id": f"{_lit(cfg.source_id)}"}
    parts["source_record_id"] = (_col(cfg.record_id) if cfg.record_id
                                 else f"CAST(row_number() OVER () + {row_offset} AS VARCHAR)")
    for name in CANONICAL_FIELDS:
        parts[name] = _expr(cfg.fields[name], used) if name in cfg.fields else "NULL"
    if cfg.attributes:
        pairs = ", ".join(f"{_lit(a)}, {_col(a)}" for a in cfg.attributes)
        parts["attributes"] = f"CAST(json_object({pairs}) AS VARCHAR)"
    else:
        parts["attributes"] = "NULL"
    missing = sorted(used - set(source_columns))
    if missing:
        raise MappingError(f"source {cfg.source_id!r}: mapped columns not found in source: {missing}. "
                           f"Available: {source_columns}")
    select = ", ".join(f"CAST({parts[c]} AS VARCHAR) AS {_q(c)}" for c in OUTPUT_COLUMNS)
    return f"SELECT {select} FROM {table}"
