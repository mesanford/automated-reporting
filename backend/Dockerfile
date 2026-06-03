# Cloud Run-ready FastAPI image.
#
# Notes:
# - Binds to $PORT (Cloud Run injects this; defaults to 8000 for local docker).
# - Single uvicorn worker. SSE responses hold one connection per active chat,
#   so concurrency is tuned at the Cloud Run service level (--concurrency=80),
#   not by stacking gunicorn workers.
# - --proxy-headers so X-Forwarded-* from the Cloud Run proxy reach FastAPI.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Use sh -c so $PORT is expanded at container start.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*"]
