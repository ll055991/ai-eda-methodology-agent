"""ApprovalManager tests: binding, transitions, replay protection (M6.6)."""

import pytest

from agent_backend.application.approval import (
    APPROVAL_ALREADY_RESOLVED,
    APPROVAL_APPROVED,
    APPROVAL_MISMATCH,
    APPROVAL_NOT_FOUND,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    ApprovalError,
    ApprovalManager,
    summarize_arguments,
)
from agent_backend.tools.models import ToolCall


def call(call_id: str = "call_1", name: str = "run_flow") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={"flow": "rtl_to_sta.yaml"})


def manager_with_pending() -> tuple[ApprovalManager, str]:
    manager = ApprovalManager()
    approval = manager.create_or_get(
        thread_id="thread-a", tool_call=call(), reason="high risk"
    )
    return manager, approval.approval_id


class TestCreation:
    def test_unique_ids_and_pending_status(self) -> None:
        manager = ApprovalManager()
        first = manager.create_or_get(thread_id="t", tool_call=call("c1"), reason="r")
        second = manager.create_or_get(thread_id="t", tool_call=call("c2"), reason="r")
        assert first.approval_id.startswith("approval_")
        assert first.approval_id != second.approval_id
        assert first.status == APPROVAL_PENDING
        assert first.tool_call_id == "c1"

    def test_create_or_get_is_idempotent_per_tool_call(self) -> None:
        manager = ApprovalManager()
        first = manager.create_or_get(thread_id="t", tool_call=call(), reason="r")
        again = manager.create_or_get(thread_id="t", tool_call=call(), reason="other")
        assert again.approval_id == first.approval_id

    def test_same_call_id_in_another_thread_gets_its_own_approval(self) -> None:
        manager = ApprovalManager()
        first = manager.create_or_get(thread_id="t1", tool_call=call(), reason="r")
        second = manager.create_or_get(thread_id="t2", tool_call=call(), reason="r")
        assert first.approval_id != second.approval_id

    def test_arguments_summary_is_bounded(self) -> None:
        summary = summarize_arguments(
            {"flow": "x" * 5000, "nested": {"a": 1}, "n": 3}
        )
        assert len(summary["flow"]) <= 200
        assert summary["nested"] == "<dict>"
        assert summary["n"] == "3"


class TestTransitions:
    def test_approve_pending(self) -> None:
        manager, approval_id = manager_with_pending()
        resolved = manager.resolve(
            approval_id=approval_id, thread_id="thread-a", decision=APPROVAL_APPROVED
        )
        assert resolved.status == APPROVAL_APPROVED
        assert resolved.decision_at is not None

    def test_reject_pending_with_reason(self) -> None:
        manager, approval_id = manager_with_pending()
        resolved = manager.resolve(
            approval_id=approval_id,
            thread_id="thread-a",
            decision=APPROVAL_REJECTED,
            reason="not authorized",
        )
        assert resolved.status == APPROVAL_REJECTED
        assert resolved.decision_reason == "not authorized"

    def test_duplicate_approve_rejected(self) -> None:
        manager, approval_id = manager_with_pending()
        manager.resolve(
            approval_id=approval_id, thread_id="thread-a", decision=APPROVAL_APPROVED
        )
        with pytest.raises(ApprovalError) as excinfo:
            manager.resolve(
                approval_id=approval_id,
                thread_id="thread-a",
                decision=APPROVAL_APPROVED,
            )
        assert excinfo.value.code == APPROVAL_ALREADY_RESOLVED

    def test_reject_then_approve_rejected(self) -> None:
        manager, approval_id = manager_with_pending()
        manager.resolve(
            approval_id=approval_id, thread_id="thread-a", decision=APPROVAL_REJECTED
        )
        with pytest.raises(ApprovalError) as excinfo:
            manager.resolve(
                approval_id=approval_id,
                thread_id="thread-a",
                decision=APPROVAL_APPROVED,
            )
        assert excinfo.value.code == APPROVAL_ALREADY_RESOLVED

    def test_wrong_thread_rejected(self) -> None:
        manager, approval_id = manager_with_pending()
        with pytest.raises(ApprovalError) as excinfo:
            manager.resolve(
                approval_id=approval_id, thread_id="thread-b", decision=APPROVAL_APPROVED
            )
        assert excinfo.value.code == APPROVAL_MISMATCH

    def test_invalid_decision_rejected(self) -> None:
        manager, approval_id = manager_with_pending()
        with pytest.raises(ApprovalError) as excinfo:
            manager.resolve(
                approval_id=approval_id, thread_id="thread-a", decision="maybe"
            )
        assert excinfo.value.code == APPROVAL_MISMATCH

    def test_unknown_approval(self) -> None:
        manager = ApprovalManager()
        with pytest.raises(ApprovalError) as excinfo:
            manager.get("approval_nope")
        assert excinfo.value.code == APPROVAL_NOT_FOUND
        with pytest.raises(ApprovalError) as excinfo:
            manager.resolve(
                approval_id="approval_nope",
                thread_id="t",
                decision=APPROVAL_APPROVED,
            )
        assert excinfo.value.code == APPROVAL_NOT_FOUND

    def test_pending_for(self) -> None:
        manager, approval_id = manager_with_pending()
        found = manager.pending_for("thread-a", "call_1")
        assert found is not None and found.approval_id == approval_id
        assert manager.pending_for("thread-a", "call_x") is None
