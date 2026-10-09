"""MCP value types + the one schema adapter (M7.2, spec §22).

The MCP layer never invents its own tool vocabulary: what the server
advertises (``tools/list``) is converted into the agent's existing
``ToolDefinition`` and what the server returns (``structuredContent``) is
carried into the existing ``ToolResult``. One conversion, one direction,
no second schema maintained by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_backend.tools.models import ToolDefinition

__all__ = [
    "McpResourceContent",
    "McpResourceSpec",
    "McpServerSpec",
    "McpToolCallResult",
    "McpToolSpec",
]

_DEFAULT_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class McpServerSpec:
    """One MCP server the agent may talk to (multi-server ready, spec §28)."""

    name: str
    url: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("mcp server name must be a non-empty string")
        if not self.url.strip():
            raise ValueError("mcp server url must be a non-empty string")


@dataclass(frozen=True)
class McpToolSpec:
    """One tool as advertised by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    title: str | None = None
    server_name: str = ""

    def to_tool_definition(self) -> ToolDefinition:
        """The agent-side definition (validated by ``ToolDefinition``)."""
        return ToolDefinition(
            name=self.name,
            description=self.description or f"MCP tool '{self.name}'.",
            input_schema=dict(self.input_schema or _DEFAULT_INPUT_SCHEMA),
        )


@dataclass(frozen=True)
class McpResourceSpec:
    """One resource (or template) as advertised by an MCP server."""

    uri: str
    name: str
    description: str = ""
    mime_type: str = ""
    server_name: str = ""
    template: bool = False


@dataclass(frozen=True)
class McpToolCallResult:
    """One MCP tool result, normalized: structured first, text as fallback.

    ``is_error`` is the server's own ``isError`` flag - never reinterpreted.
    """

    is_error: bool
    structured: dict[str, Any] | None = None
    text: str | None = None

    def error_document(self) -> dict[str, Any] | None:
        """The Platform/MCP error contract carried in the structured payload."""
        if not isinstance(self.structured, dict):
            return None
        error = self.structured.get("error")
        return error if isinstance(error, dict) else None


@dataclass(frozen=True)
class McpResourceContent:
    """One read resource: bounded text and its media type."""

    uri: str
    mime_type: str
    text: str
