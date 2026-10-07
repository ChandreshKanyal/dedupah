"""Databases and data warehouses, through SQLAlchemy.

Two ways to configure one:
- form settings (host, port, database, user, and the NAME of an environment variable holding the password): the URL is built here with
  `sqlalchemy.engine.URL.create`, which escapes every part, following each driver's documented URL format (see ENGINES);
- `url_env`: an environment variable holding a complete SQLAlchemy URL, for anything the form does not cover.

Reading is SELECT-only (checked here) and should ALSO use a read-only database user. PostgreSQL connections are opened read-only.
"""
import os
from pathlib import Path
from typing import Iterator

import pyarrow as pa
from sqlalchemy import create_engine, select, table, text
from sqlalchemy.engine import URL

from .base import Connector, register, secret, to_string_table
from .files import _as_text
from .guard import ConnectorConfigError, check_host, check_identifier, check_path, check_select_only, resolve_checked
from .records import need

# engine -> (SQLAlchemy driver name, driver module to import, optional-dependency name, default port)
ENGINES: dict[str, tuple[str, str, str, int | None]] = {
    "postgres": ("postgresql+psycopg2", "psycopg2", "postgres", 5432),           # docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg2
    "mysql": ("mysql+pymysql", "pymysql", "mysql", 3306),                         # .../dialects/mysql.html#module-sqlalchemy.dialects.mysql.pymysql
    "mariadb": ("mariadb+pymysql", "pymysql", "mysql", 3306),                     # SQLAlchemy's "mariadb" dialect name over PyMySQL
    "sqlserver": ("mssql+pyodbc", "pyodbc", "sqlserver", 1433),                   # .../dialects/mssql.html#module-sqlalchemy.dialects.mssql.pyodbc
    "sqlserver_pymssql": ("mssql+pymssql", "pymssql", "sqlserver", 1433),
    "oracle": ("oracle+oracledb", "oracledb", "oracle", 1521),                    # .../dialects/oracle.html#module-sqlalchemy.dialects.oracle.oracledb (thin mode)
    "db2": ("db2+ibm_db", "ibm_db_sa", "db2", 50000),                             # github.com/ibmdb/python-ibmdbsa
    "redshift": ("redshift+redshift_connector", "sqlalchemy_redshift", "redshift", 5439),   # github.com/sqlalchemy-redshift/sqlalchemy-redshift
    "snowflake": ("snowflake", "snowflake.sqlalchemy", "snowflake", None),        # docs.snowflake.com/en/developer-guide/python-connector/sqlalchemy
    "bigquery": ("bigquery", "sqlalchemy_bigquery", "bigquery", None),            # googleapis.dev/python/sqlalchemy-bigquery
    "databricks": ("databricks", "databricks.sqlalchemy", "databricks", 443),     # docs.databricks.com/en/dev-tools/sqlalchemy.html
}
RDS_ENGINES = {"postgres", "mysql", "mariadb", "sqlserver", "oracle"}
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")      # libpq sslmode values


def _production() -> bool:
    return os.environ.get("DEDUPE_ENV", "dev").strip().lower() == "production"


def build_url(engine: str, c: dict) -> tuple[URL | str, dict]:
    """The SQLAlchemy URL and create_engine keyword arguments for form settings. Secrets are read from the environment variables named in
    the settings. Exposed for tests: it never connects."""
    if engine not in ENGINES:
        raise ConnectorConfigError("unknown database engine")
    drivername, _, _, default_port = ENGINES[engine]
    pw = secret(None, c.get("password_env"), "database password") if c.get("password_env") else None
    user = c.get("username") or None
    port = int(c["port"]) if c.get("port") else default_port
    query: dict[str, str] = {}
    kwargs: dict = {}
    if engine in ("snowflake",):
        account = c.get("account") or ""
        if not account or not all(ch.isalnum() or ch in "-_." for ch in account):
            raise ConnectorConfigError("account must be the Snowflake account identifier, e.g. myorg-myaccount")
        db = "/".join(x for x in (c.get("database"), c.get("schema")) if x)
        for k in ("warehouse", "role"):
            if c.get(k):
                query[k] = check_identifier(c[k], k)
        return URL.create(drivername, username=user, password=pw, host=account, database=db or None, query=query), kwargs
    if engine == "bigquery":
        project = c.get("project") or ""
        if not project or not all(ch.isalnum() or ch in "-_.:" for ch in project):
            raise ConnectorConfigError("project must be the Google Cloud project id")
        cred = secret(None, c.get("credentials_env"), "service account key file") if c.get("credentials_env") else None
        if cred:
            kwargs["credentials_path"] = str(check_path(Path(cred)))
        return URL.create(drivername, host=project, database=c.get("dataset") or None), kwargs
    if engine == "databricks":
        host = check_host(c.get("host", ""))
        token = secret(None, c.get("token_env"), "Databricks access token")
        if not c.get("http_path", "").startswith("/sql/"):
            raise ConnectorConfigError("http_path is the SQL warehouse's HTTP path, starting with /sql/")
        query["http_path"] = c["http_path"]
        for k in ("catalog", "schema"):
            if c.get(k):
                query[k] = check_identifier(c[k], k)
        return URL.create(drivername, username="token", password=token, host=host, port=port, query=query), kwargs
    host = check_host(c.get("host", ""))
    database = c.get("database") or None
    if engine == "postgres" or engine == "redshift":
        mode = c.get("ssl_mode") or ("verify-full" if _production() else "prefer")
        if mode not in SSL_MODES:
            raise ConnectorConfigError("ssl_mode must be one of " + ", ".join(SSL_MODES))
        query["sslmode"] = mode
        if engine == "postgres":                 # libpq connects to hostaddr and still uses host for the certificate check
            try:
                query["hostaddr"] = resolve_checked(host)[0]
            except ConnectorConfigError as e:
                if "could not be resolved" not in str(e):
                    raise                         # a metadata address is refused; an unknown name is reported by the driver itself
        if engine == "redshift":                       # redshift_connector always uses TLS; it accepts only the two verifying modes
            query = {"sslmode": mode} if mode in ("verify-ca", "verify-full") else {}
        elif c.get("ssl_ca_env"):
            query["sslrootcert"] = str(check_path(Path(secret(None, c["ssl_ca_env"], "CA certificate file"))))
    elif engine in ("mysql", "mariadb"):
        query["charset"] = "utf8mb4"
        if c.get("ssl_ca_env"):
            query["ssl_ca"] = str(check_path(Path(secret(None, c["ssl_ca_env"], "CA certificate file"))))
    elif engine == "sqlserver":
        query["driver"] = c.get("odbc_driver") or "ODBC Driver 18 for SQL Server"
        query["Encrypt"] = "yes"
        query["TrustServerCertificate"] = "yes" if c.get("trust_server_certificate") == "yes" else "no"
    elif engine == "oracle":
        if c.get("service_name"):
            query["service_name"] = c["service_name"]
        elif not database:
            raise ConnectorConfigError("give the Oracle service name (or a SID as database)")
    return URL.create(drivername, username=user, password=pw, host=host, port=port, database=database, query=query), kwargs


class _Database(Connector):
    """Shared reading: one table (with an optional schema) or one SELECT query, streamed in batches."""

    def _setup(self, url, kwargs: dict, driver_module: str | None, extra: str | None):
        if bool(self.config.get("table")) == bool(self.config.get("query")):
            raise ValueError("a database source needs exactly one of: table, query")
        if self.config.get("query"):
            check_select_only(self.config["query"])
        else:
            check_identifier(self.config["table"], "table")
            if self.config.get("schema") and self.config.get("type") not in ("snowflake",):
                check_identifier(self.config["schema"], "schema")
        if driver_module:
            need(driver_module, extra)
        self.engine = create_engine(url, **kwargs)

    def _connect(self):
        """A connection that cannot write where the database supports it (PostgreSQL read-only transaction). Elsewhere rely on a read-only user."""
        if getattr(self, "_host", None):          # again right before connecting: the driver resolves the name itself (see check_host)
            check_host(self._host)
        conn = self.engine.connect()
        return conn.execution_options(postgresql_readonly=True) if self.engine.dialect.name == "postgresql" else conn

    def _statement(self):
        if self.config.get("query"):
            return text(self.config["query"])
        schema = self.config.get("schema") if self.config.get("type") not in ("snowflake", "bigquery") else None
        return select(text("*")).select_from(table(self.config["table"], schema=schema))

    def test_connection(self) -> None:
        with self._connect() as conn:
            conn.execute(text("SELECT 1 FROM DUAL" if self.engine.dialect.name == "oracle" else "SELECT 1"))
        self.columns()

    def columns(self) -> list[str]:
        with self._connect() as conn:
            if self.config.get("query"):  # wrap so the database does not run the full query just to name columns
                q = self.config["query"].rstrip().rstrip(";")
                stmt = text(f"SELECT * FROM ({q}) _q WHERE 1 = 0") if self.engine.dialect.name in ("oracle", "mssql") else text(f"SELECT * FROM ({q}) AS _q LIMIT 0")
            else:
                stmt = self._statement().where(text("1 = 0"))
            res = conn.execute(stmt)
            keys = list(res.keys())
            res.close()
            return keys

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        with self._connect().execution_options(stream_results=True) as conn:
            res = conn.execute(self._statement())
            keys = list(res.keys())
            sent = False
            while True:
                rows = res.fetchmany(batch_size)
                if not rows:
                    break
                sent = True
                yield to_string_table(keys, [list(r) for r in rows])
            if not sent:
                yield to_string_table(keys, [])


@register
class SqlConnector(_Database):
    """Any SQLAlchemy-supported database from a complete URL held in an environment variable (url_env).

    Config: {"type": "sql", "url_env": "DEDUPE_SRC_DB_URL", "table": "customers", "schema": "public"}  or  {..., "query": "SELECT ..."}
    """
    types = ("sql",)

    def __init__(self, **config):
        super().__init__(**config)
        if config.get("url") and _production():
            raise ConnectorConfigError("a database URL (it holds the password) must come from an environment variable (url_env) in production")
        url = secret(config.get("url"), config.get("url_env"), "database URL")
        if not url:
            raise ValueError("SQL connector needs url_env (preferred) or url")
        self._setup(url, {}, None, None)


@register
class DatabaseConnector(_Database):
    """A database described by form settings (see ENGINES); `url_env` still works for every type and then wins over the form."""
    types = tuple(e for e in ENGINES if e != "sqlserver_pymssql") + ("azure_sql", "rds", "aurora")

    def __init__(self, **config):
        super().__init__(**config)
        t = config["type"]
        engine = {"azure_sql": "sqlserver", "rds": config.get("engine") or "postgres", "aurora": config.get("engine") or "postgres"}.get(t, t)
        if t in ("rds", "aurora") and engine not in RDS_ENGINES:
            raise ConnectorConfigError("engine must be one of " + ", ".join(sorted(RDS_ENGINES)))
        if engine == "sqlserver" and config.get("odbc_driver") == "pymssql":
            engine = "sqlserver_pymssql"
        _, module, extra, _ = ENGINES[engine]
        if config.get("url_env"):                      # a complete URL: its own driver name decides, SQLAlchemy reports a missing driver
            self._setup(secret(None, config["url_env"], "database URL"), {}, None, None)
            return
        conf = dict(config)
        if engine == "sqlserver_pymssql":
            conf.pop("odbc_driver", None)
        url, kwargs = build_url(engine, conf)
        self._host = conf.get("host") if engine not in ("snowflake", "bigquery") else None
        if engine == "sqlserver_pymssql":
            url = url.set(query={})
        self._setup(url, kwargs, module, extra)


@register
class SqliteConnector(_Database):
    """A SQLite database file inside an allowed data folder, opened read-only."""
    types = ("sqlite",)

    def __init__(self, **config):
        super().__init__(**config)
        p = check_path(Path(config.get("path", "")))
        if not p.is_file():
            raise FileNotFoundError("database file not found")
        self._setup(f"sqlite:///file:{p.as_posix()}?mode=ro&uri=true", {}, None, None)


@register
class DuckdbFileConnector(Connector):
    """A DuckDB database file inside an allowed data folder, opened read-only."""
    types = ("duckdb",)

    def __init__(self, **config):
        super().__init__(**config)
        self.path = check_path(Path(config.get("path", "")))
        if bool(config.get("table")) == bool(config.get("query")):
            raise ValueError("a database source needs exactly one of: table, query")
        if config.get("query"):
            check_select_only(config["query"])
        else:
            check_identifier(config["table"], "table")
            if config.get("schema"):
                check_identifier(config["schema"], "schema")

    def _sql(self) -> str:
        if self.config.get("query"):
            return self.config["query"].rstrip().rstrip(";")
        name = f'"{self.config["schema"]}"."{self.config["table"]}"' if self.config.get("schema") else f'"{self.config["table"]}"'
        return f"SELECT * FROM {name}"

    def _con(self):
        import duckdb
        if not self.path.is_file():
            raise FileNotFoundError("database file not found")
        con = duckdb.connect(str(self.path), read_only=True)
        con.execute("SET enable_external_access = false")          # the query may only see this database
        return con

    def test_connection(self) -> None:
        self.columns()

    def columns(self) -> list[str]:
        con = self._con()
        try:
            return [d[0] for d in con.execute(f"SELECT * FROM ({self._sql()}) _q LIMIT 0").description]
        finally:
            con.close()

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        con = self._con()
        try:
            reader = con.execute(self._sql()).to_arrow_reader(batch_size)
            sent = False
            for b in reader:
                sent = True
                yield _as_text(pa.Table.from_batches([b]))
            if not sent:
                yield to_string_table([d[0] for d in con.description], [])
        finally:
            con.close()
