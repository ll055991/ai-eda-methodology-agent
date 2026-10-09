"""Stable exception hierarchy for aemp.

Keep this list short: every exception type here is part of the public
contract and is handled cleanly by the CLI. Do not add exception classes
unless callers genuinely need to distinguish them. Unexpected bugs should
NOT be converted into these types - they should crash loudly with a
traceback instead.
"""

from __future__ import annotations

__all__ = [
    "AEMPError",
    "ConfigurationError",
    "InvalidRequestError",
    "SchemaValidationError",
    "StorageError",
    "ToolNotFoundError",
]


class AEMPError(Exception):
    """Base class for all expected (handled) aemp errors.

    The CLI catches this base type and reports the message without a
    traceback.
    """


class ConfigurationError(AEMPError):
    """Invalid or missing configuration (files, paths, environment)."""


class SchemaValidationError(AEMPError):
    """A JSON instance failed schema validation.

    Attributes:
        path: Dot-separated path to the offending value inside the
            instance (e.g. ``data.metrics.wns``); empty when the error
            concerns the whole document.
        detail: Human-readable validation message from the validator.
    """

    def __init__(self, message: str, *, path: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.path = path
        self.detail = detail


class ToolNotFoundError(AEMPError):
    """A required external tool could not be located."""

    def __init__(self, tool: str, message: str | None = None) -> None:
        super().__init__(message or f"Tool not found: {tool}")
        self.tool = tool


class InvalidRequestError(AEMPError):
    """A request or command was malformed or used outside its supported scope."""


class StorageError(AEMPError):
    """A storage operation failed (run directory or SQLite index).

    The filesystem is the source of truth: a storage error on the index
    never means run data was lost.
    """
