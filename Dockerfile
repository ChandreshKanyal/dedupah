FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Dependencies first so this layer is cached until pyproject.toml changes
COPY pyproject.toml ./
RUN mkdir -p src/dedupe && touch src/dedupe/__init__.py \
    && pip install -e ".[dev]"

COPY . .
RUN pip install -e ".[dev]" --no-deps

CMD ["pytest", "-q"]
