"""MongoDB (and MongoDB-compatible services such as Amazon DocumentDB and Azure Cosmos DB for MongoDB), read with pymongo's find().

Settings: either uri_env (an environment variable holding a mongodb:// or mongodb+srv:// connection string, as MongoDB documents it) or host,
port, username, password_env, auth_source and tls; then database, collection, an optional filter and projection (JSON). A filter may not use
operators that run code on the server ($where, $function, $accumulator). The connection uses readPreference=secondaryPreferred so a large read
does not load the primary. Nested documents become dotted columns; ObjectId and dates become text.
"""
import json
import re
from typing import Iterator

import pyarrow as pa

from .base import Connector, register, secret
from .guard import ConnectorConfigError, check_host
from .records import batches_of, columns_of, need

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_CODE_OPERATORS = {"$where", "$function", "$accumulator"}


def _no_code(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _CODE_OPERATORS:
                raise ConnectorConfigError(f"the filter may not use {k}")
            _no_code(v)
    elif isinstance(obj, list):
        for v in obj:
            _no_code(v)


def _json_setting(raw: str | None, what: str) -> dict:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except json.JSONDecodeError:
        raise ConnectorConfigError(f"{what} must be a JSON object, e.g. {{\"status\": \"active\"}}") from None
    if not isinstance(v, dict):
        raise ConnectorConfigError(f"{what} must be a JSON object")
    _no_code(v)
    return v


@register
class MongoConnector(Connector):
    types = ("mongodb",)

    def __init__(self, **config):
        super().__init__(**config)
        for k in ("database", "collection"):
            if not _NAME.match(config.get(k) or ""):
                raise ConnectorConfigError(f"{k} must be a plain name")
        self.filter = _json_setting(config.get("filter"), "filter")
        self.projection = _json_setting(config.get("projection"), "projection") or None
        if not config.get("uri_env"):
            check_host(config.get("host", ""))

    def _client(self):
        pymongo = need("pymongo", "mongodb")
        c = self.config
        opts = {"readPreference": "secondaryPreferred", "serverSelectionTimeoutMS": 15000, "appname": "dedupe"}
        if c.get("uri_env"):
            return pymongo.MongoClient(secret(None, c["uri_env"], "MongoDB connection string"), **opts)
        pw = secret(None, c.get("password_env"), "MongoDB password") if c.get("password_env") else None
        return pymongo.MongoClient(host=c["host"], port=int(c.get("port") or 27017), username=c.get("username") or None, password=pw,
                                   authSource=c.get("auth_source") or "admin", tls=c.get("tls", "yes") != "no", **opts)

    def _records(self):
        client = self._client()
        try:
            cur = client[self.config["database"]][self.config["collection"]].find(self.filter, self.projection, batch_size=5000)
            for doc in cur:
                yield {k: (str(v) if k == "_id" else v) for k, v in doc.items()}
        finally:
            client.close()

    def test_connection(self) -> None:
        self.columns()

    def columns(self) -> list[str]:
        return columns_of(self._records())

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        yield from batches_of(self._records(), batch_size, self.columns())
