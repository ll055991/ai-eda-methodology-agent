"""PolicyEngine: ToolCall -> PolicyDecision (deterministic, code-owned)."""

from __future__ import annotations

from collections.abc import Iterable

from agent_backend.policy.models import PolicyDecision, ToolPolicy
from agent_backend.policy.rules import (
    DEFAULT_TOOL_POLICIES,
    HIGH_RISK_REASON,
    LOW_RISK_REASON,
    UNKNOWN_TOOL_REASON,
)
from agent_backend.tools.models import ToolCall

__all__ = ["PolicyEngine"]


class PolicyEngine:
    """Maps tool names to risk decisions; no model input, no I/O."""

    def __init__(self, policies: Iterable[ToolPolicy] = DEFAULT_TOOL_POLICIES) -> None:
        self._policies = {policy.tool_name: policy for policy in policies}

    @classmethod
    def default(cls) -> PolicyEngine:
        return cls(DEFAULT_TOOL_POLICIES)

    def evaluate(self, tool_call: ToolCall) -> PolicyDecision:
        policy = self._policies.get(tool_call.name)
        if policy is None:
            # Unknown tools are rejected by the gateway; never approve them
            # silently, never execute them either.
            return PolicyDecision(
                tool_name=tool_call.name,
                risk_level="unknown",
                approval_required=False,
                reason=UNKNOWN_TOOL_REASON,
            )
        reason = HIGH_RISK_REASON if policy.approval_required else LOW_RISK_REASON
        return PolicyDecision(
            tool_name=tool_call.name,
            risk_level=policy.risk_level,
            approval_required=policy.approval_required,
            reason=reason,
        )
