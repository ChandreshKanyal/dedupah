import csv
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from .base import Connector, register, to_string_table
from .guard import check_path


class _FileConnector(Connector):
    def __init__(self, path: str, **config):
        super().__init__(path=path, **config)
        self.path = check_path(Path(path))

    def test_connection(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"{self.path} not found")
        self.columns()


@register
class CsvConnector(_FileConnector):
    """CSV / TSV. Everything is read as text so no value is silently reinterpreted (e.g. leading zeros)."""
    types = ("csv", "tsv")

    @property
    def _delimiter(self) -> str:
        return self.config.get("delimiter") or ("\t" if self.config["type"] == "tsv" else ",")

    def columns(self) -> list[str]:
        with self.path.open(encoding=self.config.get("encoding", "utf-8-sig"), newline="") as f:
            return next(csv.reader(f, delimiter=self._delimiter))

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        cols = self.columns()
        reader = pacsv.open_csv(
            self.path,
            read_options=pacsv.ReadOptions(encoding=self.config.get("encoding", "utf-8-sig"), block_size=8 << 20),
            parse_options=pacsv.ParseOptions(delimiter=self._delimiter),
            convert_options=pacsv.ConvertOptions(column_types={c: pa.string() for c in cols}, strings_can_be_null=False),
        )
        for batch in reader:
            yield pa.Table.from_batches([batch])


@register
class XlsxConnector(_FileConnector):
    types = ("xlsx", "excel")

    def _sheet(self, wb):
        name = self.config.get("sheet")
        return wb[name] if name else wb.active

    def columns(self) -> list[str]:
        from openpyxl import load_workbook
        wb = load_workbook(self.path, read_only=True)
        try:
            return [str(h) for h in next(self._sheet(wb).values)]
        finally:
            wb.close()

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        from openpyxl import load_workbook
        wb = load_workbook(self.path, read_only=True)
        try:
            it = self._sheet(wb).values
            header = [str(h) for h in next(it)]
            buf: list[list] = []
            for row in it:
                buf.append(list(row))
                if len(buf) >= batch_size:
                    yield to_string_table(header, buf)
                    buf = []
            if buf:
                yield to_string_table(header, buf)
        finally:
            wb.close()


@register
class ParquetConnector(_FileConnector):
    types = ("parquet",)

    def columns(self) -> list[str]:
        return pq.ParquetFile(self.path).schema_arrow.names

    def read_batches(self, batch_size: int = 50_000) -> Iterator[pa.Table]:
        for batch in pq.ParquetFile(self.path).iter_batches(batch_size=batch_size):
            t = pa.Table.from_batches([batch])
            yield t.cast(pa.schema([(n, pa.string()) for n in t.schema.names]))
