"""Database addresses: a plain "postgresql://" (or "postgres://") address names no driver, and SQLAlchemy's default for it changed in 2.1
(psycopg2 -> psycopg 3). We ship psycopg2 (the "postgres" extra), so every engine this package opens names it explicitly; an address that
already names a driver is left alone."""

_PLAIN = ("postgresql://", "postgres://")


def driver_url(url: str) -> str:
    for prefix in _PLAIN:
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url
