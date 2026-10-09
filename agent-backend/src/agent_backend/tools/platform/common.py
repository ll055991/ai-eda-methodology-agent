"""Shared mapping for platform tools: client errors -> structured results."""

from __future__ import annotations

from agent_backend.platform.errors import (
    PlatformApiError,
    PlatformConnectionError,
    PlatformContractError,
)
from agent_backend.tools.models import ToolCall, ToolResult

__all__ = ["platform_error_result"]


def platform_error_result(call: ToolCall, exc: Exception) -> ToolResult:
    """Map a PlatformClient failure to a structured, secret-free result."""
    if isinstance(exc, PlatformConnectionError):
        return ToolResult.error(
            call, "PLATFORM_CONNECTION_ERROR", "Platform service is unavailable."
        )
    if isinstance(exc, PlatformApiError):
        return ToolResult.error(
            call,
            "PLATFORM_API_ERROR",
            f"{exc.code}: {exc.message}",
        )
    if isinstance(exc, PlatformContractError):
        return ToolResult.error(
            call, "PLATFORM_CONTRACT_ERROR", "Platform response violated its contract."
        )
    return ToolResult.error(call, "PLATFORM_ERROR", type(exc).__name__)
