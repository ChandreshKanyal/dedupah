import csv
import io
import urllib.request
from typing import Iterator

import pyarrow as pa

from .base import Connector, register, secret, to_string_table


@register
class GoogleSheetsConnector(Connector):
    """Google Sheets via the CSV export endpoint.

    Config: {"type": "gsheet", "spreadsheet_id": "...", "gid": "0", "token_env": "GSHEETS_TOKEN"}
    Works for sheets shared by link without a token, or with an OAuth access token (Drive read scope) from the
    environment variable named in token_env. NOT yet verified against live Google (no credentials in dev).
    """
    types = ("gsheet", "google_sheets")

    def _url(self) -> str:
        sid, gid = self.config["spreadsheet_id"], self.config.get("gid", "0")
        return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"

    def _fetch(self) -> str:
        token = secret(None, self.config.get("token_env"), "Google access token") if self.config.get("token_env") else None
        req = urllib.request.Request(self._url(), headers={"Authorization": f"Bearer {token}"} if token else {})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8-sig")

    def _rows(self) -> list[list[str]]:
        return list(csv.reader(io.StringIO(self._fetch())))

    def test_connection(self) -> None:
        if not self._rows():
            raise ValueError("Sheet is empty or not accessible")

    def columns(self) -> list[str]:
        return self._rows()[0]

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        rows = self._rows()
        header, body = rows[0], rows[1:]
        for i in range(0, max(len(body), 1), batch_size):
            chunk = [r + [None] * (len(header) - len(r)) for r in body[i:i + batch_size]]
            yield to_string_table(header, [[None if v == "" else v for v in r] for r in chunk])
