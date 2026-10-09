"""HITL runtime tests (M6.6): policy -> interrupt -> approve/reject -> resume.

Covers the M6.6 acceptance matrix: low-risk bypass, high-risk interrupt
with zero execution, approve exactly-once, reject zero-execution, replay
protection, tool-call binding, thread binding, unknown approvals and
checkpoint-backed state resume.
"""


import pytest
from langgraph.types import Command

from agent_backend.application.approval import (
    APPROVAL_ALREADY_RESOLVED,
    APPROVAL_MISMATCH,
    APPROVAL_NOT_FOUND,
    ApprovalError,
    ApprovalManager,
)
from agent_backend.application.langgraph_runtime import LangGraphAgentRuntime
from agent_backend.llm.models import ModelRequest, ModelResponse
from agent_backend.models.chat import (
    CHAT_STATUS_COMPLETED,
    CHAT_STATUS_INTERRUPTED,
    ChatRequest,
)
from agent_backend.policy import PolicyEngine
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.registry import ToolRegistry

SESSION = "sess-hitl"


class ScriptedProvider:
    name = "scripted"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]


class FakeTool:
    def __init__(self, name: str) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"{name} tool",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        )
        self.calls: list[ToolCall] = []

    def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult.success(call, {"ok": True})


def tool_response(name: str, call_id: str = "call_1") -> ModelResponse:
    return ModelResponse(
        content="",
        provider="scripted",
        tool_calls=(ToolCall(id=call_id, name=name, arguments={}),),
    )


def final(content: str = "final answer") -> ModelResponse:
    return ModelResponse(content=content, provider="scripted")


def make_runtime(
    provider: ScriptedProvider, tool: FakeTool
) -> LangGraphAgentRuntime:
    registry = ToolRegistry()
    registry.register(tool)
    return LangGraphAgentRuntime(
        provider=provider,
        model="fake-model",
        gateway=RegistryToolGateway(registry),
        max_tool_rounds=3,
        policy=PolicyEngine.default(),
        approvals=ApprovalManager(),
    )


class TestLowRisk:
    def test_case1_low_risk_executes_without_interrupt(self) -> None:
        tool = FakeTool("get_run")
        provider = ScriptedProvider([tool_response("get_run"), final("done")])
        runtime = make_runtime(provider, tool)

        response = runtime.handle(ChatRequest(message="go", session_id=SESSION))

        assert response.status == CHAT_STATUS_COMPLETED
        assert response.approval_required is False
        assert response.approval_id is None
        assert len(tool.calls) == 1


class TestHighRiskInterrupt:
    def test_case2_high_risk_interrupts_before_execution(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider([tool_response("run_flow")])
        runtime = make_runtime(provider, tool)

        response = runtime.handle(ChatRequest(message="run it", session_id=SESSION))

        assert response.status == CHAT_STATUS_INTERRUPTED
        assert response.approval_required is True
        assert response.approval_id is not None
        assert response.session_id == SESSION
        assert tool.calls == []  # nothing executed before approval
        assert len(provider.requests) == 1  # no extra LLM round either

    def test_case2b_repeated_chat_returns_same_pending_approval(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider([tool_response("run_flow")])
        runtime = make_runtime(provider, tool)

        first = runtime.handle(ChatRequest(message="run it", session_id=SESSION))
        second = runtime.handle(ChatRequest(message="again", session_id=SESSION))

        assert second.status == CHAT_STATUS_INTERRUPTED
        assert second.approval_id == first.approval_id
        assert tool.calls == []
        assert len(provider.requests) == 1  # the graph was not re-run


class TestApprove:
    def test_case3_approve_executes_exactly_once(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider(
            [tool_response("run_flow"), final("flow executed")]
        )
        runtime = make_runtime(provider, tool)

        interrupted = runtime.handle(ChatRequest(message="go", session_id=SESSION))
        assert interrupted.approval_id is not None

        resumed = runtime.resume(
            session_id=SESSION,
            approval_id=interrupted.approval_id,
            decision="approved",
        )

        assert resumed.status == CHAT_STATUS_COMPLETED
        assert resumed.message == "flow executed"
        assert len(tool.calls) == 1  # exactly once
        assert len(provider.requests) == 2


class TestReject:
    def test_case4_reject_skips_execution_and_agent_answers(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider(
            [tool_response("run_flow"), final("flow was not approved")]
        )
        runtime = make_runtime(provider, tool)

        interrupted = runtime.handle(ChatRequest(message="go", session_id=SESSION))
        resumed = runtime.resume(
            session_id=SESSION,
            approval_id=interrupted.approval_id or "",
            decision="rejected",
            reason="not authorized",
        )

        assert resumed.status == CHAT_STATUS_COMPLETED
        assert tool.calls == []  # rejected tool never executes
        tool_message = provider.requests[1].messages[-1]
        assert tool_message.role == "tool"
        assert "APPROVAL_REJECTED" in tool_message.content
        assert "not authorized" in tool_message.content


class TestReplayProtection:
    def _interrupt(self, tool: FakeTool) -> tuple[LangGraphAgentRuntime, str]:
        provider = ScriptedProvider([tool_response("run_flow"), final()])
        runtime = make_runtime(provider, tool)
        interrupted = runtime.handle(ChatRequest(message="go", session_id=SESSION))
        return runtime, interrupted.approval_id or ""

    def test_case5_duplicate_approve_rejected(self) -> None:
        runtime, approval_id = self._interrupt(FakeTool("run_flow"))
        runtime.resume(session_id=SESSION, approval_id=approval_id, decision="approved")
        with pytest.raises(ApprovalError) as excinfo:
            runtime.resume(
                session_id=SESSION, approval_id=approval_id, decision="approved"
            )
        assert excinfo.value.code == APPROVAL_ALREADY_RESOLVED

    def test_case6_reject_then_approve_rejected(self) -> None:
        runtime, approval_id = self._interrupt(FakeTool("run_flow"))
        runtime.resume(session_id=SESSION, approval_id=approval_id, decision="rejected")
        with pytest.raises(ApprovalError) as excinfo:
            runtime.resume(
                session_id=SESSION, approval_id=approval_id, decision="approved"
            )
        assert excinfo.value.code == APPROVAL_ALREADY_RESOLVED


class TestBinding:
    def test_case7_mismatched_approval_id_never_executes(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider([tool_response("run_flow"), final("blocked")])
        runtime = make_runtime(provider, tool)

        runtime.handle(ChatRequest(message="go", session_id=SESSION))
        # Direct resume with a payload that does not match this tool call's
        # pending approval: the manager stays pending and the tool must not run.
        runtime.graph.invoke(
            Command(resume={"approval_id": "approval_wrong", "decision": "approved"}),
            {"configurable": {"thread_id": SESSION}},
        )

        assert tool.calls == []
        tool_message = provider.requests[1].messages[-1]
        assert "APPROVAL_MISMATCH" in tool_message.content

    def test_case8_unknown_approval(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider([tool_response("run_flow")])
        runtime = make_runtime(provider, tool)
        runtime.handle(ChatRequest(message="go", session_id=SESSION))

        with pytest.raises(ApprovalError) as excinfo:
            runtime.resume(
                session_id=SESSION,
                approval_id="approval_does_not_exist",
                decision="approved",
            )
        assert excinfo.value.code == APPROVAL_NOT_FOUND
        assert tool.calls == []

    def test_case9_wrong_session_rejected(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider([tool_response("run_flow")])
        runtime = make_runtime(provider, tool)
        interrupted = runtime.handle(ChatRequest(message="go", session_id=SESSION))

        with pytest.raises(ApprovalError) as excinfo:
            runtime.resume(
                session_id="another-thread",
                approval_id=interrupted.approval_id or "",
                decision="approved",
            )
        assert excinfo.value.code == APPROVAL_MISMATCH
        assert tool.calls == []


class TestCheckpointResume:
    def test_case10_state_survives_interrupt_and_resume(self) -> None:
        tool = FakeTool("run_flow")
        provider = ScriptedProvider([tool_response("run_flow"), final("done")])
        runtime = make_runtime(provider, tool)
        config = {"configurable": {"thread_id": SESSION}}

        interrupted = runtime.handle(ChatRequest(message="go", session_id=SESSION))
        snapshot = runtime.graph.get_state(config)
        assert snapshot.next  # the graph is genuinely paused

        runtime.resume(
            session_id=SESSION,
            approval_id=interrupted.approval_id or "",
            decision="approved",
        )
        snapshot = runtime.graph.get_state(config)
        assert not snapshot.next  # resumed to completion

        roles = [message.role for message in snapshot.values["messages"]]
        assert roles == ["system", "user", "assistant", "tool", "assistant"]


class TestToolErrorsRemainStructured:
    def test_tool_error_after_approval_is_structured(self) -> None:
        class FailingTool(FakeTool):
            def execute(self, call: ToolCall) -> ToolResult:
                self.calls.append(call)
                return ToolResult.error(call, "PLATFORM_API_ERROR", "boom")

        tool = FailingTool("run_flow")
        provider = ScriptedProvider(
            [tool_response("run_flow"), final("could not run")]
        )
        runtime = make_runtime(provider, tool)

        interrupted = runtime.handle(ChatRequest(message="go", session_id=SESSION))
        resumed = runtime.resume(
            session_id=SESSION,
            approval_id=interrupted.approval_id or "",
            decision="approved",
        )

        assert resumed.status == CHAT_STATUS_COMPLETED
        assert len(tool.calls) == 1
        tool_message = provider.requests[1].messages[-1]
        assert "PLATFORM_API_ERROR" in tool_message.content
