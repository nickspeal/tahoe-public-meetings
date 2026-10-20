FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY packages/domain/topics.json packages/domain/agencies.json ./packages/domain/
COPY config/meeting-durations.json ./config/
COPY workers/ingestion/pyproject.toml workers/ingestion/README.md workers/ingestion/build_hook.py ./workers/ingestion/
COPY workers/ingestion/tahoe_ingest ./workers/ingestion/tahoe_ingest
RUN pip install --no-cache-dir ./workers/ingestion
ENTRYPOINT ["tahoe-ingest"]
