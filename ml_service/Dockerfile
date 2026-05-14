# ── Build ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
# All dependencies are pure-Python / API-based — no local ML model needed.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip --quiet && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN groupadd -g 1001 ml && useradd -u 1001 -g ml -m -s /bin/bash ml

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN apt-get update -qq && apt-get install -y --no-install-recommends tini && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --chown=ml:ml . .

USER ml
EXPOSE 8001

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
