"""A file uploaded through the screens: a Parquet table of text columns in the workspace's upload folder (encrypted when a data key is set).

Config: {"type": "upload", "upload_id": "U0123456789ab"}. The folder comes from DEDUPE_UPLOAD_DIR (set by the run launcher); the id is checked
against a strict pattern, so a config can never name a path.
"""
import os
import re
from pathlib import Path
from typing import Iterator

import pyarrow as pa

from dedupe import crypto

from .base import Connector, register
from .guard import ConnectorConfigError

_ID = re.compile(r"^U[0-9a-f]{12}$")


def upload_dir() -> Path:
    return Path(os.environ.get("DEDUPE_UPLOAD_DIR") or (Path(os.environ.get("DEDUPE_WORK_DIR", "data/staging")) / "uploads"))


@register
class UploadConnector(Connector):
    types = ("upload",)

    def __init__(self, **config):
        super().__init__(**config)
        uid = str(config.get("upload_id", ""))
        if not _ID.match(uid):
            raise ConnectorConfigError("an upload id looks like U followed by 12 letters or digits")
        self.path = upload_dir() / f"{uid}.parquet"

    def test_connection(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError("upload not found")

    def columns(self) -> list[str]:
        con = crypto.connect()
        try:
            return [d[0] for d in con.execute(f"SELECT * FROM {crypto.rp(self.path)} LIMIT 0").description]
        finally:
            con.close()

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        for b in crypto.iter_batches(self.path, batch_size):
            yield b if isinstance(b, pa.Table) else pa.Table.from_batches([b])
