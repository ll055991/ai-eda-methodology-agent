"""Agent API error model (contract: shared ``error.json``).

The Agent Backend reuses the shared error document shape; the codes used
here (INVALID_REQUEST / INTERNAL_ERROR) are part of that enum. Tracebacks
and internal exception texts never reach clients.
"""

from __future__ import annotations

from typing import Any

INVALID_REQUEST = "INVALID_REQUEST"
INTERNAL_ERROR = "INTERNAL_ERROR"
APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
APPROVAL_ALREADY_RESOLVED = "APPROVAL_ALREADY_RESOLVED"
APPROVAL_MISMATCH = "APPROVAL_MISMATCH"

_HTTP_STATUS = {
    INVALID_REQUEST: 400,
    INTERNAL_ERROR: 500,
    APPROVAL_NOT_FOUND: 404,
    APPROVAL_ALREADY_RESOLVED: 409,
    APPROVAL_MISMATCH: 400,
}

__all__ = [
    "APPROVAL_ALREADY_RESOLVED",
    "APPROVAL_MISMATCH",
    "APPROVAL_NOT_FOUND",
    "INVALID_REQUEST",
    "INTERNAL_ERROR",
    "AgentError",
    "error_document",
]


class AgentError(Exception):
    """A stable Agent API error (never a raw internal exception)."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        if code not in _HTTP_STATUS:
            raise ValueError(f"Unknown agent error code: {code!r}")
        super().__init__(message[:4000])
        self.code = code
        self.message = message[:4000]
        self.details = dict(details or {})

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]


def error_document(error: AgentError) -> dict[str, Any]:
    """Render an :class:`AgentError` as the shared error document."""
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    }
