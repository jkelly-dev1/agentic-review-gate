# Slim, non-root image with a healthcheck. Runs the FastAPI app with the
# offline mock provider by default; set env vars to light up the real paths.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_PROVIDER=mock \
    PORT=8000

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code.
COPY app ./app
COPY data ./data
COPY scripts ./scripts

# Non-root user; writable audit dir.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/audit \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness probe hits the app's /healthz.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
