"""ToolRegistry: the allowlist of capabilities the model may request.

Only explicitly registered tools can ever execute; unknown names are
rejected (no dynamic tool creation, ever).
"""

from __future__ import annotations

from agent_backend.tools.base import AgentTool
from agent_backend.tools.errors import ToolNotFoundError
from agent_backend.tools.models import ToolDefinition

__all__ = ["ToolRegistry"]


class ToolRegistry:
    """Name -> tool mapping with duplicate/unknown protection."""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        """Add one tool; duplicate names are rejected."""
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"tool already registered: {name!r}")
        self._tools[name] = tool

    def get(self, name: str) -> AgentTool:
        """Return the tool or raise :class:`ToolNotFoundError`."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"unknown tool: {name!r}")
        return tool

    def list(self) -> tuple[AgentTool, ...]:
        return tuple(self._tools.values())

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """All tool definitions, in registration order."""
        return tuple(tool.definition for tool in self._tools.values())
