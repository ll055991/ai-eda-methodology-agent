"""LLM value types: request, response, usage, trace metadata.

Provider-optional fields (usage, model, finish_reason, request_id) are
``None`` when the provider does not supply them - never fabricated,
consistent with the project-wide "unknown != 0" semantics.

M6.4 adds tool calling, backward compatibly: existing constructions
without ``tools`` / ``tool_calls`` behave exactly as in M6.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_backend.tools.models import ToolCall, ToolDefinition

ChatRole = Literal["system", "user", "assistant", "tool"]

_VALID_ROLES = ("system", "user", "assistant", "tool")

__all__ = ["ChatMessage", "ModelRequest", "ModelResponse", "ModelTrace", "ModelUsage"]


@dataclass(frozen=True)
class ChatMessage:
    """One conversation message.

    Roles: ``system`` / ``user`` / ``assistant`` / ``tool``.
    Assistant messages may carry ``tool_calls``; ``tool`` messages carry
    the matching ``tool_call_id`` and the bounded result text.
    """

    role: ChatRole
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in _VALID_ROLES:
            raise ValueError(f"invalid chat role {self.role!r} (expected {_VALID_ROLES})")
        if not isinstance(self.content, str):
            raise ValueError("message content must be a string")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))


@dataclass(frozen=True)
class ModelRequest:
    """One model call. ``tools`` empty = plain text completion (M6.3)."""

    model: str
    messages: tuple[ChatMessage, ...]
    temperature: float | None = None
    max_tokens: int | None = None
    tools: tuple[ToolDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if not self.messages:
            raise ValueError("messages must not be empty")
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))


@dataclass(frozen=True)
class ModelUsage:
    """Token usage; every field is None when the provider omits it."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    """One model answer plus honest provenance (provider/model/request_id).

    A response is either a final answer (``tool_calls`` empty) or a tool
    request (``tool_calls`` non-empty, ``content`` may be empty).
    """

    content: str
    provider: str
    model: str | None = None
    finish_reason: str | None = None
    usage: ModelUsage | None = None
    request_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        if not self.content.strip() and not self.tool_calls:
            raise ValueError("a model response needs content or tool calls")
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))


@dataclass(frozen=True)
class ModelTrace:
    """Internal trace metadata (logged; never exposed through the Agent API)."""

    provider: str
    model: str | None
    latency_ms: int
    request_id: str | None
    usage: ModelUsage | None
    prompt_version: str | None = None
