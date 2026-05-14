"""
Plain-text logging configuration with rotation.

Human-readable single-line records on stdout; rotated by size to keep disk
use bounded. Re-uses uvicorn's loggers so HTTP access logs and application
logs share one format.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

import config


_TEXT_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _RenameUvicornErrorFilter(logging.Filter):
    """Rename the misleadingly-named 'uvicorn.error' logger to 'uvicorn' in output."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return True


def configure_logging(base_dir: str | Path) -> None:
    """Install console + rotating-file handlers on the root logger."""
    log_dir = Path(base_dir) / config.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    formatter = logging.Formatter(_TEXT_FORMAT, datefmt=_DATE_FORMAT)
    rename_filter = _RenameUvicornErrorFilter()

    root = logging.getLogger()
    # Drop any handlers configured by uvicorn/imports so we own the format.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(rename_filter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "brs.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(rename_filter)
    root.addHandler(file_handler)

    # Align uvicorn loggers with our handlers/format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [console, file_handler]
        lg.propagate = False
        lg.setLevel(level)
