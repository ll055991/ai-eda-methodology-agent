"""AgentTool protocol: one allowlisted capability.

A tool maps structured arguments to a :class:`ToolResult`; it never
receives raw strings and never executes anything outside its declared
purpose. The gateway (not the tool) owns name/argument validation.
"""

from __future__ import annotations

from typing import Protocol

from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult

__all__ = ["AgentTool"]


class AgentTool(Protocol):
    """Structural interface every tool implements."""

    definition: ToolDefinition

    def execute(self, call: ToolCall) -> ToolResult:
        """Run the tool for one validated call.

        Raises:
            ToolError: On tool-layer failures (mapped to error results by
                the gateway).
        """
        ...
