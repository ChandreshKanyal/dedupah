"""Plain PostgreSQL addresses get the driver we ship (SQLAlchemy 2.1 changed its default to psycopg 3, which is not installed)."""
from dedupe.dburl import driver_url


def test_plain_postgres_addresses_name_psycopg2_and_others_are_left_alone():
    assert driver_url("postgresql://u:p@db:5432/dedupe") == "postgresql+psycopg2://u:p@db:5432/dedupe"
    assert driver_url("postgres://u:p@db/x") == "postgresql+psycopg2://u:p@db/x"
    for same in ("postgresql+psycopg://u@db/x", "postgresql+psycopg2://u@db/x", "sqlite:///data/api.db", "mysql+pymysql://u@h/d"):
        assert driver_url(same) == same
