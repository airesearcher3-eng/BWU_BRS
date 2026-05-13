# ── Build ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
# 1) CPU-only PyTorch — prevents pip from pulling 2 GB CUDA wheels
# 2) Install all other requirements EXCEPT sentence-transformers
# 3) Install sentence-transformers last; torch is already present so pip
#    won't replace it, but all other deps (transformers, tokenizers, etc.)
#    are now resolved normally.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip --quiet && \
    /opt/venv/bin/pip install --no-cache-dir \
        torch --index-url https://download.pytorch.org/whl/cpu && \
    grep -v "sentence-transformers" requirements.txt | \
        /opt/venv/bin/pip install --no-cache-dir -r /dev/stdin && \
    /opt/venv/bin/pip install --no-cache-dir "sentence-transformers>=3.0.0"

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
