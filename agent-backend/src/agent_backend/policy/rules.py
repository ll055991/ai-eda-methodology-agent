"""Default tool policies: exactly the four contract tools (M6.6).

    run_flow       HIGH  -> human approval required before execution
    get_run        LOW
    run_qa         LOW
    get_artifacts  LOW

No new tools may be added here without a contract change.
"""

from __future__ import annotations

from agent_backend.policy.models import ToolPolicy

DEFAULT_TOOL_POLICIES: tuple[ToolPolicy, ...] = (
    ToolPolicy(tool_name="run_flow", risk_level="high", approval_required=True),
    ToolPolicy(tool_name="get_run", risk_level="low", approval_required=False),
    ToolPolicy(tool_name="run_qa", risk_level="low", approval_required=False),
    ToolPolicy(tool_name="get_artifacts", risk_level="low", approval_required=False),
)

HIGH_RISK_REASON = (
    "Executing an EDA flow may modify run state and consume compute resources."
)
LOW_RISK_REASON = "Read-only tool; no approval required."
UNKNOWN_TOOL_REASON = "No policy registered for this tool; the gateway rejects it."
