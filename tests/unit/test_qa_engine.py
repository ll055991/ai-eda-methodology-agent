"""Unit tests for the QAEngine: operator matrix, UNKNOWN semantics,
severity/status independence, overall-status priority (M5)."""

from pathlib import Path

import pytest

from aemp.parsers import OpenSTAParser
from aemp.qa.engine import QAEngine
from aemp.qa.models import QARule, RulePack
from aemp.qa.rules import RulePackLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "reports" / "opensta"
BASE_TIMING = REPO_ROOT / "configs" / "rules" / "base_timing.yaml"


def pack(*rules: QARule, name: str = "test_pack") -> RulePack:
    return RulePack(name=name, version=1, rules=tuple(rules))


def rule(check: str, threshold: object, *, severity: str = "error", metric: str = "m") -> QARule:
    return QARule(id=f"r.{check}", metric=metric, check=check, threshold=threshold,
                  severity=severity)


class TestOperators:
    @pytest.mark.parametrize(
        ("check", "threshold", "actual", "expected_status"),
        [
            # eq
            ("eq", 5, 5, "pass"), ("eq", 5, 4, "fail"), ("eq", 5.0, 5, "pass"),
            # ne
            ("ne", 5, 4, "pass"), ("ne", 5, 5, "fail"),
            # gt
            ("gt", 5, 6, "pass"), ("gt", 5, 5, "fail"), ("gt", 5, 4, "fail"),
            # ge (the platform's core check)
            ("ge", 5, 5, "pass"), ("ge", 5, 6, "pass"), ("ge", 5, 4, "fail"),
            ("ge", 0, 0.29, "pass"), ("ge", 0, -0.01, "fail"),
            # lt
            ("lt", 5, 4, "pass"), ("lt", 5, 5, "fail"),
            # le
            ("le", 5, 5, "pass"), ("le", 5, 6, "fail"), ("le", 5, 4, "pass"),
            # between (inclusive bounds)
            ("between", [40.0, 85.0], 40.0, "pass"),
            ("between", [40.0, 85.0], 85.0, "pass"),
            ("between", [40.0, 85.0], 55.0, "pass"),
            ("between", [40.0, 85.0], 39.9, "fail"),
            ("between", [40.0, 85.0], 85.1, "fail"),
        ],
    )
    def test_operator_matrix(
        self, check: str, threshold: object, actual: float, expected_status: str
    ) -> None:
        summary = QAEngine().evaluate({"m": actual}, pack(rule(check, threshold)))
        assert summary.findings[0].status == expected_status

    def test_integer_and_float_metrics_both_work(self) -> None:
        summary = QAEngine().evaluate({"m": 2}, pack(rule("ge", 1.5)))
        assert summary.findings[0].status == "pass"
        summary = QAEngine().evaluate({"m": 2.0}, pack(rule("ge", 1.5)))
        assert summary.findings[0].status == "pass"


class TestMissingMetric:
    def test_missing_metric_is_unknown_never_fail_or_zero(self) -> None:
        summary = QAEngine().evaluate({}, pack(rule("ge", 0)))
        finding = summary.findings[0]
        assert finding.status == "unknown"
        assert finding.actual is None  # not 0 - unknown != 0
        assert "unavailable" in finding.message
        assert summary.status == "unknown"

    def test_none_metric_is_unknown(self) -> None:
        # metrics mapping with an explicit None value (parser gap)
        summary = QAEngine().evaluate({"m": None}, pack(rule("ge", 0)))  # type: ignore[dict-item]
        assert summary.findings[0].status == "unknown"

    def test_boolean_metric_is_unknown(self) -> None:
        # bool is not a valid metric value - treated as unavailable
        summary = QAEngine().evaluate({"m": True}, pack(rule("ge", 0)))  # type: ignore[dict-item]
        assert summary.findings[0].status == "unknown"

    def test_missing_metric_in_otherwise_passing_pack_gates_unknown(self) -> None:
        rules = pack(rule("ge", 0, metric="a"), rule("ge", 0, metric="missing"))
        summary = QAEngine().evaluate({"a": 1.0}, rules)
        assert summary.status == "unknown"  # PASS + UNKNOWN -> UNKNOWN
        assert summary.passed == 1 and summary.unknown == 1


class TestSeverityVsStatus:
    def test_fail_with_warning_severity_does_not_gate(self) -> None:
        rules = pack(rule("ge", 0, metric="a", severity="warning"))
        summary = QAEngine().evaluate({"a": -1.0}, rules)
        assert summary.findings[0].status == "fail"  # status answers "rule held?"
        assert summary.findings[0].severity == "warning"  # severity: how bad
        assert summary.status == "pass"  # warnings do not fail the gate
        assert summary.warnings == 1
        assert summary.failed == 1  # still counted as a failed rule

    def test_fail_with_error_severity_gates(self) -> None:
        rules = pack(rule("ge", 0, metric="a", severity="error"))
        summary = QAEngine().evaluate({"a": -1.0}, rules)
        assert summary.status == "fail"

    def test_info_severity_fail_does_not_gate(self) -> None:
        rules = pack(rule("ge", 0, metric="a", severity="info"))
        summary = QAEngine().evaluate({"a": -1.0}, rules)
        assert summary.status == "pass"


class TestOverallStatus:
    def test_pass_plus_pass_is_pass(self) -> None:
        rules = pack(rule("ge", 0, metric="a"), rule("le", 10, metric="b"))
        summary = QAEngine().evaluate({"a": 1.0, "b": 2.0}, rules)
        assert summary.status == "pass"

    def test_pass_plus_unknown_is_unknown(self) -> None:
        rules = pack(rule("ge", 0, metric="a"), rule("ge", 0, metric="ghost"))
        summary = QAEngine().evaluate({"a": 1.0}, rules)
        assert summary.status == "unknown"

    def test_pass_plus_fail_is_fail(self) -> None:
        rules = pack(rule("ge", 0, metric="a"), rule("ge", 0, metric="b"))
        summary = QAEngine().evaluate({"a": 1.0, "b": -1.0}, rules)
        assert summary.status == "fail"

    def test_unknown_plus_fail_is_fail(self) -> None:
        rules = pack(rule("ge", 0, metric="ghost"), rule("ge", 0, metric="b"))
        summary = QAEngine().evaluate({"b": -1.0}, rules)
        assert summary.status == "fail"

    def test_summary_counts_add_up(self) -> None:
        rules = pack(
            rule("ge", 0, metric="a"),
            rule("ge", 0, metric="b"),
            rule("ge", 0, metric="ghost"),
        )
        summary = QAEngine().evaluate({"a": 1.0, "b": -1.0}, rules)
        assert summary.total_rules == 3
        assert (summary.passed, summary.failed, summary.unknown) == (1, 1, 1)


class TestRealReportChain:
    """M4 -> M5: real OpenSTA fixtures drive the deterministic QA verdict.

    The fixtures are the platform's own pinned-tool output; this proves
    parser facts (not hand-typed numbers) can produce the verdict.
    """

    def evaluate_fixture(self, name: str):
        report = OpenSTAParser().parse(FIXTURES / name)
        pack = RulePackLoader.load(BASE_TIMING)
        return QAEngine().evaluate(report.metrics_dict(), pack)

    def test_clean_timing_fixture_passes(self) -> None:
        summary = self.evaluate_fixture("timing_basic.rpt")
        assert summary.status == "pass"
        assert summary.failed == 0 and summary.unknown == 0

    def test_violated_timing_fixture_fails_on_wns(self) -> None:
        summary = self.evaluate_fixture("timing_violated.rpt")
        assert summary.status == "fail"
        wns = next(f for f in summary.findings if f.metric == "sta.wns_ns")
        assert wns.actual == pytest.approx(-0.01)
        assert wns.status == "fail"
        assert wns.severity == "error"
        assert wns.rule_id == "timing.wns.non_negative"


class TestDeterminism:
    def test_same_inputs_same_outputs(self) -> None:
        rules = pack(rule("ge", 0, metric="a"), rule("between", [1, 5], metric="b"))
        metrics = {"a": -0.5, "b": 3.3}
        first = QAEngine().evaluate(metrics, rules)
        second = QAEngine().evaluate(metrics, rules)
        assert first == second  # frozen dataclasses, value equality

    def test_finding_keeps_full_evidence(self) -> None:
        rules = pack(rule("ge", 0.0, metric="sta.wns_ns"))
        summary = QAEngine().evaluate({"sta.wns_ns": -0.01}, rules)
        f = summary.findings[0]
        assert f.rule_id == "r.ge"
        assert f.metric == "sta.wns_ns"
        assert f.actual == pytest.approx(-0.01)
        assert f.expected == ">= 0.0"
        assert f.threshold == 0.0
        assert f.check == "ge"
        assert f.severity == "error"
        assert f.status == "fail"
        assert "-0.01" in f.message
