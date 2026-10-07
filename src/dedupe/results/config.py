"""Where results may be written: a project's saved database destinations.

A destination is the NAME of an environment variable (it must start with DEDUPE_DST_) that holds a database URL, plus an optional default schema.
The URL itself, with its password, is never stored or typed into a screen: the operator who runs the server sets the variable, and the project
owner points the project at it. A destination is versioned and approved like a connection (when the project asks for a second person), because
letting a project write personal data into a database is the most sensitive thing a project can be allowed to do.
"""
import re

from pydantic import BaseModel, ConfigDict, field_validator

ENV_PREFIX = "DEDUPE_DST_"
_ENV = re.compile(r"^DEDUPE_DST_[A-Z0-9_]{1,60}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
DESTINATION_ID = r"^[A-Za-z0-9_-]{1,40}$"


class DestinationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_id: str
    url_env: str
    schema_name: str | None = None            # the default schema (namespace) tables are written to
    description: str = ""

    @field_validator("destination_id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.fullmatch(DESTINATION_ID, v):
            raise ValueError("a destination id is 1-40 letters, digits, - or _")
        return v

    @field_validator("url_env")
    @classmethod
    def _env(cls, v: str) -> str:
        if not _ENV.match(v):
            raise ValueError(f"url_env must be an environment variable name starting with {ENV_PREFIX} (capital letters, digits, underscore)")
        return v

    @field_validator("schema_name")
    @classmethod
    def _schema(cls, v: str | None) -> str | None:
        if v is not None and not _IDENT.match(v):
            raise ValueError("a schema name is letters, digits and _, not starting with a digit")
        return v

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        return v[:300]


def identifier_ok(name: str) -> bool:
    return bool(_IDENT.match(name or ""))


__all__ = ["DestinationConfig", "ENV_PREFIX", "DESTINATION_ID", "identifier_ok"]
