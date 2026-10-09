"""Unit tests for rule validation and the rule pack loader (M5)."""

from pathlib import Path

import pytest

from aemp.core.errors import InvalidRequestError
from aemp.qa.models import QARule
from aemp.qa.rules import RulePackLoader

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestQARuleValidation:
    def test_valid_rule_with_defaults(self) -> None:
        rule = QARule(
            id="timing.wns.non_negative",
            metric="sta.wns_ns",
            check="ge",
            threshold=0.0,
        )
        assert rule.severity == "error"  # sensible default
        assert rule.description == ""
        assert rule.expected_text() == ">= 0.0"

    def test_blank_id_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="id"):
            QARule(id="  ", metric="m", check="ge", threshold=0)

    def test_blank_metric_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="metric"):
            QARule(id="r", metric="", check="ge", threshold=0)

    def test_invalid_check_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="invalid check"):
            QARule(id="r", metric="m", check="something_random", threshold=0)

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="invalid severity"):
            QARule(id="r", metric="m", check="ge", threshold=0, severity="fatal")

    def test_non_numeric_threshold_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="threshold"):
            QARule(id="r", metric="m", check="ge", threshold="0.5")

    def test_boolean_threshold_rejected(self) -> None:
        # bool is an int subclass - explicitly rejected as a threshold
        with pytest.raises(InvalidRequestError, match="threshold"):
            QARule(id="r", metric="m", check="ge", threshold=True)

    def test_between_requires_pair(self) -> None:
        with pytest.raises(InvalidRequestError, match="pair"):
            QARule(id="r", metric="m", check="between", threshold=[40.0])

    def test_between_requires_ordered_bounds(self) -> None:
        with pytest.raises(InvalidRequestError, match="low 90.0 > high 50.0"):
            QARule(id="r", metric="m", check="between", threshold=[90.0, 50.0])

    def test_between_normalizes_and_formats(self) -> None:
        rule = QARule(id="r", metric="m", check="between", threshold=[40, 85])
        assert rule.threshold == (40.0, 85.0)
        assert rule.expected_text() == "between 40.0 and 85.0"


class TestRulePackLoader:
    def test_committed_base_timing_pack_loads(self) -> None:
        # regression guard for the committed rule pack
        pack = RulePackLoader.load(REPO_ROOT / "configs" / "rules" / "base_timing.yaml")
        assert pack.name == "base_timing"
        assert pack.version == 1
        assert [rule.id for rule in pack.rules] == [
            "timing.wns.non_negative",
            "timing.tns.non_negative",
        ]
        assert all(rule.severity == "error" for rule in pack.rules)

    def test_missing_pack_name_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "pack.yaml"
        path.write_text("version: 1\nrules: []\n", encoding="utf-8")
        with pytest.raises(InvalidRequestError, match="pack"):
            RulePackLoader.load(path)

    def test_missing_version_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "pack.yaml"
        path.write_text("pack: p\nrules: []\n", encoding="utf-8")
        with pytest.raises(InvalidRequestError, match="version"):
            RulePackLoader.load(path)

    def test_empty_rules_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "pack.yaml"
        path.write_text("pack: p\nversion: 1\nrules: []\n", encoding="utf-8")
        with pytest.raises(InvalidRequestError, match="rules"):
            RulePackLoader.load(path)

    @pytest.mark.parametrize("missing", ["id", "metric", "check", "threshold"])
    def test_missing_required_field_rejected(self, tmp_path: Path, missing: str) -> None:
        fields = {"id": "r", "metric": "m", "check": "ge", "threshold": "0"}
        del fields[missing]
        body = "\n".join(f"    {key}: {value}" for key, value in fields.items())
        path = tmp_path / "pack.yaml"
        path.write_text(
            f"pack: p\nversion: 1\nrules:\n  - {body.lstrip()}\n",
            encoding="utf-8",
        )
        with pytest.raises(InvalidRequestError, match=missing):
            RulePackLoader.load(path)

    def test_invalid_rule_in_pack_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "pack.yaml"
        path.write_text(
            "pack: p\nversion: 1\nrules:\n"
            "  - id: r\n    metric: m\n    check: nonsense\n    threshold: 1\n",
            encoding="utf-8",
        )
        with pytest.raises(InvalidRequestError, match="invalid check"):
            RulePackLoader.load(path)

    def test_duplicate_rule_id_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "pack.yaml"
        path.write_text(
            "pack: p\nversion: 1\nrules:\n"
            "  - id: r\n    metric: m\n    check: ge\n    threshold: 0\n"
            "  - id: r\n    metric: m\n    check: le\n    threshold: 9\n",
            encoding="utf-8",
        )
        with pytest.raises(InvalidRequestError, match="duplicate rule id"):
            RulePackLoader.load(path)
