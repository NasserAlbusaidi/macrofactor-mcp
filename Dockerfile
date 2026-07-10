FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY main.py garmin_sync.py importers.py schemas.py ./
COPY lib ./lib
COPY tools ./tools
COPY docs ./docs

RUN pip install --no-cache-dir .

ENV MACROFACTOR_DB_PATH=/data/macrofactor.duckdb \
    MACROFACTOR_DATA_DIR=/data/exports
RUN mkdir -p /data/exports

ENTRYPOINT ["macrofactor-mcp"]
