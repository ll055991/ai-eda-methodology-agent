"""Tool-layer errors (registry / validation / execution)."""

from __future__ import annotations

__all__ = [
    "ToolError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolValidationError",
]


class ToolError(Exception):
    """Base class for tool-layer failures."""


class ToolNotFoundError(ToolError):
    """The requested tool is not in the allowlist registry."""


class ToolValidationError(ToolError):
    """Tool arguments do not satisfy the tool's JSON Schema."""


class ToolExecutionError(ToolError):
    """The tool failed while executing (unexpected internal failure)."""
