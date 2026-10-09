"""QAEngine: deterministic metric evaluation. Metric + Rule -> Finding.

Pure and side-effect free: same metrics + same rules ALWAYS produce the
same findings. No LLM, no network, no time, no randomness - QA verdicts
must be reproducible and auditable.

Overall status priority (documented in README and tested):
    1. any FAILED finding with severity=error  -> FAIL
    2. else any UNKNOWN finding                -> UNKNOWN
    3. else                                    -> PASS
    (a FAILED finding with severity=warning does NOT gate: it is counted
    in ``warnings`` and stays visible as a finding.)
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping

from aemp.qa.models import (
    FINDING_STATUS_FAIL,
    FINDING_STATUS_PASS,
    FINDING_STATUS_UNKNOWN,
    QAFinding,
    QARule,
    QASummary,
    RulePack,
)

__all__ = ["QAEngine"]

_CHECKS: dict[str, Callable[[float, object], bool]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "ge": operator.ge,
    "lt": operator.lt,
    "le": operator.le,
}


def _check_between(actual: float, threshold: tuple[float, float]) -> bool:
    low, high = threshold
    return low <= actual <= high


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class QAEngine:
    """Evaluates a rule pack against a metrics mapping."""

    def evaluate(self, metrics: Mapping[str, float], rule_pack: RulePack) -> QASummary:
        findings = tuple(
            self._evaluate_rule(metrics, rule) for rule in rule_pack.rules
        )
        return self._summarize(rule_pack, findings)

    # -- internals --------------------------------------------------------

    @staticmethod
    def _evaluate_rule(metrics: Mapping[str, float], rule: QARule) -> QAFinding:
        actual = metrics.get(rule.metric)
        if not _is_number(actual):
            # Missing metric = UNKNOWN. Never PASS (no evidence), never
            # FAIL (no evidence), and never substituted with 0.
            return QAFinding(
                rule_id=rule.id,
                metric=rule.metric,
                check=rule.check,
                threshold=rule.threshold,
                severity=rule.severity,
                status=FINDING_STATUS_UNKNOWN,
                expected=rule.expected_text(),
                actual=None,
                message=f"Metric {rule.metric!r} is unavailable",
            )
        if rule.check == "between":
            ok = _check_between(float(actual), rule.threshold)  # type: ignore[arg-type]
        else:
            ok = _CHECKS[rule.check](float(actual), rule.threshold)
        status = FINDING_STATUS_PASS if ok else FINDING_STATUS_FAIL
        return QAFinding(
            rule_id=rule.id,
            metric=rule.metric,
            check=rule.check,
            threshold=rule.threshold,
            severity=rule.severity,
            status=status,
            expected=rule.expected_text(),
            actual=float(actual),
            message=(
                f"{rule.metric} = {actual}: expected {rule.expected_text()} -> {status}"
            ),
        )

    @staticmethod
    def _summarize(rule_pack: RulePack, findings: tuple[QAFinding, ...]) -> QASummary:
        failed = sum(1 for f in findings if f.status == FINDING_STATUS_FAIL)
        unknown = sum(1 for f in findings if f.status == FINDING_STATUS_UNKNOWN)
        passed = sum(1 for f in findings if f.status == FINDING_STATUS_PASS)
        warnings = sum(
            1
            for f in findings
            if f.status == FINDING_STATUS_FAIL and f.severity == "warning"
        )
        error_fail = any(
            f.status == FINDING_STATUS_FAIL and f.severity == "error"
            for f in findings
        )
        if error_fail:
            status = FINDING_STATUS_FAIL
        elif unknown:
            status = FINDING_STATUS_UNKNOWN
        else:
            status = FINDING_STATUS_PASS
        return QASummary(
            status=status,
            total_rules=len(findings),
            passed=passed,
            failed=failed,
            unknown=unknown,
            warnings=warnings,
            findings=findings,
            rule_pack=rule_pack.name,
            rule_pack_version=rule_pack.version,
        )
