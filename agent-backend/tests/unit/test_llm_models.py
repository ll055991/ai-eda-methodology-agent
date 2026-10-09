"""LLM value type tests: optional fields stay None (unknown != 0)."""

from dataclasses import FrozenInstanceError

import pytest

from agent_backend.llm.models import (
    ChatMessage,
    ModelRequest,
    ModelResponse,
    ModelTrace,
    ModelUsage,
)


class TestChatMessage:
    def test_valid_roles(self) -> None:
        for role in ("system", "user", "assistant"):
            assert ChatMessage(role=role, content="x").role == role  # type: ignore[arg-type]

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="role"):
            ChatMessage(role="function", content="x")  # type: ignore[arg-type]

    def test_tool_role_requires_tool_call_id(self) -> None:
        with pytest.raises(ValueError, match="tool_call_id"):
            ChatMessage(role="tool", content="{}")

    def test_tool_message_with_call_id(self) -> None:
        message = ChatMessage(role="tool", content="{}", tool_call_id="call_1")
        assert message.tool_call_id == "call_1"

    def test_assistant_message_can_carry_tool_calls(self) -> None:
        from agent_backend.tools.models import ToolCall

        call = ToolCall(id="c1", name="get_run", arguments={})
        message = ChatMessage(role="assistant", content="", tool_calls=(call,))
        assert message.tool_calls == (call,)

    def test_model_request_tools_default_empty(self) -> None:
        request = ModelRequest(model="m", messages=(ChatMessage("user", "hi"),))
        assert request.tools == ()

    def test_response_with_tool_calls_and_empty_content(self) -> None:
        from agent_backend.tools.models import ToolCall

        response = ModelResponse(
            content="",
            provider="fake",
            tool_calls=(ToolCall(id="c1", name="get_run", arguments={}),),
        )
        assert response.tool_calls[0].name == "get_run"

    def test_response_without_content_or_tools_rejected(self) -> None:
        with pytest.raises(ValueError, match="content or tool calls"):
            ModelResponse(content="  ", provider="fake")


class TestModelRequest:
    def test_minimal_request(self) -> None:
        request = ModelRequest(model="m", messages=(ChatMessage("user", "hi"),))
        assert request.temperature is None
        assert request.max_tokens is None

    def test_empty_model_rejected(self) -> None:
        with pytest.raises(ValueError, match="model"):
            ModelRequest(model=" ", messages=(ChatMessage("user", "hi"),))

    def test_empty_messages_rejected(self) -> None:
        with pytest.raises(ValueError, match="messages"):
            ModelRequest(model="m", messages=())


class TestModelResponse:
    def test_optional_fields_default_to_none(self) -> None:
        response = ModelResponse(content="ok", provider="fake")
        assert response.model is None
        assert response.finish_reason is None
        assert response.usage is None
        assert response.request_id is None

    def test_usage_fields_default_to_none_not_zero(self) -> None:
        usage = ModelUsage()
        assert usage.prompt_tokens is None
        assert usage.completion_tokens is None
        assert usage.total_tokens is None

    def test_trace_is_frozen(self) -> None:
        trace = ModelTrace(
            provider="fake",
            model="m",
            latency_ms=12,
            request_id=None,
            usage=None,
            prompt_version="v1",
        )
        with pytest.raises(FrozenInstanceError):
            trace.latency_ms = 13  # type: ignore[misc]
