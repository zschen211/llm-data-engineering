"""Unified logging for data-factory.

Console-only, minimal: the pipeline driver emits structured progress via
``run_stages`` rows instead of log files. Every line identifies the emitting
file/line/function so log greps stay useful:

    2026-08-16 10:00:00 | INFO    | [data-factory] | executor.py:142:run_workflow | msg

Entry points (CLI, example scripts) call ``setup_logging`` once; modules use
``get_logger(__name__)``.
"""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | [data-factory] | %(filename)s:%(lineno)d:%(funcName)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str | int | None = None, stream=None) -> logging.Logger:
    """Configure the data-factory logger namespace once.

    ``level``: logging level; defaults to ``$DFAC_LOG_LEVEL`` or INFO.
    ``stream``: console sink (default stderr); tests pass a StringIO.
    """
    global _configured
    logger = logging.getLogger("data_factory")
    logger.setLevel(level or os.environ.get("DFAC_LOG_LEVEL", "INFO"))
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(handler)
    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Logger for a module, lazily configured if no entry point set it up."""
    if not _configured:
        setup_logging()
    return logging.getLogger(f"data_factory.{name}")
