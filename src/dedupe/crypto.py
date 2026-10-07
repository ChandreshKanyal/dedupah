"""Encryption at rest for everything the pipeline writes to disk (Parquet files, DuckDB spill files).

    DEDUPE_DATA_KEY      32 random bytes, base64 (generate: python -m dedupe.crypto new-key). Comes from the environment, i.e. from the
                         customer's secret store / KMS at start-up; it is never written to disk by this code and never logged.
    DEDUPE_DATA_KEY_ID   a label for the key (default "k1"); recorded next to the data so a wrong or rotated key is recognised.

With a key set, every Parquet file is written with Parquet Modular Encryption (AES-GCM-256, footer and all columns), DuckDB's temporary
spill files are encrypted, and files without the key cannot be read. Without a key nothing changes (development). Production refuses to
start without one (`require_in_production`).

What this does NOT cover (the deployment must): the customer's own input files, the database that holds the judgment / config / security logs
(use an encrypted database volume or the database's own encryption), operating-system swap and crash dumps, backups of those, and the key
itself (keep it in a KMS/HSM; this code reads it from the environment). Whole-disk or volume encryption (BitLocker, LUKS, cloud volume keys) is
still the baseline underneath; this layer means a copied file or a backup is unreadable without the key.

    python -m dedupe.crypto new-key
    python -m dedupe.crypto status --work data/staging
    python -m dedupe.crypto convert --work data/staging [--also data/registry]     # encrypt existing plain files (key in the environment)
    python -m dedupe.crypto rekey --work data/staging --old-key-env DEDUPE_OLD_KEY --old-key-id k1     # move to the key in DEDUPE_DATA_KEY
"""
import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

KEY_NAME = "dedupe"
MAGIC_ENCRYPTED = b"PARE"
SIDECAR = ".encryption.json"


class CryptoError(RuntimeError):
    pass


@dataclass(frozen=True)
class Encryption:
    key_id: str
    key: str = field(repr=False)                     # base64; never shown in repr, errors or logs

    @property
    def check(self) -> str:
        """A fingerprint that identifies the key without revealing it (stored next to the data)."""
        return hmac.new(base64.b64decode(self.key), b"dedupe-key-check", hashlib.sha256).hexdigest()[:16]


def parse_key(value: str, key_id: str = "k1") -> Encryption:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise CryptoError("the data key must be base64 text (python -m dedupe.crypto new-key makes one)") from None
    if len(raw) != 32:
        raise CryptoError("the data key must be exactly 32 bytes (256 bits) once decoded")
    if not key_id or len(key_id) > 40 or not key_id.replace("-", "").replace("_", "").isalnum():
        raise CryptoError("the key id is a short label of letters, digits, - and _")
    return Encryption(key_id, value.strip())


def from_env(name: str = "DEDUPE_DATA_KEY", id_name: str = "DEDUPE_DATA_KEY_ID") -> Encryption | None:
    v = os.environ.get(name, "").strip()
    return parse_key(v, os.environ.get(id_name, "k1").strip() or "k1") if v else None


_UNSET = object()
_active = _UNSET


def active() -> Encryption | None:
    global _active
    if _active is _UNSET:
        _active = from_env()
    return _active


def configure(enc: Encryption | None | object = _UNSET) -> None:
    """Set the key in code (tests, embedding); with no argument, forget it so the environment is read again."""
    global _active
    _active = enc


def enabled() -> bool:
    return active() is not None


def require_in_production(env: str) -> None:
    if env == "production" and not enabled():
        raise CryptoError("production needs encryption at rest: set DEDUPE_DATA_KEY (and DEDUPE_DATA_KEY_ID)")


# ---- SQL fragments (use inside the f-strings that build DuckDB statements) ----------------------------------------------
_CFG = f"encryption_config={{footer_key: '{KEY_NAME}'}}"


def rp(path, row_numbers: bool = False) -> str:
    """read_parquet('<path>') that also decrypts when a key is set; `row_numbers` adds file_row_number (the row's position in the file)."""
    opts = ([_CFG] if enabled() else []) + (["file_row_number = true"] if row_numbers else [])
    return f"read_parquet('{path}'{''.join(', ' + o for o in opts)})"


def rp_param() -> str:
    """read_parquet(?) with the path passed as a bound parameter."""
    return f"read_parquet(?, {_CFG})" if enabled() else "read_parquet(?)"


def fmt() -> str:
    """The options of a COPY ... TO '<file>' (...) that writes Parquet (encrypted when a key is set)."""
    return f"FORMAT PARQUET, ENCRYPTION_CONFIG {{footer_key: '{KEY_NAME}'}}" if enabled() else "FORMAT PARQUET"


def register(con, enc: Encryption | None = None, name: str = KEY_NAME) -> None:
    enc = enc or active()
    if enc is not None:
        con.execute(f"PRAGMA add_parquet_key('{name}', '{enc.key}')")


def connect(config: dict | None = None) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection that knows the key and encrypts its spill files. Use this instead of duckdb.connect()."""
    con = duckdb.connect(config=config or {})
    if enabled():
        register(con)
        con.execute("SET temp_file_encryption = true")
    return con


# ---- Arrow tables and batches (DuckDB writes and reads them, so the files are encrypted the same way) ------------------
def write_table(table: pa.Table, path: Path) -> None:
    """Write one Arrow table as a Parquet file (encrypted when a key is set), atomically."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    if not enabled():
        pq.write_table(table, tmp)
    else:
        con = connect()
        con.register("_t", table)
        con.execute(f"COPY (SELECT * FROM _t) TO '{tmp}' ({fmt()})")
        con.close()
    os.replace(tmp, path)


def iter_batches(path: Path, batch_size: int):
    """Record batches of a Parquet file, decrypting when a key is set."""
    if not enabled():
        yield from pq.ParquetFile(path).iter_batches(batch_size=batch_size)
        return
    con = connect()
    try:
        yield from con.execute(f"SELECT * FROM {rp(path)}").fetch_record_batch(batch_size)
    finally:
        con.close()


class _PartsWriter:
    """Writes batches as separate encrypted part files and joins them into one file on close (Parquet writers cannot append to an encrypted file
    from DuckDB). Part files are encrypted too, and removed after the join."""

    def __init__(self, path: Path, schema: pa.Schema):
        self.path, self.schema, self.parts = Path(path), schema, []

    def write_table(self, table: pa.Table) -> None:
        part = self.path.with_name(f"{self.path.name}.part{len(self.parts):06d}")
        write_table(table, part)
        self.parts.append(part)

    def close(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        con = connect()
        try:
            if self.parts:
                names = ", ".join(f"'{p}'" for p in self.parts)
                con.execute(f"COPY (SELECT * FROM read_parquet([{names}], {_CFG}) ) TO '{tmp}' ({fmt()})")
            else:
                con.register("_t", self.schema.empty_table())
                con.execute(f"COPY (SELECT * FROM _t) TO '{tmp}' ({fmt()})")
        finally:
            con.close()
        os.replace(tmp, self.path)
        for p in self.parts:
            p.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self.close()
        else:
            for p in self.parts:
                p.unlink(missing_ok=True)


def open_writer(path: Path, schema: pa.Schema):
    """A context manager with write_table(): a plain ParquetWriter without a key, an encrypting one with a key."""
    return pq.ParquetWriter(path, schema) if not enabled() else _PartsWriter(path, schema)


# ---- the work folder remembers which key protects it -------------------------------------------------------------------
def _parquet_files(*folders: Path) -> list[Path]:
    return sorted({f for d in folders if Path(d).exists() for f in Path(d).rglob("*.parquet")} | {f for d in folders if Path(d).exists() for f in Path(d).rglob("*.parquet.part*")})


def is_encrypted_file(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == MAGIC_ENCRYPTED


def bind_work(work: Path, *, write: bool = True, extra: tuple = ()) -> None:
    """Make sure the folder (and the registry folders in `extra`) is used with the right key.
    No key and an encrypted folder, a different key, or plain files next to a key are refused with an explanation."""
    work = Path(work)
    side = work / SIDECAR
    enc = active()
    recorded = json.loads(side.read_text(encoding="utf-8")) if side.exists() else None
    if recorded and enc is None:
        raise CryptoError(f"{work} holds encrypted data (key id {recorded.get('key_id')!r}) but DEDUPE_DATA_KEY is not set")
    if enc is None:
        return
    if recorded:
        if recorded.get("check") != enc.check:
            raise CryptoError(f"{work} was encrypted with a different key (key id {recorded.get('key_id')!r}); this process has key id {enc.key_id!r}. Use "
                              "the matching key, or move the data with: python -m dedupe.crypto rekey")
        return
    plain = [f for f in _parquet_files(work, *extra) if not is_encrypted_file(f)]
    if plain:
        raise CryptoError(f"{work} already holds {len(plain)} unencrypted Parquet file(s); encrypt them first with: python -m dedupe.crypto convert --work {work}")
    if write:
        work.mkdir(parents=True, exist_ok=True)
        side.write_text(json.dumps({"key_id": enc.key_id, "check": enc.check, "cipher": "AES-GCM-256 (Parquet modular encryption)"}, indent=2), encoding="utf-8")


def status(work: Path, *extra: Path) -> dict:
    work = Path(work)
    files = _parquet_files(work, *extra)
    enc = [f for f in files if is_encrypted_file(f)]
    side = work / SIDECAR
    rec = json.loads(side.read_text(encoding="utf-8")) if side.exists() else None
    return {"key_configured": enabled(), "key_id": active().key_id if enabled() else None, "recorded_key_id": rec and rec.get("key_id"),
            "key_matches_recorded": None if not (rec and enabled()) else rec.get("check") == active().check,
            "parquet_files": len(files), "encrypted": len(enc), "plain": [str(f) for f in files if f not in enc][:20]}


def rekey(work: Path, new: Encryption, old: Encryption | None = None, extra: tuple = ()) -> dict:
    """Re-encrypt every Parquet file under `work` (and `extra`) from `old` (None = plain files) to `new`. Safe to re-run: a file already readable
    with the new key is skipped, so an interrupted run can simply be repeated."""
    work = Path(work)
    done = skipped = 0
    con = duckdb.connect()
    register(con, new, "dedupe_new")
    if old is not None:
        register(con, old, "dedupe_old")
    try:
        for f in _parquet_files(work, *extra):
            if is_encrypted_file(f):
                try:
                    con.execute(f"SELECT 1 FROM read_parquet('{f}', encryption_config={{footer_key: 'dedupe_new'}}) LIMIT 1").fetchall()
                    skipped += 1
                    continue
                except duckdb.Error:
                    if old is None:
                        raise CryptoError(f"{f.name} is encrypted with another key: pass the old key") from None
                src = f"read_parquet('{f}', encryption_config={{footer_key: 'dedupe_old'}})"
            else:
                src = f"read_parquet('{f}')"
            tmp = f.with_name(f.name + ".rekey")
            con.execute(f"COPY (SELECT * FROM {src}) TO '{tmp}' (FORMAT PARQUET, ENCRYPTION_CONFIG {{footer_key: 'dedupe_new'}})")
            os.replace(tmp, f)
            done += 1
    finally:
        con.close()
    work.mkdir(parents=True, exist_ok=True)
    (work / SIDECAR).write_text(json.dumps({"key_id": new.key_id, "check": new.check, "cipher": "AES-GCM-256 (Parquet modular encryption)"}, indent=2), encoding="utf-8")
    return {"rewritten": done, "already_current": skipped, "key_id": new.key_id}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-key", help="print a new random key (store it in your secret manager, never in the repository)")
    for name in ("status", "convert", "rekey"):
        p = sub.add_parser(name)
        p.add_argument("--work", type=Path, required=True)
        p.add_argument("--also", type=Path, action="append", default=[], help="another folder to include (e.g. the identity registry)")
        if name == "rekey":
            p.add_argument("--old-key-env", default="DEDUPE_OLD_KEY")
            p.add_argument("--old-key-id", default="k1")
    a = ap.parse_args()
    if a.cmd == "new-key":
        print(base64.b64encode(secrets.token_bytes(32)).decode())
        return
    if a.cmd == "status":
        print(json.dumps(status(a.work, *a.also), indent=2))
        return
    new = active()
    if new is None:
        raise SystemExit("set DEDUPE_DATA_KEY (the key to move TO)")
    if a.cmd == "convert":
        print(json.dumps(rekey(a.work, new, None, tuple(a.also)), indent=2))
    else:
        old = parse_key(os.environ.get(a.old_key_env, ""), a.old_key_id)
        print(json.dumps(rekey(a.work, new, old, tuple(a.also)), indent=2))


if __name__ == "__main__":
    main()
