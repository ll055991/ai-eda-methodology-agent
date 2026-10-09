"""RegistryToolGateway tests: schema validation before execution."""

from typing import Any

from agent_backend.tools.errors import ToolExecutionError
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.registry import ToolRegistry

SCHEMA = {
    "type": "object",
    "required": ["limit"],
    "additionalProperties": False,
    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
}


class RecordingTool:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.definition = ToolDefinition(
            name="list_runs", description="List runs.", input_schema=SCHEMA
        )
        self.calls: list[ToolCall] = []
        self._error = error

    def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if self._error is not None:
            raise self._error
        return ToolResult.success(call, {"runs": []})


def make_gateway(tool: RecordingTool) -> RegistryToolGateway:
    registry = ToolRegistry()
    registry.register(tool)
    return RegistryToolGateway(registry)


def call(arguments: dict[str, Any], name: str = "list_runs") -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


class TestValidation:
    def test_valid_arguments_execute(self) -> None:
        tool = RecordingTool()
        result = make_gateway(tool).execute(call({"limit": 10}))
        assert result.status == "success"
        assert tool.calls[0].arguments == {"limit": 10}

    def test_missing_required_argument_rejected_before_execution(self) -> None:
        tool = RecordingTool()
        result = make_gateway(tool).execute(call({}))
        assert result.status == "error"
        assert result.error_code == "TOOL_ARGUMENTS_INVALID"
        assert "limit" in (result.error_message or "")
        assert tool.calls == []

    def test_wrong_type_rejected(self) -> None:
        tool = RecordingTool()
        result = make_gateway(tool).execute(call({"limit": "ten"}))
        assert result.error_code == "TOOL_ARGUMENTS_INVALID"
        assert tool.calls == []

    def test_extra_property_rejected(self) -> None:
        tool = RecordingTool()
        result = make_gateway(tool).execute(
            call({"limit": 10, "shell": "rm -rf /"})
        )
        assert result.error_code == "TOOL_ARGUMENTS_INVALID"
        assert tool.calls == []

    def test_unknown_tool_rejected(self) -> None:
        tool = RecordingTool()
        result = make_gateway(tool).execute(call({"limit": 1}, name="execute_shell"))
        assert result.status == "error"
        assert result.error_code == "TOOL_NOT_FOUND"
        assert tool.calls == []


class TestExecution:
    def test_tool_error_becomes_error_result(self) -> None:
        tool = RecordingTool(error=ToolExecutionError("backend down"))
        result = make_gateway(tool).execute(call({"limit": 1}))
        assert result.error_code == "TOOL_EXECUTION_ERROR"
        assert "backend down" in (result.error_message or "")

    def test_unexpected_exception_never_crashes_the_gateway(self) -> None:
        tool = RecordingTool(error=RuntimeError("boom"))
        result = make_gateway(tool).execute(call({"limit": 1}))
        assert result.error_code == "TOOL_EXECUTION_ERROR"
        assert result.error_message == "RuntimeError"
        assert "boom" not in (result.error_message or "")

    def test_definitions_passthrough(self) -> None:
        gateway = make_gateway(RecordingTool())
        assert [definition.name for definition in gateway.definitions()] == [
            "list_runs"
        ]
