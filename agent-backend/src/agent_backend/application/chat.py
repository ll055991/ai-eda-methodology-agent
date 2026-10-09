"""Chat application service and the AgentRuntime seam.

The runtime is replaceable behind one protocol:

    M6.2  StubAgentRuntime
    M6.3  LLMAgentRuntime          (hand-written loop)
    M6.5  LangGraphAgentRuntime    (StateGraph; LLMAgentRuntime kept as a
                                    compatibility facade)

so the HTTP layer never needs to change when the agent framework does.
Tool calling semantics (allowlist, schema validation, round limit) live
in the graph runtime and the M6.4 tool system; this module only holds the
seam, the stub and the application service.
"""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from agent_backend.application.approval import ApprovalError
from agent_backend.application.langgraph_runtime import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    LangGraphAgentRuntime,
)
from agent_backend.errors import INVALID_REQUEST, AgentError
from agent_backend.logging import get_logger
from agent_backend.models.chat import (
    CHAT_STATUS_COMPLETED,
    ChatRequest,
    ChatResponse,
    ChatResumeRequest,
)

__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "AgentRuntime",
    "ChatService",
    "LLMAgentRuntime",
    "LangGraphAgentRuntime",
    "StubAgentRuntime",
]

logger = get_logger(__name__)

STUB_MESSAGE = (
    "Agent Backend is running in stub mode; the LLM runtime is not enabled "
    "(AEMP_AGENT_RUNTIME=stub)."
)


class AgentRuntime(Protocol):
    """The seam every future agent framework must implement."""

    def handle(self, request: ChatRequest) -> ChatResponse: ...


class LLMAgentRuntime(LangGraphAgentRuntime):
    """Compatibility facade: the M6.3/M6.4 name over the M6.5 graph runtime.

    Kept so existing code/tests and ``AEMP_AGENT_RUNTIME=llm`` semantics do
    not change when the loop became a StateGraph.
    """


class StubAgentRuntime:
    """M6.2 baseline: answers without any LLM, tool call or Platform access."""

    def handle(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=STUB_MESSAGE,
            status=CHAT_STATUS_COMPLETED,
            session_id=request.session_id or uuid4().hex,
            run_id=None,
            approval_required=False,
        )


class ChatService:
    """Application service: HTTP -> ChatService -> AgentRuntime.

    Session handling is minimal by design: the client may supply a
    ``session_id``, otherwise the runtime generates one. Nothing is
    persisted (no Redis / SQLite / vector store in M6.2).
    """

    def __init__(self, runtime: AgentRuntime | None = None) -> None:
        self._runtime: AgentRuntime = runtime or StubAgentRuntime()

    def handle(self, request: ChatRequest) -> ChatResponse:
        response = self._runtime.handle(request)
        logger.info(
            "chat handled: session_id=%s status=%s run_id=%s",
            response.session_id,
            response.status,
            response.run_id,
        )
        return response

    def resume(self, request: ChatResumeRequest) -> ChatResponse:
        """Resume an interrupted execution after a human decision (M6.6).

        Raises:
            AgentError: INVALID_REQUEST when the runtime cannot resume;
                approval-domain codes (NOT_FOUND / ALREADY_RESOLVED /
                MISMATCH) when the decision is not valid for this session.
        """
        resume = getattr(self._runtime, "resume", None)
        if resume is None:
            raise AgentError(
                INVALID_REQUEST, "the current runtime does not support resume"
            )
        try:
            response = resume(
                session_id=request.session_id,
                approval_id=request.approval_id,
                decision=request.decision,
                reason=request.reason,
            )
        except ApprovalError as exc:
            raise AgentError(exc.code, exc.message) from exc
        logger.info(
            "chat resumed: session_id=%s status=%s approval_id=%s",
            response.session_id,
            response.status,
            request.approval_id,
        )
        return response
