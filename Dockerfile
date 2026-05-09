# syntax=docker/dockerfile:1.7
# ── Builder ───────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt


# ── Runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENV=production \
    HOST=0.0.0.0 \
    PORT=8000

# Non-root user
RUN groupadd --system --gid 1000 brs \
 && useradd --system --uid 1000 --gid brs --create-home --home-dir /home/brs brs \
 && apt-get update \
 && apt-get install -y --no-install-recommends curl tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=brs:brs . /app

# App-managed runtime dirs (also mounted as volumes in compose)
RUN mkdir -p /app/uploads /app/output /app/db /app/logs \
 && chown -R brs:brs /app

USER brs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
