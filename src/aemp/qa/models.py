"""QA data models: rules, findings, summary.

Three layers that must never be mixed:
    Metric  = fact            (M4 parsers)
    Rule    = threshold       (this module, YAML-driven)
    Finding = verdict         (QAEngine)

``status`` answers "did the rule hold?"; ``severity`` answers "how bad is
it if not?" - the two are independent: status=fail with severity=warning
is legal and does not fail the overall gate.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CHECKS",
    "FINDING_STATUS_FAIL",
    "FINDING_STATUS_PASS",
    "FINDING_STATUS_UNKNOWN",
    "RULE_SEVERITIES",
    "QAFinding",
    "QARule",
    "QASummary",
    "RulePack",
]

CHECKS = ("eq", "ne", "gt", "ge", "lt", "le", "between")
RULE_SEVERITIES = ("info", "warning", "error")
FINDING_STATUS_PASS = "pass"
FINDING_STATUS_FAIL = "fail"
FINDING_STATUS_UNKNOWN = "unknown"

_CHECK_SYMBOLS = {"eq": "==", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}


@dataclass(frozen=True)
class QARule:
    """One QA rule: a metric must satisfy a threshold condition.

    Validation happens at construction - an invalid rule can never reach
    the engine (loading-time errors, never evaluation-time surprises).

    ``threshold`` is a number for eq/ne/gt/ge/lt/le, or a ``(low, high)``
    pair for ``between`` (low <= actual <= high).
    """

    id: str
    metric: str
    check: str
    threshold: float | tuple[float, float]
    severity: str = "error"
    description: str = ""

    def __post_init__(self) -> None:
        from aemp.core.errors import InvalidRequestError

        if not isinstance(self.id, str) or not self.id.strip():
            msg = "rule 'id' must be a non-empty string"
            raise InvalidRequestError(msg)
        if not isinstance(self.metric, str) or not self.metric.strip():
            msg = f"rule {self.id!r}: 'metric' must be a non-empty string"
            raise InvalidRequestError(msg)
        if self.check not in CHECKS:
            msg = (
                f"rule {self.id!r}: invalid check {self.check!r} "
                f"(expected one of {CHECKS})"
            )
            raise InvalidRequestError(msg)
        if self.severity not in RULE_SEVERITIES:
            msg = (
                f"rule {self.id!r}: invalid severity {self.severity!r} "
                f"(expected one of {RULE_SEVERITIES})"
            )
            raise InvalidRequestError(msg)

        if self.check == "between":
            if not isinstance(self.threshold, (list, tuple)) or len(self.threshold) != 2:
                msg = (
                    f"rule {self.id!r}: 'between' threshold must be a pair "
                    f"[low, high]"
                )
                raise InvalidRequestError(msg)
            low, high = self.threshold
            if not all(_is_number(v) for v in (low, high)):
                msg = f"rule {self.id!r}: 'between' bounds must be numbers"
                raise InvalidRequestError(msg)
            if low > high:
                msg = f"rule {self.id!r}: 'between' low {low} > high {high}"
                raise InvalidRequestError(msg)
            object.__setattr__(self, "threshold", (float(low), float(high)))
            return
        if not _is_number(self.threshold):
            msg = (
                f"rule {self.id!r}: threshold must be a number for "
                f"check {self.check!r}"
            )
            raise InvalidRequestError(msg)
        object.__setattr__(self, "threshold", float(self.threshold))

    def expected_text(self) -> str:
        """Human-readable expectation, e.g. ``>= 0.0``."""
        if self.check == "between":
            low, high = self.threshold
            return f"between {low} and {high}"
        symbol = _CHECK_SYMBOLS[self.check]
        return f"{symbol} {self.threshold}"


@dataclass(frozen=True)
class QAFinding:
    """One rule evaluation verdict with full evidence.

    Everything needed to answer "why FAIL?" is preserved: rule_id,
    metric, actual value, expected text, threshold, severity.
    """

    rule_id: str
    metric: str
    check: str
    threshold: float | tuple[float, float]
    severity: str
    status: str  # pass | fail | unknown
    expected: str
    actual: float | None  # None when the metric was unavailable
    message: str


@dataclass(frozen=True)
class QASummary:
    """Overall QA result: aggregate status plus every finding."""

    status: str  # overall gate: pass | fail | unknown
    total_rules: int
    passed: int
    failed: int
    unknown: int
    warnings: int  # failed findings with severity=warning (do not gate)
    findings: tuple[QAFinding, ...]
    rule_pack: str
    rule_pack_version: int


@dataclass(frozen=True)
class RulePack:
    """A named, versioned collection of QA rules (frozen into runs)."""

    name: str
    version: int
    rules: tuple[QARule, ...]


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
