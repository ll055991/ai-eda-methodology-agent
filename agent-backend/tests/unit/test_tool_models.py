"""Tool value type tests: definitions, calls, results, bounded serialization."""

import json

import pytest

from agent_backend.tools.models import (
    MAX_TOOL_RESULT_BYTES,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


class TestToolDefinition:
    def test_openai_schema_conversion(self) -> None:
        definition = ToolDefinition(
            name="list_runs",
            description="List runs.",
            input_schema={"type": "object", "properties": {}},
        )
        schema = definition.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "list_runs"
        assert schema["function"]["parameters"] == definition.input_schema

    @pytest.mark.parametrize("name", ["", "has space", "1starts_with_digit", "a-b"])
    def test_invalid_name_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="name"):
            ToolDefinition(name=name, description="d")

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValueError, match="description"):
            ToolDefinition(name="t", description="  ")


class TestToolCall:
    def test_valid_call(self) -> None:
        call = ToolCall(id="call_1", name="get_run", arguments={"run_id": "x"})
        assert call.arguments["run_id"] == "x"

    @pytest.mark.parametrize(
        ("call_id", "name", "arguments"),
        [
            ("", "get_run", {}),
            ("c", "", {}),
            ("c", "get_run", "not-a-dict"),
        ],
    )
    def test_invalid_calls_rejected(
        self, call_id: str, name: str, arguments: object
    ) -> None:
        with pytest.raises(ValueError):
            ToolCall(id=call_id, name=name, arguments=arguments)  # type: ignore[arg-type]


class TestToolResult:
    def test_success_serialization(self) -> None:
        call = ToolCall(id="c1", name="get_run", arguments={})
        result = ToolResult.success(call, {"run_id": "r"})
        document = result.to_dict()
        assert document == {
            "tool_call_id": "c1",
            "tool_name": "get_run",
            "status": "success",
            "data": {"run_id": "r"},
        }
        assert "error_code" not in document

    def test_error_requires_code(self) -> None:
        with pytest.raises(ValueError, match="error_code"):
            ToolResult(tool_call_id="c", tool_name="t", status="error")

    def test_success_must_not_carry_error_code(self) -> None:
        with pytest.raises(ValueError, match="success"):
            ToolResult(
                tool_call_id="c",
                tool_name="t",
                status="success",
                error_code="X",
            )

    def test_error_constructor_bounds_message(self) -> None:
        call = ToolCall(id="c1", name="t", arguments={})
        result = ToolResult.error(call, "PLATFORM_API_ERROR", "x" * 5000)
        assert result.error_code == "PLATFORM_API_ERROR"
        assert result.error_message is not None
        assert len(result.error_message) == 1000

    def test_small_result_not_truncated(self) -> None:
        call = ToolCall(id="c1", name="t", arguments={})
        content = ToolResult.success(call, {"ok": True}).to_message_content()
        document = json.loads(content)
        assert "truncated" not in document

    def test_large_result_is_bounded_and_flagged(self) -> None:
        call = ToolCall(id="c1", name="t", arguments={})
        big = {"blob": "x" * (MAX_TOOL_RESULT_BYTES * 2)}
        content = ToolResult.success(call, big).to_message_content()
        assert len(content.encode("utf-8")) <= MAX_TOOL_RESULT_BYTES
        document = json.loads(content)
        assert document["truncated"] is True
        assert document["status"] == "success"
        assert document["data"] is None
        assert document["data_preview"]
