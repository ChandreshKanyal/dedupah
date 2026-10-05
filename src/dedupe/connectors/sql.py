from typing import Iterator

import pyarrow as pa
from sqlalchemy import create_engine, select, table, text

from .base import Connector, register, secret, to_string_table


@register
class SqlConnector(Connector):
    """Any SQLAlchemy-supported database: Postgres, MySQL, AWS RDS/Aurora, SQL Server, Oracle, Snowflake, Redshift, BigQuery...

    Config: {"type": "sql", "url_env": "MY_DB_URL", "table": "customers", "schema": "public"}
        or  {"type": "sql", "url_env": "MY_DB_URL", "query": "SELECT ... FROM ..."}
    The URL (which holds the password) is read from an environment variable. Use a read-only database user.
    """
    types = ("sql", "postgres", "mysql", "rds")

    def __init__(self, **config):
        super().__init__(**config)
        url = secret(config.get("url"), config.get("url_env"), "database URL")
        if not url:
            raise ValueError("SQL connector needs url_env (preferred) or url")
        if bool(config.get("table")) == bool(config.get("query")):
            raise ValueError("SQL connector needs exactly one of: table, query")
        if config.get("query") and not config["query"].lstrip().lower().startswith(("select", "with")):
            raise ValueError("Only SELECT queries are allowed")
        self.engine = create_engine(url)

    def _statement(self):
        if self.config.get("query"):
            return text(self.config["query"])
        return select(text("*")).select_from(table(self.config["table"], schema=self.config.get("schema")))

    def test_connection(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        self.columns()

    def columns(self) -> list[str]:
        with self.engine.connect() as conn:
            if self.config.get("query"):  # wrap so the database does not run the full query just to name columns
                stmt = text(f"SELECT * FROM ({self.config['query'].rstrip().rstrip(';')}) AS _q LIMIT 0")
            else:
                stmt = self._statement().limit(0)
            res = conn.execute(stmt)
            keys = list(res.keys())
            res.close()
            return keys

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        with self.engine.connect().execution_options(stream_results=True) as conn:
            res = conn.execute(self._statement())
            keys = list(res.keys())
            while True:
                rows = res.fetchmany(batch_size)
                if not rows:
                    break
                yield to_string_table(keys, [list(r) for r in rows])
