import duckdb

import dedupe


def test_package_imports():
    assert dedupe.__version__


def test_duckdb_works():
    assert duckdb.sql("select 1 + 1").fetchone() == (2,)
