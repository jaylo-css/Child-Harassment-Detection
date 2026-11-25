# SafeChat Guardian backend: works on Google Cloud Run, Render, or any container host.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    FORWARDED_ALLOW_IPS="*"

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY pyproject.toml README.md ./
COPY app/__init__.py app/__init__.py
RUN pip install -e ".[llm]"

COPY app ./app
COPY scripts ./scripts
COPY data/synthetic ./data/synthetic

# Train the baseline classifier on the committed synthetic data (no PAN data in the image).
RUN mkdir -p models \
    && python scripts/train_baseline.py --no-pan \
    && useradd --create-home --uid 10001 guardian \
    && chown -R guardian:guardian /app/data /app/models

USER guardian
EXPOSE 8080

# One process on purpose: the stream engine, SSE broker and dedup state live in memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
