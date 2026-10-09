"""Logging setup for aemp.

Console logging for the CLI now. Run-scoped file logging (M1) will be
built on the same ``aemp`` logger namespace, so modules should always log
via :func:`get_logger` and never configure handlers themselves.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOGGER_NAME = "aemp"

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger inside the ``aemp`` namespace.

    Args:
        name: Usually the caller's ``__name__``. Names outside the
            ``aemp`` hierarchy are re-rooted under ``aemp.`` so that
            handler configuration stays in one place.
    """
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def setup_logging(
    level: int | str = logging.INFO, log_file: Path | None = None
) -> logging.Logger:
    """Configure the ``aemp`` logger and return it.

    Installs a stderr console handler at *level* and, when *log_file* is
    given, an additional DEBUG-level file handler (the seam future
    run-scoped logging will use). Safe to call repeatedly: existing
    ``aemp`` handlers are replaced, not duplicated.

    Args:
        level: Minimum severity for the console handler. The CLI defaults
            to INFO; pass ``logging.DEBUG`` for verbose mode.
        log_file: Optional path that receives the same records at DEBUG
            level.

    Returns:
        The configured ``aemp`` logger.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(console)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(file_handler)

    return logger
