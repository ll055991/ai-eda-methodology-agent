"""Chat models: the Agent API contract shapes (contracts/agent-api.yaml).

Approval fields are reserved for M6.7 and stay inert in M6.2
(``approval_required`` is always False).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CHAT_STATUS_COMPLETED = "completed"
CHAT_STATUS_FAILED = "failed"
CHAT_STATUS_PENDING_APPROVAL = "pending_approval"  # reserved (M6.0); superseded by interrupted
CHAT_STATUS_INTERRUPTED = "interrupted"  # M6.6: waiting for human approval

__all__ = [
    "CHAT_STATUS_COMPLETED",
    "CHAT_STATUS_FAILED",
    "CHAT_STATUS_INTERRUPTED",
    "CHAT_STATUS_PENDING_APPROVAL",
    "ChatRequest",
    "ChatResponse",
    "ChatResumeRequest",
]


@dataclass(frozen=True)
class ChatRequest:
    """One Frontend -> Agent message."""

    message: str
    session_id: str | None = None


@dataclass(frozen=True)
class ChatResumeRequest:
    """One human decision on a pending approval (M6.6)."""

    session_id: str
    approval_id: str
    decision: str  # approved | rejected
    reason: str | None = None


@dataclass(frozen=True)
class ChatResponse:
    """One Agent -> Frontend reply.

    Every factual claim must carry the ``run_id`` it came from; M6.2's
    stub has no facts, so ``run_id`` stays None.
    """

    message: str
    status: str
    session_id: str | None = None
    run_id: str | None = None
    approval_required: bool = False
    approval_id: str | None = None
    resume_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form matching ``agent-chat-response.json``."""
        document: dict[str, Any] = {
            "message": self.message,
            "status": self.status,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "approval_required": self.approval_required,
        }
        if self.approval_id is not None:
            document["approval_id"] = self.approval_id
        if self.resume_token is not None:
            document["resume_token"] = self.resume_token
        return document
