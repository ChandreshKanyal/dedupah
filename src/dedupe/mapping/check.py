"""Checks for source configurations that come from a person (web screen / API) and so are untrusted.

A source is accepted only when: its connector settings are on a short allow-list for that connector type (no literal passwords, urls or tokens:
only the NAME of an environment variable, and only names starting with DEDUPE_SRC_, so a typed name can never point at one of our own secrets);
the connector can be built (file confined to DEDUPE_DATA_ROOTS, SELECT-only query, ...) and reached; every mapped column exists in the source;
the mapping compiles. Error messages are fixed sentences plus names of columns/fields: they never repeat a data value, a path or a credential.
"""
import re

import duckdb
import pyarrow as pa

from dedupe.connectors import create
from dedupe.connectors.guard import ConnectorConfigError

from .canonical import CANONICAL_FIELDS
from .config import IngestConfig, MappingError, SourceConfig, compile_select
from .suggest import suggest

ENV_PREFIX = "DEDUPE_SRC_"
_ENV_NAME = re.compile(r"^DEDUPE_SRC_[A-Z0-9_]{1,60}$")
_FILE = {"path", "type"}
ALLOWED_SETTINGS = {
    "csv": _FILE | {"delimiter", "encoding"}, "tsv": _FILE | {"delimiter", "encoding"}, "xlsx": _FILE | {"sheet"}, "excel": _FILE | {"sheet"},
    "parquet": _FILE, "gsheet": {"type", "spreadsheet_id", "gid", "token_env"}, "google_sheets": {"type", "spreadsheet_id", "gid", "token_env"},
    **{t: {"type", "url_env", "table", "schema", "query"} for t in ("sql", "postgres", "mysql", "rds")},
}
SETTING_HELP = {
    "csv": "path (a file inside an allowed data folder), delimiter, encoding", "tsv": "path, delimiter, encoding", "xlsx": "path, sheet",
    "parquet": "path", "gsheet": f"spreadsheet_id, gid, token_env (an environment variable name starting with {ENV_PREFIX})",
    "sql": f"url_env (an environment variable name starting with {ENV_PREFIX} that holds the database URL), and one of table (+ schema) or query (one SELECT)",
}
_TEXT_LIMIT = 500


class SourceProblem(ValueError):
    pass


def check_connector_settings(conf: dict) -> None:
    ctype = conf.get("type")
    if ctype not in ALLOWED_SETTINGS:
        raise SourceProblem("unknown connector type; choose one of: " + ", ".join(sorted(SETTING_HELP)))
    extra = sorted(set(conf) - ALLOWED_SETTINGS[ctype])
    if extra:
        raise SourceProblem(f"these settings are not allowed for a {ctype} source: {extra}. Allowed: {SETTING_HELP.get(ctype, '')}. "
                            "Passwords and tokens are never stored here: put them in an environment variable and give its name")
    for k in ("url_env", "token_env"):
        if k in conf and not _ENV_NAME.match(str(conf[k])):
            raise SourceProblem(f"{k} must be an environment variable name starting with {ENV_PREFIX} (capital letters, digits, underscore)")
    for k, v in conf.items():
        if not isinstance(v, str) or len(v) > _TEXT_LIMIT or "\x00" in v:
            raise SourceProblem(f"setting {k!r} must be short text")


def _open(conf: dict):
    """Build the connector with the safety checks applied; explain failures without echoing paths, URLs or values."""
    check_connector_settings(conf)
    try:
        conn = create(conf)
        conn.test_connection()
        return conn
    except SourceProblem:
        raise
    except FileNotFoundError:
        raise SourceProblem("the file was not found") from None
    except ConnectorConfigError as e:
        raise SourceProblem(str(e)[:200]) from None          # our own fixed sentences (outside the allowed folders, not a SELECT, ...)
    except ValueError as e:
        raise SourceProblem("the source settings are incomplete or not valid" + (f" ({str(e)[:120]})" if "Environment variable" in str(e) else "")) from None
    except Exception as e:  # noqa: BLE001 - a driver error text can contain a host, user or value: report only its kind
        raise SourceProblem(f"the source could not be opened ({type(e).__name__})") from None


def inspect_source(conf: dict, sample_rows: int = 200) -> dict:
    """Columns, a sample (kept in memory, never returned as is) and suggested field mappings for a connector setting."""
    conn = _open(conf)
    try:
        cols = [str(c) for c in conn.columns()]
        sample = conn.sample(sample_rows)
    except Exception as e:  # noqa: BLE001
        raise SourceProblem(f"the source could be reached but not read ({type(e).__name__})") from None
    return {"columns": cols, "sample": sample, "suggestions": suggest(sample) if sample.num_rows else []}


def check_source(src: SourceConfig, sample_rows: int = 200) -> pa.Table:
    """Validate one whole source; returns its sample turned into canonical rows (for the masked preview)."""
    info = inspect_source(src.connector, sample_rows)
    try:
        sql = compile_select(src, info["columns"])
    except MappingError as e:
        raise SourceProblem(str(e).split(". Available")[0][:400]) from None
    con = duckdb.connect()
    try:
        con.register("batch", info["sample"])
        return con.execute(sql).to_arrow_table()
    except duckdb.Error as e:
        raise SourceProblem(f"the mapping could not be applied ({type(e).__name__})") from None
    finally:
        con.close()


def check_sources(content: dict) -> list[dict]:
    """[] when every source is fine, else [{source_id, problem}] (the whole content is checked, not just the first failure)."""
    try:
        cfg = IngestConfig.model_validate(content)
    except ValueError as e:
        errs = getattr(e, "errors", lambda: [])()
        where = ", ".join(sorted({".".join(str(x) for x in err["loc"]) for err in errs})[:10]) or "the content"
        return [{"source_id": "", "problem": f"the configuration is not valid at: {where}"}]
    problems, seen = [], set()
    if not cfg.sources:
        problems.append({"source_id": "", "problem": "at least one source is needed"})
    for s in cfg.sources:
        if s.source_id in seen:
            problems.append({"source_id": s.source_id, "problem": "this source id is used twice"})
            continue
        seen.add(s.source_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s.source_id):
            problems.append({"source_id": s.source_id, "problem": "a source id is 1-40 letters, digits, - or _"})
            continue
        if not s.fields.get("full_name") and not s.fields.get("pan") and not s.fields.get("mobile"):
            problems.append({"source_id": s.source_id, "problem": "map at least a name, PAN or mobile: without one of them no record can be matched"})
        try:
            check_source(s)
        except SourceProblem as e:
            problems.append({"source_id": s.source_id, "problem": str(e)})
    return problems


__all__ = ["check_sources", "check_source", "inspect_source", "check_connector_settings", "SourceProblem", "ENV_PREFIX", "CANONICAL_FIELDS", "SETTING_HELP"]
