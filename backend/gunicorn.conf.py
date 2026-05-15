"""
Gunicorn configuration for production.

Run via:
    gunicorn -c gunicorn.conf.py app:app
"""
import multiprocessing
import os

bind = os.getenv("BIND", "0.0.0.0:8000")
workers = int(os.getenv("WEB_CONCURRENCY", str(max(2, multiprocessing.cpu_count()))))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "600"))
graceful_timeout = 30
keepalive = 5
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = 100

# Logging is owned by the app via logging_config; gunicorn just forwards.
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Forwarded headers: trust the immediate proxy. Adjust if you terminate TLS
# elsewhere or have multiple proxy hops.
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
proxy_protocol = False
