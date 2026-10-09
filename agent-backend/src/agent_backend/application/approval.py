"""Approval model and manager (M6.6): state only, never tool execution.

One approval is bound to exactly one ``(thread_id, tool_call_id)`` pair -
never to a tool name - so an approval for ``run_flow(A)`` can never be
replayed onto ``run_flow(B)``. Only ``pending`` approvals can be decided;
duplicate decisions are rejected (replay protection).

In-memory by design (M6.6): valid for the lifetime of the process only.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from agent_backend.tools.models import ToolCall

__all__ = [
    "APPROVAL_ALREADY_RESOLVED",
    "APPROVAL_MISMATCH",
    "APPROVAL_NOT_FOUND",
    "APPROVAL_APPROVED",
    "APPROVAL_PENDING",
    "APPROVAL_REJECTED",
    "ApprovalError",
    "ApprovalManager",
    "ApprovalRequest",
    "summarize_arguments",
]

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"

APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
APPROVAL_ALREADY_RESOLVED = "APPROVAL_ALREADY_RESOLVED"
APPROVAL_MISMATCH = "APPROVAL_MISMATCH"

ApprovalStatus = Literal["pending", "approved", "rejected"]

_SUMMARY_MAX_CHARS = 1000
_SUMMARY_VALUE_CHARS = 200
_SUMMARY_MAX_KEYS = 10


class ApprovalError(Exception):
    """A stable approval-domain error (mapped to Agent API codes)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Bounded, sanitized view of tool arguments (safe for UI/logs)."""
    summary: dict[str, Any] = {}
    for key, value in list(arguments.items())[:_SUMMARY_MAX_KEYS]:
        if value is None or isinstance(value, (str, int, float, bool)):
            summary[str(key)] = str(value)[:_SUMMARY_VALUE_CHARS]
        else:
            summary[str(key)] = f"<{type(value).__name__}>"
    encoded = json.dumps(summary, ensure_ascii=False)
    if len(encoded) > _SUMMARY_MAX_CHARS:
        summary = {"summary": encoded[:_SUMMARY_MAX_CHARS]}
    return summary


@dataclass(frozen=True)
class ApprovalRequest:
    """One pending (or decided) human-approval request."""

    approval_id: str
    tool_call_id: str
    tool_name: str
    thread_id: str
    reason: str
    status: ApprovalStatus
    arguments_summary: dict[str, Any]
    created_at: str
    decision_at: str | None = None
    decision_reason: str | None = None


class ApprovalManager:
    """Creates and resolves approvals; NEVER executes tools."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, ApprovalRequest] = {}
        self._by_call: dict[tuple[str, str], str] = {}

    def create_or_get(
        self, *, thread_id: str, tool_call: ToolCall, reason: str
    ) -> ApprovalRequest:
        """Idempotent per (thread_id, tool_call_id) - resume-safe."""
        key = (thread_id, tool_call.id)
        with self._lock:
            existing_id = self._by_call.get(key)
            if existing_id is not None:
                return self._by_id[existing_id]
            approval = ApprovalRequest(
                approval_id=f"approval_{uuid4().hex}",
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                thread_id=thread_id,
                reason=reason,
                status=APPROVAL_PENDING,
                arguments_summary=summarize_arguments(tool_call.arguments),
                created_at=_utc_now(),
            )
            self._by_id[approval.approval_id] = approval
            self._by_call[key] = approval.approval_id
            return approval

    def get(self, approval_id: str) -> ApprovalRequest:
        with self._lock:
            approval = self._by_id.get(approval_id)
        if approval is None:
            raise ApprovalError(
                APPROVAL_NOT_FOUND, f"Unknown approval: {approval_id}"
            )
        return approval

    def pending_for(self, thread_id: str, tool_call_id: str) -> ApprovalRequest | None:
        with self._lock:
            approval_id = self._by_call.get((thread_id, tool_call_id))
            return self._by_id.get(approval_id) if approval_id else None

    def resolve(
        self,
        *,
        approval_id: str,
        thread_id: str,
        decision: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """pending -> approved | rejected, exactly once, thread-bound.

        Raises:
            ApprovalError: NOT_FOUND, MISMATCH (wrong thread/decision) or
                ALREADY_RESOLVED (replay protection).
        """
        if decision not in (APPROVAL_APPROVED, APPROVAL_REJECTED):
            raise ApprovalError(
                APPROVAL_MISMATCH,
                f"decision must be 'approved' or 'rejected', got {decision!r}",
            )
        with self._lock:
            approval = self._by_id.get(approval_id)
            if approval is None:
                raise ApprovalError(
                    APPROVAL_NOT_FOUND, f"Unknown approval: {approval_id}"
                )
            if approval.thread_id != thread_id:
                raise ApprovalError(
                    APPROVAL_MISMATCH,
                    "Approval does not belong to this session/thread.",
                )
            if approval.status != APPROVAL_PENDING:
                raise ApprovalError(
                    APPROVAL_ALREADY_RESOLVED,
                    f"Approval is already {approval.status}.",
                )
            resolved = replace(
                approval,
                status=decision,
                decision_at=_utc_now(),
                decision_reason=(reason or None),
            )
            self._by_id[approval_id] = resolved
            return resolved


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
