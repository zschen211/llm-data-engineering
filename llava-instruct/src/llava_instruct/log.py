"""Unified logging for llava-instruct.

Every log line uses one format that identifies the emitting file, line and
function:

    2026-08-10 10:00:00 | INFO    | [llava-instruct] | store.py:142:sync_source | message

Entry points (CLI main, web app, scripts) call ``setup_logging`` once; modules
use ``get_logger(__name__)``.
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | [llava-instruct] | %(filename)s:%(lineno)d:%(funcName)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int | str = logging.INFO, stream=None) -> logging.Logger:
    """Configure the llava-instruct logger namespace once."""
    logger = logging.getLogger("llava_instruct")
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"llava_instruct.{name}")
