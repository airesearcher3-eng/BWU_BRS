"""
Structured logging configuration with rotation.

JSON-ish single-line records suitable for log aggregators; rotated by size
to keep disk use bounded. Re-uses uvicorn's loggers so HTTP access logs and
application logs share one format.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import config


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter — one line per record."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for attr in ("request_id", "user_id", "path", "method", "status"):
            if hasattr(record, attr):
                payload[attr] = getattr(record, attr)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(base_dir: str | Path) -> None:
    """Install console + rotating-file handlers on the root logger."""
    log_dir = Path(base_dir) / config.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    formatter = JsonFormatter()

    root = logging.getLogger()
    # Drop any handlers configured by uvicorn/imports so we own the format.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "brs.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Align uvicorn loggers with our handlers/format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [console, file_handler]
        lg.propagate = False
        lg.setLevel(level)
