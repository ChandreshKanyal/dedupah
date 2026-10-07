"""Creating tables and adding the columns later versions need.

Every module declares its own tables and used to call `metadata.create_all(engine)`, which creates missing TABLES but never adds a missing COLUMN to a
table that already exists: after an upgrade the new code would fail on the old database. `create_and_upgrade` does both. It is deliberately small:

  - it only ADDS columns (never drops, renames or retypes anything), so an upgrade can always be undone by running the old version again;
  - a new column must be nullable or carry a server default (a database refuses `ADD COLUMN ... NOT NULL` without one), which is checked here so the
    mistake shows up in the first test run instead of on a customer's database;
  - it works the same on SQLite and PostgreSQL.

This is not Alembic: the tables belong to several independent modules, so one migration history would mean moving them all. If the schema ever
needs a rename or a type change, adopt Alembic then.
"""
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import Engine


def create_and_upgrade(engine: Engine, metadata: MetaData) -> list[str]:
    """Create missing tables, then add missing columns to existing ones. Returns the columns added, as 'table.column'."""
    metadata.create_all(engine)
    added = []
    insp = inspect(engine)
    for table in metadata.sorted_tables:
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            if not col.nullable and col.server_default is None and not col.primary_key:
                raise RuntimeError(f"column {table.name}.{col.name} is new and NOT NULL: give it a server_default or make it nullable")
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=engine.dialect)}'
            if col.server_default is not None:
                ddl += f" DEFAULT {col.server_default.arg.text if hasattr(col.server_default.arg, 'text') else col.server_default.arg!r}"
            if not col.nullable:
                ddl += " NOT NULL"
            with engine.begin() as conn:
                conn.execute(text(ddl))
            added.append(f"{table.name}.{col.name}")
    return added


__all__ = ["create_and_upgrade"]
