"""Logging for the Agent Backend (independent of the Platform logger)."""

from __future__ import annotations

import logging
import sys

LOGGER_NAME = "agent_backend"

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger inside the ``agent_backend`` namespace."""
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def setup_logging(level: int | str = logging.INFO) -> logging.Logger:
    """Configure the ``agent_backend`` logger with a stderr handler."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(console)
    return logger
