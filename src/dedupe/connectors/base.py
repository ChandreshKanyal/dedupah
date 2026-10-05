"""Connector plugin interface. A new data source = one subclass + @register. The core never changes."""
import os
from abc import ABC, abstractmethod
from typing import ClassVar, Iterator

import pyarrow as pa

REGISTRY: dict[str, type["Connector"]] = {}


def register(cls: type["Connector"]) -> type["Connector"]:
    for t in cls.types:
        REGISTRY[t] = cls
    return cls


def create(config: dict) -> "Connector":
    """Build a connector from a config dict like {"type": "csv", "path": "..."}."""
    config = dict(config)
    ctype = config.pop("type", None)
    if ctype not in REGISTRY:
        raise ValueError(f"Unknown connector type {ctype!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[ctype](type=ctype, **config)


def secret(config_value: str | None, env_name: str | None, what: str) -> str | None:
    """Credentials come from environment variables, never from committed config files."""
    if env_name:
        v = os.environ.get(env_name)
        if not v:
            raise ValueError(f"Environment variable {env_name} (for {what}) is not set")
        return v
    return config_value


def to_string_table(columns: list[str], rows: list[list]) -> pa.Table:
    """All connectors emit string columns: typing and cleaning is the standardise stage's job."""
    cols = list(zip(*rows, strict=True)) if rows else [()] * len(columns)
    return pa.table({name: pa.array([None if v is None else str(v) for v in col], type=pa.string())
                     for name, col in zip(columns, cols, strict=True)})


class Connector(ABC):
    types: ClassVar[tuple[str, ...]] = ()

    def __init__(self, **config):
        self.config = config

    @abstractmethod
    def test_connection(self) -> None:
        """Raise if the source cannot be reached/read."""

    @abstractmethod
    def columns(self) -> list[str]:
        """Discover the source's column names."""

    @abstractmethod
    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        """Stream the source as Arrow tables of string columns."""

    def sample(self, n: int = 200) -> pa.Table:
        for batch in self.read_batches(batch_size=n):
            return batch.slice(0, n)
        return pa.table({c: pa.array([], type=pa.string()) for c in self.columns()})
