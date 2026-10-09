"""API error contract: stable codes, not Python exception texts.

The contract (``contracts/schemas/error.json``) defines the wire shape;
this module defines the codes, their HTTP status mapping for the future
HTTP server (M6.1), and the :class:`ApiError` the application service
raises. Internal exception messages are allowed in ``message`` for human
debugging, but callers must switch on ``code`` only.
"""

from __future__ import annotations

from typing import Any

from aemp.core.errors import AEMPError, InvalidRequestError

__all__ = [
    "API_ERROR_CODES",
    "API_ERROR_HTTP_STATUS",
    "ApiError",
    "error_document",
]

INVALID_REQUEST = "INVALID_REQUEST"
RUN_NOT_FOUND = "RUN_NOT_FOUND"
FLOW_NOT_FOUND = "FLOW_NOT_FOUND"
FLOW_VALIDATION_FAILED = "FLOW_VALIDATION_FAILED"
TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
TOOL_ENVIRONMENT_UNAVAILABLE = "TOOL_ENVIRONMENT_UNAVAILABLE"
QA_EVALUATION_FAILED = "QA_EVALUATION_FAILED"
ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
INTERNAL_ERROR = "INTERNAL_ERROR"

API_ERROR_CODES = (
    INVALID_REQUEST,
    RUN_NOT_FOUND,
    FLOW_NOT_FOUND,
    FLOW_VALIDATION_FAILED,
    TOOL_EXECUTION_FAILED,
    TOOL_ENVIRONMENT_UNAVAILABLE,
    QA_EVALUATION_FAILED,
    ARTIFACT_NOT_FOUND,
    INTERNAL_ERROR,
)

API_ERROR_HTTP_STATUS = {
    INVALID_REQUEST: 400,
    RUN_NOT_FOUND: 404,
    FLOW_NOT_FOUND: 404,
    FLOW_VALIDATION_FAILED: 422,
    TOOL_EXECUTION_FAILED: 500,
    TOOL_ENVIRONMENT_UNAVAILABLE: 503,
    QA_EVALUATION_FAILED: 500,
    ARTIFACT_NOT_FOUND: 404,
    INTERNAL_ERROR: 500,
}


class ApiError(AEMPError):
    """A contract-level API error (never a raw internal exception).

    Attributes:
        code: One of :data:`API_ERROR_CODES`.
        message: Human-readable, bounded text (max 4000 chars).
        details: Optional machine-readable extras (never exception reprs).
    """

    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        if code not in API_ERROR_CODES:
            msg = f"Unknown API error code: {code!r}"
            raise InvalidRequestError(msg)
        super().__init__(message[:4000])
        self.code = code
        self.message = message[:4000]
        self.details = dict(details or {})

    @property
    def http_status(self) -> int:
        """HTTP status for this error code (used by the M6.1 HTTP server)."""
        return API_ERROR_HTTP_STATUS[self.code]


def error_document(error: ApiError) -> dict[str, Any]:
    """Render an :class:`ApiError` as the contract error document."""
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    }
