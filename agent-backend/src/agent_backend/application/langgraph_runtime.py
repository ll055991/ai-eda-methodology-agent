"""LangGraph Agent Runtime (M6.5/M6.6): StateGraph + HITL.

    START -> agent_node -> should_continue --+--> END
                 ^                           |
                 |                           v
                 +--------- tool_node <------+
                              |
                        policy check (code-owned)
                              |
                   approval required?
                    ├── no  -> ToolGateway.execute()
                    └── yes -> interrupt() + checkpoint
                                   (human approve/reject)
                               -> resume -> ToolGateway.execute()
                               -> or APPROVAL_REJECTED tool result

M6.6 adds, without new agent capabilities:

- a deterministic PolicyEngine in front of every tool execution;
- LangGraph ``interrupt`` + ``InMemorySaver`` checkpointer so a paused
  execution can be resumed after a human decision;
- ``resume(session_id, approval_id, decision)`` bound to the exact
  ``(thread_id, tool_call_id)`` pair.

The graph is still orchestration only: ``agent_node`` talks to the
ModelProvider, ``tool_node`` talks to the PolicyEngine, the ApprovalManager
and the ToolGateway - never to the platform client, HTTP or the filesystem.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_backend.application.approval import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    ApprovalError,
    ApprovalManager,
    ApprovalRequest,
)
from agent_backend.llm.base import ModelProvider
from agent_backend.llm.errors import LLMError
from agent_backend.llm.models import ChatMessage, ModelRequest, ModelTrace
from agent_backend.logging import get_logger
from agent_backend.models.chat import (
    CHAT_STATUS_COMPLETED,
    CHAT_STATUS_FAILED,
    CHAT_STATUS_INTERRUPTED,
    ChatRequest,
    ChatResponse,
)
from agent_backend.policy import PolicyEngine
from agent_backend.skills import SkillService
from agent_backend.tools.gateway import ToolGateway
from agent_backend.tools.models import ToolCall, ToolResult

__all__ = [
    "MAX_ROUNDS_MESSAGE",
    "NO_TOOLS_MESSAGE",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "AgentState",
    "LangGraphAgentRuntime",
    "build_agent_graph",
    "default_checkpointer",
]

logger = get_logger(__name__)

PROMPT_VERSION = "eda-methodology-assistant/v2"

# The prompt only STATES the rules; the real security boundary is the
# policy + tool registry + JSON Schema validation + gateway + Platform API.
SYSTEM_PROMPT = (
    "You are an EDA Methodology assistant. "
    "You may use only the tools explicitly provided to you. "
    "Do not invent tools. "
    "Do not execute shell commands. "
    "Do not access files directly. "
    "Use tools when information must be retrieved from the platform. "
    "After receiving tool results, answer based on those results."
)

STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"

MAX_ROUNDS_MESSAGE = "Agent reached maximum tool-call rounds."
NO_TOOLS_MESSAGE = "Agent received a tool call but no tools are available."


def default_checkpointer() -> InMemorySaver:
    """In-memory checkpointer with our dataclasses registered explicitly.

    M6.6 limitation: state survives only for the lifetime of the process.
    """
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("agent_backend.llm.models", "ChatMessage"),
            ("agent_backend.tools.models", "ToolCall"),
        ]
    )
    return InMemorySaver(serde=serde)


@dataclass
class AgentState:
    """Graph state passed between nodes (deliberately minimal)."""

    messages: tuple[ChatMessage, ...] = ()
    tool_round: int = 0
    status: str = STATE_RUNNING  # running | completed | failed
    final_message: str | None = None
    failure: str | None = None
    thread_id: str = ""
    executed_tool_call_ids: tuple[str, ...] = ()


def build_agent_graph(
    *,
    provider: ModelProvider,
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    gateway: ToolGateway | None = None,
    max_tool_rounds: int = 3,
    system_prompt: str = SYSTEM_PROMPT,
    policy: PolicyEngine | None = None,
    approvals: ApprovalManager | None = None,
    checkpointer: InMemorySaver | None = None,
):
    """Build and compile the explicit StateGraph (no prebuilt agent API)."""
    if max_tool_rounds < 1:
        raise ValueError(f"max_tool_rounds must be >= 1, got {max_tool_rounds}")
    policy_engine = policy or PolicyEngine.default()
    approval_manager = approvals or ApprovalManager()

    def agent_node(state: AgentState) -> dict[str, Any]:
        """State -> ModelProvider.chat() -> State update. Nothing else."""
        if state.status != STATE_RUNNING:
            return {}
        if _pending_calls(state):
            # The current tool batch is unfinished (HITL can pause mid-batch):
            # never answer early - route straight back to the tool node.
            return {}
        # Hard round limit: never start another model call after the last
        # allowed tool round (mirrors the M6.4 loop exactly).
        if (
            state.tool_round >= max_tool_rounds
            and state.messages
            and state.messages[-1].role == "tool"
        ):
            return {"status": STATE_FAILED, "failure": MAX_ROUNDS_MESSAGE}

        tools = gateway.definitions() if gateway is not None else ()
        request = ModelRequest(
            model=model,
            messages=tuple(state.messages),
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )
        started = time.monotonic()
        try:
            response = provider.chat(request)
        except LLMError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                "llm call failed: node=agent_node provider=%s error=%s latency_ms=%d",
                provider.name,
                type(exc).__name__,
                latency_ms,
            )
            return {
                "status": STATE_FAILED,
                "failure": f"LLM request failed ({type(exc).__name__})",
            }
        latency_ms = int((time.monotonic() - started) * 1000)
        trace = ModelTrace(
            provider=response.provider,
            model=response.model or model,
            latency_ms=latency_ms,
            request_id=response.request_id,
            usage=response.usage,
            prompt_version=PROMPT_VERSION,
        )
        # trace only - never the prompt or the full answer text
        logger.info(
            "llm call: node=agent_node provider=%s model=%s latency_ms=%d "
            "request_id=%s prompt_version=%s usage=%s",
            trace.provider,
            trace.model,
            trace.latency_ms,
            trace.request_id,
            trace.prompt_version,
            trace.usage,
        )

        if not response.tool_calls:
            messages = tuple(state.messages) + (
                ChatMessage(role="assistant", content=response.content),
            )
            return {
                "messages": messages,
                "status": STATE_COMPLETED,
                "final_message": response.content,
            }
        if gateway is None:
            return {"status": STATE_FAILED, "failure": NO_TOOLS_MESSAGE}
        messages = tuple(state.messages) + (
            ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            ),
        )
        # A new batch starts: per-batch execution bookkeeping resets. This
        # keeps same-id calls from different rounds independent.
        return {"messages": messages, "executed_tool_call_ids": ()}

    def tool_node(state: AgentState) -> dict[str, Any]:
        """ONE pending call per invocation: policy -> (interrupt) -> gateway.

        Exactly one call is processed per node run so that a LangGraph
        interrupt has a stable position: on resume the node re-runs, the
        already-resolved approval is read from the ApprovalManager, the
        tool executes exactly once and the node returns (committing the
        result to the checkpoint). Remaining calls of the batch are handled
        by subsequent invocations (agent short-circuits, see
        ``should_continue``).
        """
        assert gateway is not None
        batch = _current_batch(state)
        if batch is None:
            return {}
        executed = set(state.executed_tool_call_ids)
        tool_call = next(
            (call for call in batch.tool_calls if call.id not in executed), None
        )
        if tool_call is None:
            return {}

        result = _execute_with_policy(
            tool_call,
            thread_id=state.thread_id,
            policy=policy_engine,
            approvals=approval_manager,
            gateway=gateway,
            round_index=state.tool_round + 1,
        )
        messages = tuple(state.messages) + (
            ChatMessage(
                role="tool",
                content=result.to_message_content(),
                tool_call_id=tool_call.id,
            ),
        )
        executed.add(tool_call.id)
        batch_done = all(call.id in executed for call in batch.tool_calls)
        update: dict[str, Any] = {
            "messages": messages,
            "executed_tool_call_ids": tuple(executed),
        }
        if batch_done:  # one tool round = one completed batch (M6.4 parity)
            update["tool_round"] = state.tool_round + 1
        return update

    def should_continue(state: AgentState) -> Literal["tools", "end"]:
        """The single routing decision of the graph."""
        if state.status != STATE_RUNNING:
            return "end"
        if _pending_calls(state):
            return "tools"  # unfinished batch (incl. post-interrupt resume)
        if not state.messages:
            return "end"
        last = state.messages[-1]
        if last.role == "assistant" and last.tool_calls:
            return "tools"
        return "end"

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", "end": END}
    )
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)


def _execute_with_policy(
    tool_call: ToolCall,
    *,
    thread_id: str,
    policy: PolicyEngine,
    approvals: ApprovalManager,
    gateway: ToolGateway,
    round_index: int,
) -> ToolResult:
    """The ONLY path from a tool call to execution (policy cannot be skipped)."""
    decision = policy.evaluate(tool_call)
    logger.info(
        "policy check: node=tool_node tool=%s risk=%s approval_required=%s",
        tool_call.name,
        decision.risk_level,
        decision.approval_required,
    )
    if not decision.approval_required:
        started = time.monotonic()
        result = gateway.execute(tool_call)
        _log_tool_result(tool_call, result, round_index, started)
        return result

    approval = approvals.create_or_get(
        thread_id=thread_id, tool_call=tool_call, reason=decision.reason
    )
    logger.info(
        "approval created: id=%s tool=%s call=%s round=%d",
        approval.approval_id,
        tool_call.name,
        tool_call.id,
        round_index,
    )

    current = approval
    if approval.status == APPROVAL_PENDING:
        logger.info(
            "interrupt: node=tool_node approval_id=%s tool=%s",
            approval.approval_id,
            tool_call.name,
        )
        payload = interrupt(
            {
                "type": "tool_approval",
                "approval_id": approval.approval_id,
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "reason": decision.reason,
                "arguments_summary": approval.arguments_summary,
            }
        )
        current = approvals.get(approval.approval_id)
        if current.status == APPROVAL_PENDING:
            # Direct graph resume (no API-layer resolution yet): apply the
            # resume payload through the manager so the transition rules
            # (binding, replay protection) still hold.
            applied = _apply_resume_payload(
                tool_call,
                approval,
                payload,
                thread_id=thread_id,
                approvals=approvals,
            )
            if isinstance(applied, ToolResult):
                return applied
            current = applied
    # The ApprovalManager is authoritative: an approval resolved before the
    # resume (API path) or by the payload above has exactly one final state.

    if current.status == APPROVAL_REJECTED:
        logger.info(
            "approval rejected: id=%s tool=%s", approval.approval_id, tool_call.name
        )
        return ToolResult.error(
            tool_call,
            "APPROVAL_REJECTED",
            current.decision_reason or "Human approval was rejected.",
        )
    logger.info(
        "approval approved: id=%s tool=%s", approval.approval_id, tool_call.name
    )
    started = time.monotonic()
    result = gateway.execute(tool_call)
    _log_tool_result(tool_call, result, round_index, started)
    return result


def _apply_resume_payload(
    tool_call: ToolCall,
    approval: ApprovalRequest,
    payload: Any,
    *,
    thread_id: str,
    approvals: ApprovalManager,
) -> ApprovalRequest | ToolResult:
    """Apply a direct-resume payload via the manager, or return an error."""
    approval_id_value = payload.get("approval_id") if isinstance(payload, dict) else None
    decision_value = payload.get("decision") if isinstance(payload, dict) else None
    if approval_id_value != approval.approval_id or decision_value not in (
        APPROVAL_APPROVED,
        APPROVAL_REJECTED,
    ):
        logger.warning(
            "approval mismatch: expected=%s got=%r",
            approval.approval_id,
            approval_id_value,
        )
        return ToolResult.error(
            tool_call,
            "APPROVAL_MISMATCH",
            "Resume payload does not match the pending approval.",
        )
    reason_value = payload.get("reason") if isinstance(payload, dict) else None
    try:
        return approvals.resolve(
            approval_id=approval.approval_id,
            thread_id=thread_id,
            decision=decision_value,
            reason=reason_value,
        )
    except ApprovalError as exc:
        return ToolResult.error(tool_call, exc.code, exc.message)


def _current_batch(state: AgentState) -> ChatMessage | None:
    """The most recent assistant message, when it carries tool calls."""
    for message in reversed(tuple(state.messages)):
        if message.role == "assistant":
            return message if message.tool_calls else None
    return None


def _pending_calls(state: AgentState) -> tuple[ToolCall, ...]:
    """Calls of the current batch that have not produced a result yet."""
    batch = _current_batch(state)
    if batch is None:
        return ()
    executed = set(state.executed_tool_call_ids)
    return tuple(call for call in batch.tool_calls if call.id not in executed)


def _log_tool_result(
    tool_call: ToolCall, result: ToolResult, round_index: int, started: float
) -> None:
    logger.info(
        "tool call: node=tool_node name=%s id=%s round=%d status=%s "
        "error_code=%s latency_ms=%d",
        tool_call.name,
        tool_call.id,
        round_index,
        result.status,
        result.error_code,
        int((time.monotonic() - started) * 1000),
    )


class LangGraphAgentRuntime:
    """AgentRuntime implementation backed by the compiled StateGraph.

    Depends on the ModelProvider, the ToolGateway, the PolicyEngine, the
    ApprovalManager and the checkpointer - never on the platform client,
    HTTP, Docker or the filesystem.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        gateway: ToolGateway | None = None,
        max_tool_rounds: int = 3,
        policy: PolicyEngine | None = None,
        approvals: ApprovalManager | None = None,
        checkpointer: InMemorySaver | None = None,
        skills: SkillService | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be a non-empty string")
        if max_tool_rounds < 1:
            raise ValueError(f"max_tool_rounds must be >= 1, got {max_tool_rounds}")
        self._max_tool_rounds = max_tool_rounds
        self._approvals = approvals or ApprovalManager()
        self._skills = skills
        self._graph = build_agent_graph(
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            gateway=gateway,
            max_tool_rounds=max_tool_rounds,
            policy=policy or PolicyEngine.default(),
            approvals=self._approvals,
            checkpointer=checkpointer or default_checkpointer(),
        )

    @property
    def graph(self):
        """The compiled StateGraph (introspection/tests)."""
        return self._graph

    @property
    def approvals(self) -> ApprovalManager:
        return self._approvals

    # -- runtime API ---------------------------------------------------------

    def handle(self, request: ChatRequest) -> ChatResponse:
        thread_id = request.session_id or uuid4().hex
        config = self._config(thread_id)

        snapshot = self._graph.get_state(config)
        if snapshot.next:  # a pending interrupt: never bypass, never re-run
            return self._interrupted_response(snapshot.tasks, thread_id)

        # M7.1: deterministic skill selection + progressive disclosure.
        # Skills only extend the system message; they can never add tools,
        # change policy or skip HITL.
        system_prompt = (
            self._skills.build_system_prompt(SYSTEM_PROMPT, request.message)
            if self._skills is not None
            else SYSTEM_PROMPT
        )
        initial = AgentState(
            messages=(
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=request.message),
            ),
            thread_id=thread_id,
        )
        result = self._graph.invoke(initial, config)
        return self._to_chat_response(result, thread_id)

    def resume(
        self,
        *,
        session_id: str,
        approval_id: str,
        decision: str,
        reason: str | None = None,
    ) -> ChatResponse:
        """Resolve one pending approval and resume the interrupted graph.

        Raises:
            ApprovalError: NOT_FOUND / MISMATCH / ALREADY_RESOLVED - the
                graph is only resumed after a valid, single transition.
        """
        approval = self._approvals.resolve(
            approval_id=approval_id,
            thread_id=session_id,
            decision=decision,
            reason=reason,
        )
        logger.info(
            "resume: thread=%s approval_id=%s decision=%s",
            session_id,
            approval.approval_id,
            approval.status,
        )
        payload = {
            "approval_id": approval.approval_id,
            "decision": approval.status,
            "reason": approval.decision_reason,
        }
        result = self._graph.invoke(Command(resume=payload), self._config(session_id))
        return self._to_chat_response(result, session_id)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def _to_chat_response(self, result: Any, thread_id: str) -> ChatResponse:
        if isinstance(result, dict):
            interrupts = result.get("__interrupt__")
            if interrupts:
                payload = _interrupt_value(interrupts[0])
                return self._interrupted_from_payload(payload, thread_id)
            state = AgentState(
                **{
                    key: value
                    for key, value in result.items()
                    if key in AgentState.__dataclass_fields__
                }
            )
        else:
            state = result
        if state.status == STATE_COMPLETED:
            return ChatResponse(
                message=state.final_message or "",
                status=CHAT_STATUS_COMPLETED,
                session_id=thread_id,
            )
        return ChatResponse(
            message=state.failure or "Agent failed.",
            status=CHAT_STATUS_FAILED,
            session_id=thread_id,
        )

    def _interrupted_response(self, tasks: Any, thread_id: str) -> ChatResponse:
        payload: dict[str, Any] = {}
        for task in tasks or ():
            for pending in getattr(task, "interrupts", ()) or ():
                payload = _interrupt_value(pending)
                break
            if payload:
                break
        return self._interrupted_from_payload(payload, thread_id)

    @staticmethod
    def _interrupted_from_payload(
        payload: Any, thread_id: str
    ) -> ChatResponse:
        tool_name = (
            payload.get("tool_name") if isinstance(payload, dict) else None
        )
        approval_id = (
            payload.get("approval_id") if isinstance(payload, dict) else None
        )
        message = (
            f"Human approval required for tool '{tool_name}'."
            if tool_name
            else "Human approval required."
        )
        return ChatResponse(
            message=message,
            status=CHAT_STATUS_INTERRUPTED,
            session_id=thread_id,
            approval_required=True,
            approval_id=approval_id,
        )


def _interrupt_value(interrupt_object: Any) -> Any:
    """Extract the payload from a LangGraph Interrupt (or plain value)."""
    return getattr(interrupt_object, "value", interrupt_object)
