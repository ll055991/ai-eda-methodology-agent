"""Minimal agent loop tests (M6.4): LLM -> Tool -> LLM -> Final.

Scripted providers and fake tools; no network, no platform.
"""

from typing import Any

from agent_backend.application.chat import LLMAgentRuntime
from agent_backend.llm.errors import LLMProviderError
from agent_backend.llm.models import ModelRequest, ModelResponse
from agent_backend.models.chat import CHAT_STATUS_COMPLETED, CHAT_STATUS_FAILED, ChatRequest
from agent_backend.tools.errors import ToolExecutionError
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.registry import ToolRegistry

SCHEMA = {
    "type": "object",
    "required": ["limit"],
    "additionalProperties": False,
    "properties": {"limit": {"type": "integer", "minimum": 1}},
}


class ScriptedProvider:
    """Returns canned responses in order; repeats the last one forever."""

    name = "scripted"

    def __init__(self, responses: list[ModelResponse], *, error: Exception | None = None):
        self._responses = list(responses)
        self._error = error
        self.requests: list[ModelRequest] = []

    def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]


class FakeTool:
    def __init__(
        self,
        name: str,
        *,
        schema: dict[str, Any] | None = None,
        error: Exception | None = None,
        data: Any | None = None,
    ) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"{name} tool",
            input_schema=schema
            or {"type": "object", "additionalProperties": False, "properties": {}},
        )
        self.calls: list[ToolCall] = []
        self._error = error
        self._data = data

    def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if self._error is not None:
            raise self._error
        return ToolResult.success(call, self._data if self._data is not None else {})


def make_gateway(*tools: FakeTool) -> RegistryToolGateway:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return RegistryToolGateway(registry)


def tool_response(
    name: str, arguments: dict[str, Any] | None = None, call_id: str = "call_1"
) -> ModelResponse:
    return ModelResponse(
        content="",
        provider="scripted",
        tool_calls=(
            ToolCall(id=call_id, name=name, arguments=arguments or {}),
        ),
    )


def final(content: str = "final answer") -> ModelResponse:
    return ModelResponse(content=content, provider="scripted")


def make_runtime(
    provider: ScriptedProvider,
    gateway: RegistryToolGateway | None = None,
    *,
    max_rounds: int = 3,
) -> LLMAgentRuntime:
    return LLMAgentRuntime(
        provider=provider,
        model="fake-model",
        gateway=gateway,
        max_tool_rounds=max_rounds,
    )


class TestCase1PlainAnswer:
    def test_single_llm_call_no_tools(self) -> None:
        provider = ScriptedProvider([final("hello")])
        response = make_runtime(provider).handle(ChatRequest(message="hi"))
        assert response.status == CHAT_STATUS_COMPLETED
        assert response.message == "hello"
        assert len(provider.requests) == 1
        assert provider.requests[0].tools == ()

    def test_tools_advertised_when_gateway_present(self) -> None:
        provider = ScriptedProvider([final("hello")])
        gateway = make_gateway(FakeTool("list_runs"))
        make_runtime(provider, gateway).handle(ChatRequest(message="hi"))
        assert [tool.name for tool in provider.requests[0].tools] == ["list_runs"]


class TestCase2OneToolRound:
    def test_two_llm_calls_one_tool(self) -> None:
        tool = FakeTool("list_runs", data={"runs": []})
        provider = ScriptedProvider([tool_response("list_runs"), final("done")])
        response = make_runtime(provider, make_gateway(tool)).handle(
            ChatRequest(message="list runs")
        )

        assert response.status == CHAT_STATUS_COMPLETED
        assert response.message == "done"
        assert len(provider.requests) == 2
        assert len(tool.calls) == 1

        messages = provider.requests[1].messages
        assert [message.role for message in messages] == [
            "system",
            "user",
            "assistant",
            "tool",
        ]
        assert messages[2].tool_calls[0].name == "list_runs"
        assert messages[3].tool_call_id == "call_1"
        assert '"runs"' in messages[3].content


class TestCase3TwoToolRounds:
    def test_three_llm_calls_two_tools(self) -> None:
        tool = FakeTool("list_runs")
        provider = ScriptedProvider(
            [tool_response("list_runs"), tool_response("list_runs", call_id="call_2"), final()]
        )
        make_runtime(provider, make_gateway(tool)).handle(ChatRequest(message="go"))
        assert len(provider.requests) == 3
        assert len(tool.calls) == 2


class TestCase4ToolFailureFeedback:
    def test_error_result_is_fed_back_and_llm_recovers(self) -> None:
        tool = FakeTool("list_runs", error=ToolExecutionError("backend down"))
        provider = ScriptedProvider([tool_response("list_runs"), final("recovered")])
        response = make_runtime(provider, make_gateway(tool)).handle(
            ChatRequest(message="go")
        )
        assert response.status == CHAT_STATUS_COMPLETED
        tool_message = provider.requests[1].messages[-1]
        assert tool_message.role == "tool"
        assert "TOOL_EXECUTION_ERROR" in tool_message.content


class TestCase5UnknownTool:
    def test_unknown_tool_rejected_and_fed_back(self) -> None:
        provider = ScriptedProvider(
            [tool_response("execute_shell", {"cmd": "rm -rf /"}), final("blocked")]
        )
        response = make_runtime(provider, make_gateway(FakeTool("list_runs"))).handle(
            ChatRequest(message="go")
        )
        assert response.status == CHAT_STATUS_COMPLETED
        tool_message = provider.requests[1].messages[-1]
        assert "TOOL_NOT_FOUND" in tool_message.content


class TestCase6InvalidArguments:
    def test_invalid_arguments_rejected_before_execution(self) -> None:
        tool = FakeTool("list_runs", schema=SCHEMA)
        provider = ScriptedProvider(
            [tool_response("list_runs", {"limit": "abc"}), final("fixed")]
        )
        make_runtime(provider, make_gateway(tool)).handle(ChatRequest(message="go"))
        assert tool.calls == []
        tool_message = provider.requests[1].messages[-1]
        assert "TOOL_ARGUMENTS_INVALID" in tool_message.content


class TestCase7RoundLimit:
    def test_round_limit_fails_cleanly(self) -> None:
        tool = FakeTool("list_runs")
        provider = ScriptedProvider([tool_response("list_runs")])  # forever
        response = make_runtime(provider, make_gateway(tool), max_rounds=3).handle(
            ChatRequest(message="loop")
        )
        assert response.status == CHAT_STATUS_FAILED
        assert "maximum tool-call rounds" in response.message
        assert len(provider.requests) == 3
        assert len(tool.calls) == 3


class TestCase8MultipleToolCalls:
    def test_multiple_calls_execute_sequentially_in_order(self) -> None:
        tools = [FakeTool("tool_a"), FakeTool("tool_b"), FakeTool("tool_c")]
        multi = ModelResponse(
            content="",
            provider="scripted",
            tool_calls=(
                ToolCall(id="c1", name="tool_a", arguments={}),
                ToolCall(id="c2", name="tool_b", arguments={}),
                ToolCall(id="c3", name="tool_c", arguments={}),
            ),
        )
        provider = ScriptedProvider([multi, final("all done")])
        response = make_runtime(provider, make_gateway(*tools)).handle(
            ChatRequest(message="go")
        )

        assert response.status == CHAT_STATUS_COMPLETED
        executed = [
            call.name for tool in tools for call in tool.calls
        ]
        assert executed == ["tool_a", "tool_b", "tool_c"]
        tool_messages = [
            message
            for message in provider.requests[1].messages
            if message.role == "tool"
        ]
        assert [message.tool_call_id for message in tool_messages] == ["c1", "c2", "c3"]


class TestGuards:
    def test_tool_call_without_gateway_fails_honestly(self) -> None:
        provider = ScriptedProvider([tool_response("list_runs")])
        response = make_runtime(provider).handle(ChatRequest(message="go"))
        assert response.status == CHAT_STATUS_FAILED
        assert "no tools are available" in response.message
        assert len(provider.requests) == 1

    def test_provider_error_still_fails(self) -> None:
        provider = ScriptedProvider([], error=LLMProviderError("boom"))
        response = make_runtime(provider).handle(ChatRequest(message="go"))
        assert response.status == CHAT_STATUS_FAILED
        assert "LLMProviderError" in response.message
