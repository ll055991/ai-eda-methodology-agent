"""PolicyEngine tests: deterministic, code-owned risk classification (M6.6)."""

import pytest

from agent_backend.policy import PolicyEngine
from agent_backend.policy.models import ToolPolicy
from agent_backend.tools.models import ToolCall


def call(name: str) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments={})


class TestDefaultPolicies:
    def test_run_flow_is_high_risk_and_requires_approval(self) -> None:
        decision = PolicyEngine.default().evaluate(call("run_flow"))
        assert decision.risk_level == "high"
        assert decision.approval_required is True
        assert "EDA flow" in decision.reason

    @pytest.mark.parametrize("name", ["get_run", "run_qa", "get_artifacts"])
    def test_read_only_tools_are_low_risk(self, name: str) -> None:
        decision = PolicyEngine.default().evaluate(call(name))
        assert decision.risk_level == "low"
        assert decision.approval_required is False

    def test_unknown_tool_is_not_approved_silently(self) -> None:
        decision = PolicyEngine.default().evaluate(call("execute_shell"))
        assert decision.risk_level == "unknown"
        assert decision.approval_required is False  # gateway will reject it
        assert "gateway" in decision.reason


class TestPolicyModel:
    def test_high_risk_without_approval_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="approval"):
            ToolPolicy(tool_name="t", risk_level="high", approval_required=False)

    def test_invalid_risk_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="risk level"):
            ToolPolicy(tool_name="t", risk_level="bogus", approval_required=False)  # type: ignore[arg-type]

    def test_custom_engine_uses_given_policies(self) -> None:
        engine = PolicyEngine(
            [ToolPolicy(tool_name="run_flow", risk_level="low", approval_required=False)]
        )
        assert engine.evaluate(call("run_flow")).approval_required is False
