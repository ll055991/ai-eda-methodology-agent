"""Policy value types: risk classification and decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "high", "unknown"]

__all__ = ["PolicyDecision", "RiskLevel", "ToolPolicy"]


@dataclass(frozen=True)
class ToolPolicy:
    """Declared risk of one tool (code-owned, never model-owned)."""

    tool_name: str
    risk_level: RiskLevel
    approval_required: bool

    def __post_init__(self) -> None:
        if not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string")
        if self.risk_level not in ("low", "high", "unknown"):
            raise ValueError(f"invalid risk level {self.risk_level!r}")
        if self.risk_level == "high" and not self.approval_required:
            raise ValueError("high-risk tools must require approval")


@dataclass(frozen=True)
class PolicyDecision:
    """The deterministic authorization decision for one tool call."""

    tool_name: str
    risk_level: str
    approval_required: bool
    reason: str
