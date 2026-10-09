"""ToolRegistry tests: allowlist discipline (no dynamic tools, ever)."""

import pytest

from agent_backend.tools.errors import ToolNotFoundError
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.registry import ToolRegistry


class EchoTool:
    def __init__(self, name: str = "echo") -> None:
        self.definition = ToolDefinition(name=name, description="Echo tool.")

    def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult.success(call, call.arguments)


def test_register_get_list_definitions() -> None:
    registry = ToolRegistry()
    tool = EchoTool()
    registry.register(tool)
    assert registry.get("echo") is tool
    assert registry.list() == (tool,)
    assert [definition.name for definition in registry.definitions()] == ["echo"]


def test_duplicate_registration_rejected() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


def test_unknown_tool_rejected() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError, match="unknown tool"):
        registry.get("execute_shell")


def test_registry_is_empty_by_default() -> None:
    registry = ToolRegistry()
    assert registry.definitions() == ()
