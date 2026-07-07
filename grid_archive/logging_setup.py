"""Logging: every request URL and every 429 goes to the logfile (and stderr)."""

from __future__ import annotations

import logging
import sys

import config

_CONFIGURED = False


def get_logger() -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("grid_archive")
    if _CONFIGURED:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    _CONFIGURED = True
    return logger
