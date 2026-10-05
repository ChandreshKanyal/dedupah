import duckdb

import dedupe
from dedupe.config.settings import get_settings


def test_package_imports():
    assert dedupe.__version__


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("DEDUPE_DATA_DIR", "/tmp/x")
    assert get_settings().data_dir == "/tmp/x"


def test_duckdb_works():
    assert duckdb.sql("select 1 + 1").fetchone() == (2,)
