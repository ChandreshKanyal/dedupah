"""Files that live elsewhere: SFTP servers and cloud object storage (Amazon S3 and S3-compatible stores, Azure Blob / ADLS, Google Cloud Storage).

SFTP (paramiko): the server's host key MUST be given and is checked (an unknown or changed key is refused: no trust on first use). The file is
downloaded into the workspace, read with the same readers as local files, and deleted as soon as it has been read.

Object storage (DuckDB's httpfs / azure extensions, docs: duckdb.org/docs/extensions/httpfs/s3api, .../azure): files are read where they are,
with credentials passed as a temporary DuckDB secret built from environment variables. Local files are switched off on that DuckDB connection, so
a crafted path can never reach the server's own disk. The extensions download once on first use (pre-install them in the container image).
"""
import base64
import contextlib
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterator

import pyarrow as pa

from .base import Connector, register, secret, to_string_table
from .files import FILE_TYPES, _as_text, read_local
from .guard import ConnectorConfigError, allow_insecure_http, check_host
from .records import batches_of, columns_of, need

MAX_DOWNLOAD = 4 * 1024 ** 3                 # 4 GB per SFTP file
_REMOTE_PATH = re.compile(r"^[\w\-. /()+=,@~]{1,500}$")


def _work_tmp() -> Path:
    d = Path(os.environ.get("DEDUPE_WORK_DIR", "data/staging")) / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_private_key(paramiko, text: str):
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text))
        except (paramiko.SSHException, ValueError):
            continue
    raise ConnectorConfigError("the private key could not be read (OpenSSH or PEM format, without a passphrase)")


@register
class SftpConnector(Connector):
    """One file on an SFTP server. Settings: host, port, username, password_env or private_key_env, host_key (the server's public key as in
    known_hosts: 'ssh-ed25519 AAAA...'), path (the remote file), format (csv, xlsx, json, ...) and the format's own options."""
    types = ("sftp",)

    def __init__(self, **config):
        super().__init__(**config)
        self.host = check_host(config.get("host", ""))
        self.port = int(config.get("port") or 22)
        self.fmt = config.get("format") or "csv"
        if self.fmt not in FILE_TYPES:
            raise ConnectorConfigError("unknown file format; choose one of: " + ", ".join(sorted(FILE_TYPES)))
        if not _REMOTE_PATH.match(config.get("path") or "") or ".." in config["path"].split("/"):
            raise ConnectorConfigError("path must be the remote file's path (letters, digits and / . _ -)")
        if not config.get("host_key"):
            raise ConnectorConfigError("host_key is required: the server's public key as in known_hosts (ask the server's administrator)")
        if not config.get("username") or not (config.get("password_env") or config.get("private_key_env")):
            raise ConnectorConfigError("give username and one of password_env or private_key_env")
        self._local: Path | None = None
        self._reader = None

    def _client(self):
        paramiko = need("paramiko", "sftp")
        parts = str(self.config["host_key"]).split()
        if len(parts) < 2:
            raise ConnectorConfigError("host_key must look like 'ssh-ed25519 AAAA...' (key type, then the key)")
        try:
            key = paramiko.PKey.from_type_string(parts[0], base64.b64decode(parts[1]))
        except Exception:  # noqa: BLE001
            raise ConnectorConfigError("host_key is not a valid public key") from None
        client = paramiko.SSHClient()
        name = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
        client.get_host_keys().add(name, key.get_name(), key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        pkey = _load_private_key(paramiko, secret(None, self.config["private_key_env"], "SFTP private key")) if self.config.get("private_key_env") else None
        pw = secret(None, self.config["password_env"], "SFTP password") if self.config.get("password_env") else None
        client.connect(self.host, port=self.port, username=self.config["username"], password=pw, pkey=pkey, allow_agent=False, look_for_keys=False,
                       timeout=30, banner_timeout=30, auth_timeout=30)
        return client

    def _download(self):
        if self._reader is not None:
            return self._reader
        client = self._client()
        try:
            sftp = client.open_sftp()
            size = sftp.stat(self.config["path"]).st_size or 0
            if size > MAX_DOWNLOAD:
                raise ConnectorConfigError("the remote file is larger than the allowed size")
            fd, name = tempfile.mkstemp(dir=_work_tmp(), suffix="." + self.fmt.replace("_", ""))
            os.close(fd)
            self._local = Path(name)
            sftp.get(self.config["path"], str(self._local))
        finally:
            client.close()
        self._reader = read_local(self.fmt, self._local, self.config)
        return self._reader

    def _cleanup(self):
        if self._local is not None:
            self._local.unlink(missing_ok=True)
            self._local, self._reader = None, None

    def test_connection(self) -> None:
        self._download().columns()

    def columns(self) -> list[str]:
        return self._download().columns()

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        try:
            yield from self._download().read_batches(batch_size)
        finally:
            self._cleanup()                       # the downloaded copy never stays on disk

    def __del__(self):
        with contextlib.suppress(Exception):      # at interpreter exit the file system helpers may already be gone
            self._cleanup()


# ---- cloud object storage through DuckDB -------------------------------------------------------------------------------------------
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")
_ACCOUNT = re.compile(r"^[a-z0-9]{3,24}$")
_CONTAINER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
_OBJECT = re.compile(r"^[\w\-. /()+=,@~*?\[\]{}]{1,1000}$")
_REGION = re.compile(r"^[a-z0-9-]{2,40}$")
CLOUD_FORMATS = ("csv", "tsv", "parquet", "json", "jsonl", "xlsx")


def _q(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


class _ObjectStore(Connector):
    scheme = ""
    extensions: tuple[str, ...] = ()

    def __init__(self, **config):
        super().__init__(**config)
        self.fmt = config.get("format") or "csv"
        if self.fmt not in CLOUD_FORMATS:
            raise ConnectorConfigError("format must be one of " + ", ".join(CLOUD_FORMATS))
        p = config.get("path") or ""
        if not _OBJECT.match(p) or p.startswith("/") or ".." in p.split("/"):
            raise ConnectorConfigError("path must be the object's key or a pattern inside the bucket, e.g. exports/customers.csv or exports/*.parquet")
        self.url = self._url(p)

    def _url(self, path: str) -> str:
        raise NotImplementedError

    def _secret_sql(self) -> str:
        raise NotImplementedError

    def _con(self):
        import duckdb
        secret_sql = self._secret_sql()                # first: it may add an extension (credential_chain needs "aws")
        con = duckdb.connect()
        if os.environ.get("DEDUPE_DUCKDB_EXTENSIONS"):           # extensions installed with the image (no download at run time)
            con.execute(f"SET extension_directory = {_q(os.environ['DEDUPE_DUCKDB_EXTENSIONS'])}")
        for ext in self.extensions:
            con.execute(f"INSTALL {ext}; LOAD {ext}")
        if self.fmt == "xlsx":
            con.execute("INSTALL excel; LOAD excel")
        con.execute(secret_sql)
        con.execute("SET disabled_filesystems = 'LocalFileSystem'")     # only the bucket can be read from here
        con.execute("SET lock_configuration = true")
        return con

    def _relation_sql(self) -> str:
        if self.fmt in ("csv", "tsv"):
            delim = self.config.get("delimiter") or ("\t" if self.fmt == "tsv" else ",")
            return f"SELECT * FROM read_csv($u, all_varchar = true, header = true, union_by_name = true, delim = {_q(delim)})"
        if self.fmt == "parquet":
            return "SELECT * FROM read_parquet($u, union_by_name = true)"
        if self.fmt == "xlsx":
            sheet = self.config.get("sheet")
            return "SELECT * FROM read_xlsx($u, all_varchar = true, header = true" + (f", sheet = {_q(sheet)}" if sheet else "") + ")"
        return "SELECT json FROM read_json_objects($u, format = " + ("'newline_delimited'" if self.fmt == "jsonl" else "'array'") + ")"

    def _records(self, con):
        cur = con.execute(self._relation_sql(), {"u": self.url})
        while rows := cur.fetchmany(10_000):
            for (doc,) in rows:
                rec = json.loads(doc)
                yield rec if isinstance(rec, dict) else {"value": rec}

    def test_connection(self) -> None:
        self.columns()

    def columns(self) -> list[str]:
        con = self._con()
        try:
            if self.fmt in ("json", "jsonl"):
                return columns_of(self._records(con))
            return [d[0] for d in con.execute(f"SELECT * FROM ({self._relation_sql()}) LIMIT 0", {"u": self.url}).description]
        finally:
            con.close()

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        cols = self.columns()
        con = self._con()
        try:
            if self.fmt in ("json", "jsonl"):
                yield from batches_of(self._records(con), batch_size, cols)
                return
            reader = con.execute(self._relation_sql(), {"u": self.url}).to_arrow_reader(batch_size)
            sent = False
            for b in reader:
                sent = True
                yield _as_text(pa.Table.from_batches([b]))
            if not sent:
                yield to_string_table(cols, [])
        finally:
            con.close()


@register
class S3Connector(_ObjectStore):
    """Amazon S3, or any S3-compatible store (MinIO, Ceph, Wasabi, Cloudflare R2...) via `endpoint`. Credentials: access_key_env + secret_key_env, or
    auth = credential_chain to use the server's own AWS identity (instance role, AWS_PROFILE)."""
    types = ("s3",)
    extensions = ("httpfs",)

    def _url(self, path: str) -> str:
        b = self.config.get("bucket") or ""
        if not _BUCKET.match(b):
            raise ConnectorConfigError("bucket must be an S3 bucket name (lower case letters, digits, dots and hyphens)")
        return f"s3://{b}/{path}"

    def _secret_sql(self) -> str:
        parts = ["TYPE s3"]
        if self.config.get("region"):
            if not _REGION.match(self.config["region"]):
                raise ConnectorConfigError("region must look like ap-south-1")
            parts.append(f"REGION {_q(self.config['region'])}")
        if self.config.get("endpoint"):
            ep = self.config["endpoint"]
            host, _, port = ep.partition(":")
            check_host(host, "endpoint")
            if port and not port.isdigit():
                raise ConnectorConfigError("endpoint must be host or host:port")
            parts += [f"ENDPOINT {_q(ep)}", f"URL_STYLE {_q(self.config.get('url_style') or 'path')}"]
            if self.config.get("use_ssl") == "no":
                if not allow_insecure_http():
                    raise ConnectorConfigError("an S3 endpoint without TLS is only allowed in local tests")
                parts.append("USE_SSL false")
        if self.config.get("auth") == "credential_chain":
            self.extensions = ("httpfs", "aws")
            parts.append("PROVIDER credential_chain")
        else:
            key = secret(None, self.config.get("access_key_env"), "S3 access key id")
            sec = secret(None, self.config.get("secret_key_env"), "S3 secret access key")
            if not key or not sec:
                raise ConnectorConfigError("give access_key_env and secret_key_env, or choose auth = credential_chain")
            parts += [f"KEY_ID {_q(key)}", f"SECRET {_q(sec)}"]
        return "CREATE TEMPORARY SECRET src (" + ", ".join(parts) + ")"


@register
class GcsConnector(_ObjectStore):
    """Google Cloud Storage through its S3-compatible XML API with HMAC keys (hmac_key_env, hmac_secret_env), as DuckDB documents."""
    types = ("gcs",)
    extensions = ("httpfs",)

    def _url(self, path: str) -> str:
        b = self.config.get("bucket") or ""
        if not _BUCKET.match(b):
            raise ConnectorConfigError("bucket must be a Cloud Storage bucket name")
        return f"gs://{b}/{path}"

    def _secret_sql(self) -> str:
        key = secret(None, self.config.get("hmac_key_env"), "GCS HMAC key id")
        sec = secret(None, self.config.get("hmac_secret_env"), "GCS HMAC secret")
        if not key or not sec:
            raise ConnectorConfigError("give hmac_key_env and hmac_secret_env (an HMAC key of a service account with read access)")
        return f"CREATE TEMPORARY SECRET src (TYPE gcs, KEY_ID {_q(key)}, SECRET {_q(sec)})"


@register
class AzureBlobConnector(_ObjectStore):
    """Azure Blob Storage / ADLS Gen2. Credentials: connection_string_env, or auth = credential_chain (managed identity, Azure CLI login)."""
    types = ("azure_blob",)
    extensions = ("azure",)

    def _url(self, path: str) -> str:
        acc, cont = self.config.get("account") or "", self.config.get("container") or ""
        if not _ACCOUNT.match(acc) or not _CONTAINER.match(cont):
            raise ConnectorConfigError("account is the storage account name (3-24 lower case letters or digits); container is the container name")
        return f"az://{cont}/{path}"

    def _secret_sql(self) -> str:
        if self.config.get("auth") == "credential_chain":
            return f"CREATE TEMPORARY SECRET src (TYPE azure, PROVIDER credential_chain, ACCOUNT_NAME {_q(self.config['account'])})"
        cs = secret(None, self.config.get("connection_string_env"), "Azure storage connection string")
        if not cs:
            raise ConnectorConfigError("give connection_string_env, or choose auth = credential_chain")
        return f"CREATE TEMPORARY SECRET src (TYPE azure, CONNECTION_STRING {_q(cs)})"
