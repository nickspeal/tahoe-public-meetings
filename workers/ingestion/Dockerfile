# Build from the repository root (see the root .dockerignore allowlist):
#   docker build --platform linux/amd64 -f workers/ingestion/Dockerfile -t tahoe-ingest .
# Runs as a Cloud Run Job: `tahoe-ingest run-scheduled` by default.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# pdfinfo/pdftotext for extraction; curl + CA certificates for the verified-TLS fallback.
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Tracked source the pipeline ingests at runtime (no local database dumps).
COPY config/ ./config/
COPY packages/domain/ ./packages/domain/
COPY tests/fixtures/meetings.json ./tests/fixtures/meetings.json
COPY workers/ingestion/pyproject.toml workers/ingestion/README.md workers/ingestion/build_hook.py ./workers/ingestion/
COPY workers/ingestion/tahoe_ingest ./workers/ingestion/tahoe_ingest
# The build hook bundles the topic catalog, agency registry, and duration model into the wheel.
RUN pip install "./workers/ingestion[ai]" && rm -rf ./workers/ingestion \
    && useradd --system --uid 10001 --no-create-home --home-dir /nonexistent tahoe

USER 10001
# Relative config paths resolve against /app. Source PDFs live only in a per-run
# directory under TMPDIR (in memory on Cloud Run) that is removed after each run.
ENV TAHOE_SOURCE_ROOT=/app \
    TMPDIR=/tmp
ENTRYPOINT ["tahoe-ingest"]
CMD ["run-scheduled"]
