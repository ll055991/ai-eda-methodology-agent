"""LangGraph runtime tests (M6.5): graph topology + graph-driven behavior.

The graph is explicit: START -> agent -> should_continue -> tools -> agent
-> ... -> END. These tests inspect the compiled topology and drive the
runtime with scripted providers and fake tools (no network, no platform).
"""

from typing import Any

from agent_backend.application.langgraph_runtime import (
    MAX_ROUNDS_MESSAGE,
    NO_TOOLS_MESSAGE,
    AgentState,
    LangGraphAgentRuntime,
)
from agent_backend.llm.errors import LLMProviderError
from agent_backend.llm.models import ChatMessage, ModelRequest, ModelResponse
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
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments or {}),),
    )


def final(content: str = "final answer") -> ModelResponse:
    return ModelResponse(content=content, provider="scripted")


def make_runtime(
    provider: ScriptedProvider,
    gateway: RegistryToolGateway | None = None,
    *,
    max_rounds: int = 3,
) -> LangGraphAgentRuntime:
    return LangGraphAgentRuntime(
        provider=provider,
        model="fake-model",
        gateway=gateway,
        max_tool_rounds=max_rounds,
    )


class TestGraphTopology:
    def test_nodes_and_edges(self) -> None:
        runtime = make_runtime(ScriptedProvider([final()]))
        topology = runtime.graph.get_graph()

        assert {"__start__", "__end__", "agent", "tools"} <= set(topology.nodes)
        edges = {(edge.source, edge.target): edge.conditional for edge in topology.edges}

        assert edges[("__start__", "agent")] is False
        assert edges[("agent", "tools")] is True  # conditional edge
        assert edges[("agent", "__end__")] is True  # conditional edge
        assert edges[("tools", "agent")] is False

    def test_no_broken_structures(self) -> None:
        topology = make_runtime(ScriptedProvider([final()])).graph.get_graph()
        edges = {(edge.source, edge.target) for edge in topology.edges}
        assert ("tools", "tools") not in edges
        assert ("__end__", "agent") not in edges
        assert ("__start__", "tools") not in edges


class TestStateTransitions:
    def test_plain_answer_state(self) -> None:
        provider = ScriptedProvider([final("hello")])
        runtime = make_runtime(provider)
        # M6.6: the runtime carries a checkpointer, so an explicit thread id
        # is required for direct graph.invoke (same as the runtime API does).
        result = runtime.graph.invoke(
            AgentState(
                messages=(ChatMessage("system", "s"), ChatMessage("user", "hi"))
            ),
            {"configurable": {"thread_id": "test-plain"}},
        )
        assert isinstance(result, dict)
        assert result["status"] == "completed"
        assert result["final_message"] == "hello"
        assert result["tool_round"] == 0

    def test_one_tool_round_state_and_message_sequence(self) -> None:
        tool = FakeTool("list_runs", data={"runs": []})
        provider = ScriptedProvider([tool_response("list_runs"), final("done")])
        runtime = make_runtime(provider, make_gateway(tool))

        result = runtime.graph.invoke(
            AgentState(
                messages=(ChatMessage("system", "s"), ChatMessage("user", "go"))
            ),
            {"configurable": {"thread_id": "test-tool-round"}},
        )
        assert result["status"] == "completed"
        assert result["tool_round"] == 1
        assert [message.role for message in result["messages"]] == [
            "system",
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        assert result["messages"][3].tool_call_id == "call_1"


class TestGraphDrivenBehavior:
    def test_case1_plain_answer(self) -> None:
        provider = ScriptedProvider([final()])
        response = make_runtime(provider).handle(ChatRequest(message="hi"))
        assert response.status == CHAT_STATUS_COMPLETED
        assert len(provider.requests) == 1

    def test_case2_one_tool_round(self) -> None:
        tool = FakeTool("list_runs")
        provider = ScriptedProvider([tool_response("list_runs"), final("done")])
        response = make_runtime(provider, make_gateway(tool)).handle(
            ChatRequest(message="go")
        )
        assert response.status == CHAT_STATUS_COMPLETED
        assert len(provider.requests) == 2
        assert len(tool.calls) == 1

    def test_case3_two_tool_rounds(self) -> None:
        tool = FakeTool("list_runs")
        provider = ScriptedProvider(
            [
                tool_response("list_runs"),
                tool_response("list_runs", call_id="call_2"),
                final(),
            ]
        )
        make_runtime(provider, make_gateway(tool)).handle(ChatRequest(message="go"))
        assert len(provider.requests) == 3
        assert len(tool.calls) == 2

    def test_case4_tool_failure_is_fed_back(self) -> None:
        tool = FakeTool("list_runs", error=ToolExecutionError("backend down"))
        provider = ScriptedProvider([tool_response("list_runs"), final("recovered")])
        response = make_runtime(provider, make_gateway(tool)).handle(
            ChatRequest(message="go")
        )
        assert response.status == CHAT_STATUS_COMPLETED
        tool_message = provider.requests[1].messages[-1]
        assert tool_message.role == "tool"
        assert "TOOL_EXECUTION_ERROR" in tool_message.content

    def test_case5_unknown_tool_goes_through_gateway(self) -> None:
        provider = ScriptedProvider(
            [tool_response("execute_shell", {"cmd": "x"}), final("blocked")]
        )
        make_runtime(provider, make_gateway(FakeTool("list_runs"))).handle(
            ChatRequest(message="go")
        )
        assert "TOOL_NOT_FOUND" in provider.requests[1].messages[-1].content

    def test_case6_invalid_arguments_rejected(self) -> None:
        tool = FakeTool("list_runs", schema=SCHEMA)
        provider = ScriptedProvider(
            [tool_response("list_runs", {"limit": "abc"}), final("fixed")]
        )
        make_runtime(provider, make_gateway(tool)).handle(ChatRequest(message="go"))
        assert tool.calls == []
        assert "TOOL_ARGUMENTS_INVALID" in provider.requests[1].messages[-1].content

    def test_case7_max_rounds_fails_cleanly(self) -> None:
        tool = FakeTool("list_runs")
        provider = ScriptedProvider([tool_response("list_runs")])  # forever
        response = make_runtime(provider, make_gateway(tool), max_rounds=3).handle(
            ChatRequest(message="loop")
        )
        assert response.status == CHAT_STATUS_FAILED
        assert response.message == MAX_ROUNDS_MESSAGE
        assert len(provider.requests) == 3
        assert len(tool.calls) == 3

    def test_case8_multiple_tool_calls_sequential(self) -> None:
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
        assert [call.name for tool in tools for call in tool.calls] == [
            "tool_a",
            "tool_b",
            "tool_c",
        ]


class TestBoundaries:
    def test_provider_error_fails(self) -> None:
        provider = ScriptedProvider([], error=LLMProviderError("boom"))
        response = make_runtime(provider).handle(ChatRequest(message="go"))
        assert response.status == CHAT_STATUS_FAILED
        assert "LLMProviderError" in response.message

    def test_tool_call_without_gateway_fails_honestly(self) -> None:
        provider = ScriptedProvider([tool_response("list_runs")])
        response = make_runtime(provider).handle(ChatRequest(message="go"))
        assert response.status == CHAT_STATUS_FAILED
        assert response.message == NO_TOOLS_MESSAGE
        assert len(provider.requests) == 1

    def test_runtime_does_not_hold_a_platform_client(self) -> None:
        runtime = make_runtime(ScriptedProvider([final()]))
        assert not hasattr(runtime, "_platform_client")
        assert not hasattr(runtime, "_platform")
