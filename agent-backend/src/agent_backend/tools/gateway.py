"""ToolGateway: the runtime's only door to any tool.

The LLM runtime depends on this protocol only - it never sees the
registry internals, the platform client, HTTP or any vendor SDK.

``RegistryToolGateway`` enforces the allowlist and validates arguments
against each tool's JSON Schema BEFORE execution. Unknown tools and bad
arguments become structured error results (fed back to the model), never
exceptions and never dynamic tool creation.
"""

from __future__ import annotations

from typing import Any, Protocol

from jsonschema import Draft202012Validator

from agent_backend.logging import get_logger
from agent_backend.tools.errors import ToolError, ToolNotFoundError
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.registry import ToolRegistry

__all__ = ["RegistryToolGateway", "ToolGateway"]

logger = get_logger(__name__)

_MAX_DETAIL_CHARS = 500


class ToolGateway(Protocol):
    """What the agent runtime is allowed to know about tools."""

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Tool schemas to advertise to the model."""
        ...

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Validate and execute one tool call."""
        ...


class RegistryToolGateway:
    """Gateway over an allowlist :class:`ToolRegistry`."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._registry.definitions()

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            tool = self._registry.get(tool_call.name)
        except ToolNotFoundError:
            return ToolResult.error(
                tool_call, "TOOL_NOT_FOUND", f"unknown tool: {tool_call.name}"
            )

        invalid = _first_schema_error(
            tool.definition.input_schema, tool_call.arguments
        )
        if invalid is not None:
            return ToolResult.error(tool_call, "TOOL_ARGUMENTS_INVALID", invalid)

        try:
            return tool.execute(tool_call)
        except ToolError as exc:
            return ToolResult.error(tool_call, "TOOL_EXECUTION_ERROR", str(exc))
        except Exception as exc:  # never let a tool crash the agent loop
            logger.exception("tool %s crashed", tool_call.name)
            return ToolResult.error(
                tool_call, "TOOL_EXECUTION_ERROR", type(exc).__name__
            )


def _first_schema_error(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Return a bounded validation message, or None when arguments are valid."""
    errors = sorted(
        Draft202012Validator(schema).iter_errors(arguments),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    first = next(iter(errors), None)
    if first is None:
        return None
    path = ".".join(str(part) for part in first.absolute_path) or "<arguments>"
    return f"{path}: {first.message}"[:_MAX_DETAIL_CHARS]
