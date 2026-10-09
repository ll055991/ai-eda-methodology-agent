"""MCP adapter errors (M7.2).

The MCP server is an adapter, so its failures are adapter failures. Three
kinds must stay strictly apart - conflating them would turn "the Platform
said no" into "the Platform is down" (or worse, into a success):

    PlatformUnavailableError  transport failure (refused / timeout / DNS)
    PlatformApiError          the Platform answered with an error document
                              (code/message/details are passed through verbatim)
    PlatformResponseError     the Platform answered something unusable

Resource failures are separate again (:class:`ResourceError` family) because
a bad URI is a caller mistake, not a Platform problem.

MCP protocol errors are never raised from here: a tool that cannot answer
returns a structured ``isError`` result (see ``aemp.mcp.tools``), so the
model sees an honest, switchable error code instead of a stack trace.
"""

from __future__ import annotations

from typing import Any

from aemp.core.errors import AEMPError

__all__ = [
    "McpAdapterError",
    "PlatformApiError",
    "PlatformResponseError",
    "PlatformUnavailableError",
    "ResourceError",
    "ResourceNotFoundError",
    "ResourcePathError",
    "ResourceTooLargeError",
    "ToolArgumentsInvalidError",
    "UnknownToolError",
]


class McpAdapterError(AEMPError):
    """Base class for every expected MCP-adapter failure."""


class PlatformUnavailableError(McpAdapterError):
    """The Platform HTTP API could not be reached (transport level)."""


class PlatformApiError(McpAdapterError):
    """The Platform answered with its error contract.

    Attributes:
        code: Platform error code (e.g. ``RUN_NOT_FOUND``), never invented.
        message: Platform message, bounded.
        details: Platform machine-readable details, passed through.
        http_status: The HTTP status the Platform used.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        http_status: int = 0,
    ) -> None:
        super().__init__(f"{code}: {message}"[:4000])
        self.code = code
        self.message = message[:4000]
        self.details = dict(details or {})
        self.http_status = http_status

    def to_document(self) -> dict[str, Any]:
        """The Platform error contract, unchanged."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class PlatformResponseError(McpAdapterError):
    """The Platform response was not the JSON object the contract promises."""


class UnknownToolError(McpAdapterError):
    """A tool name that this server does not expose (never created on demand)."""


class ToolArgumentsInvalidError(McpAdapterError):
    """Arguments failed the tool's own JSON Schema (checked before any call)."""


class ResourceError(McpAdapterError):
    """Base class for resource-layer failures."""


class ResourceNotFoundError(ResourceError):
    """The URI is valid but names nothing this server exposes."""


class ResourcePathError(ResourceError):
    """The URI is malformed, out of the allowlist, or escapes its root."""


class ResourceTooLargeError(ResourceError):
    """The resource exists but exceeds the configured size limit."""
