"""Structured logging for Rick v6 runtime."""

from __future__ import annotations

import logging
import os
import sys


def get_logger(name: str = "rick") -> logging.Logger:
    """Return a named logger under the 'rick' namespace with consistent formatting."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        fmt = os.getenv("RICK_LOG_FORMAT", "%(asctime)s [%(name)s] %(levelname)s: %(message)s")
        handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)
        level = os.getenv("RICK_LOG_LEVEL", "WARNING").upper()
        logger.setLevel(getattr(logging, level, logging.WARNING))
    return logger
