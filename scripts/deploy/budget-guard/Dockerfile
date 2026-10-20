# Budget guard for the ingestion scheduler. Build context: scripts/deploy/budget-guard
FROM python:3.13-slim
WORKDIR /app
COPY main.py ./
USER nobody
CMD ["python", "-u", "main.py"]
