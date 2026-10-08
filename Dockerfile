FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN pip install --upgrade pip setuptools

# ---- test image (development and CI): includes the test tools, runs as root, never ship it ----
FROM base AS test
# Dependencies first so this layer is cached until pyproject.toml changes
COPY pyproject.toml ./
RUN mkdir -p src/dedupe && touch src/dedupe/__init__.py \
    && pip install -e ".[dev,api,connectors]"
COPY . .
RUN pip install -e ".[dev,api,connectors]" --no-deps
CMD ["pytest", "-q"]

# ---- runtime image: only what the API needs, unprivileged user, health check ----
FROM base AS runtime
COPY pyproject.toml ./
RUN mkdir -p src/dedupe && touch src/dedupe/__init__.py \
    && pip install -e ".[api,connectors]"
# DuckDB extensions for cloud storage and Excel, installed at build time so the server needs no internet access to read S3 / Azure / GCS.
# SQL Server additionally needs Microsoft's ODBC Driver 18 (see learn.microsoft.com/sql/connect/odbc/linux-mac) plus the "sqlserver" extra.
ENV DEDUPE_DUCKDB_EXTENSIONS=/opt/duckdb_extensions
RUN python -c "import duckdb; c = duckdb.connect(); c.execute(\"SET extension_directory = '/opt/duckdb_extensions'\"); [c.execute('INSTALL ' + e) for e in ('httpfs', 'azure', 'aws', 'excel')]"
COPY src ./src
COPY configs ./configs
RUN pip install -e ".[api,connectors]" --no-deps \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin dedupe \
    && mkdir /data && chown dedupe /data
USER 10001
ENV DEDUPE_WORK_DIR=/data/work
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
# binds all interfaces INSIDE the container; publish the port only to localhost or a TLS-terminating proxy (see docker-compose.yml)
CMD ["python", "-m", "dedupe.api", "--host", "0.0.0.0"]
