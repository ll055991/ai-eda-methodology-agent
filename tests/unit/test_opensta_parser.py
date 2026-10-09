"""Unit tests for OpenSTAParser against REAL OpenSTA 3.1.0 fixtures.

Fixtures come from actual platform runs (see tests/fixtures/reports/
README.md) - never invented. Synthetic cases reuse the exact real line
format to test number formats and missing-metric handling.
"""

from pathlib import Path

import pytest

from aemp.core.errors import InvalidRequestError
from aemp.parsers import (
    OpenSTAParser,
    ReportNotFoundError,
    ReportParseError,
    ReportParserRegistry,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "reports" / "opensta"
BASIC = FIXTURES / "timing_basic.rpt"
VIOLATED = FIXTURES / "timing_violated.rpt"


@pytest.fixture(scope="module")
def basic_report():
    return OpenSTAParser().parse(BASIC)


@pytest.fixture(scope="module")
def violated_report():
    return OpenSTAParser().parse(VIOLATED)


class TestRealFixtures:
    def test_basic_metrics_match_the_real_report(
        self, basic_report
    ) -> None:
        # values cross-checked by hand against the fixture lines:
        #   "worst slack max 0.29" / "tns max 0.00"
        assert basic_report.metric_value("sta.wns_ns") == pytest.approx(0.29)
        assert basic_report.metric_value("sta.tns_ns") == pytest.approx(0.0)
        assert basic_report.metric_value("sta.path_count") == 2
        assert basic_report.metric_value("sta.violation_count") == 0

    def test_basic_metrics_carry_units(self, basic_report) -> None:
        metrics = {m.name: m for m in basic_report.metrics}
        assert metrics["sta.wns_ns"].unit == "ns"
        assert metrics["sta.wns_ns"].source == "timing_basic.rpt"
        assert metrics["sta.path_count"].unit == "count"

    def test_basic_paths_parsed(self, basic_report) -> None:
        assert len(basic_report.paths) == 2
        data_path = basic_report.paths[1]
        assert data_path.startpoint.startswith("_15_")
        assert data_path.endpoint.startswith("_17_")
        assert data_path.path_group == "clk"
        assert data_path.path_type == "max"
        assert data_path.status == "MET"
        assert data_path.slack_ns == pytest.approx(0.29)

    def test_violated_fixture_negative_metrics(self, violated_report) -> None:
        assert violated_report.metric_value("sta.wns_ns") == pytest.approx(-0.01)
        assert violated_report.metric_value("sta.tns_ns") == pytest.approx(-0.02)
        assert violated_report.metric_value("sta.violation_count") == 1

    def test_violated_path_marks_the_violation(self, violated_report) -> None:
        violated = [p for p in violated_report.paths if p.status == "VIOLATED"]
        assert len(violated) == 1
        assert violated[0].slack_ns == pytest.approx(-0.01)

    def test_tool_version_is_none_when_report_has_no_banner(self, basic_report) -> None:
        # -no_splash reports carry no version banner: never guessed
        assert basic_report.tool_version is None
        assert basic_report.tool == "opensta"


class TestNumberFormats:
    """Synthetic minimal reports in the REAL line format."""

    def parse_line(self, line: str) -> object:
        return OpenSTAParser().parse_text(line)

    def test_plus_sign_normalized(self) -> None:
        report = OpenSTAParser().parse_text("worst slack max +0.29\ntns max +0.00\n")
        assert report.metric_value("sta.wns_ns") == pytest.approx(0.29)

    def test_trailing_digits_normalized(self) -> None:
        report = OpenSTAParser().parse_text("worst slack max 0.290\ntns max 0.000\n")
        assert report.metric_value("sta.wns_ns") == pytest.approx(0.29)

    def test_negative_wns_parsed_as_negative(self) -> None:
        report = OpenSTAParser().parse_text("worst slack max -1.2\ntns max -2.7\n")
        assert report.metric_value("sta.wns_ns") == pytest.approx(-1.2)
        assert report.metric_value("sta.tns_ns") == pytest.approx(-2.7)

    def test_tolerant_to_extra_whitespace_and_tabs(self) -> None:
        report = OpenSTAParser().parse_text("  worst slack\t max \t 0.29  \n  tns  max  0.0 \n")
        assert report.metric_value("sta.wns_ns") == pytest.approx(0.29)


class TestHonestFailures:
    def test_empty_report_raises(self) -> None:
        with pytest.raises(ReportParseError, match="empty"):
            OpenSTAParser().parse_text("")

    def test_whitespace_only_report_raises(self) -> None:
        with pytest.raises(ReportParseError, match="empty"):
            OpenSTAParser().parse_text("   \n\t\n")

    def test_garbled_report_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ReportParseError, match="Unrecognized"):
            OpenSTAParser().parse_text("hello world, nothing EDA here <<<>>>\n")

    def test_missing_wns_is_absent_with_diagnostic_not_zero(self, basic_report) -> None:
        # real fixture with the worst-slack line removed
        text = BASIC.read_text(encoding="utf-8").replace("worst slack max 0.29\n", "")
        report = OpenSTAParser().parse_text(text, source="no_wns.rpt")
        assert report.metric_value("sta.wns_ns") is None  # unknown != 0
        assert report.metric_value("sta.tns_ns") == pytest.approx(0.0)  # still parsed
        codes = {d.code for d in report.diagnostics}
        assert "metric_unavailable" in codes

    def test_missing_tns_is_absent_with_diagnostic(self) -> None:
        report = OpenSTAParser().parse_text("worst slack max 0.29\n")
        assert report.metric_value("sta.tns_ns") is None
        assert report.metric_value("sta.wns_ns") == pytest.approx(0.29)
        assert any(d.code == "metric_unavailable" for d in report.diagnostics)

    def test_missing_file_raises_dedicated_error(self, tmp_path: Path) -> None:
        with pytest.raises(ReportNotFoundError, match="does not exist"):
            OpenSTAParser().parse(tmp_path / "nope.rpt")

    def test_multiple_paths_all_parsed(self) -> None:
        # real-format report with three paths (one VIOLATED in the middle)
        text = (
            "Startpoint: a (input port clocked by clk)\n"
            "Endpoint: b (DFF_X1)\nPath Group: clk\nPath Type: max\n"
            "           0.10   slack (MET)\n"
            "Startpoint: c (rising edge-triggered flip-flop clocked by clk)\n"
            "Endpoint: d (DFF_X1)\nPath Group: clk\nPath Type: max\n"
            "           -0.42   slack (VIOLATED)\n"
            "Startpoint: e (rising edge-triggered flip-flop clocked by clk)\n"
            "Endpoint: f (DFF_X1)\nPath Group: clk\nPath Type: max\n"
            "           0.05   slack (MET)\n"
            "worst slack max -0.42\ntns max -0.42\n"
        )
        report = OpenSTAParser().parse_text(text)
        assert report.metric_value("sta.path_count") == 3
        assert report.metric_value("sta.violation_count") == 1
        assert report.paths[1].slack_ns == pytest.approx(-0.42)
        assert report.metric_value("sta.wns_ns") == pytest.approx(-0.42)


class TestRegistry:
    def test_register_and_resolve(self) -> None:
        registry = ReportParserRegistry()
        registry.register("opensta", OpenSTAParser())
        assert isinstance(registry.resolve("opensta"), OpenSTAParser)

    def test_unknown_parser_rejected(self) -> None:
        registry = ReportParserRegistry()
        with pytest.raises(InvalidRequestError, match="Unknown report parser"):
            registry.resolve("openroad")

    def test_duplicate_registration_rejected(self) -> None:
        registry = ReportParserRegistry()
        registry.register("opensta", OpenSTAParser())
        with pytest.raises(InvalidRequestError, match="already registered"):
            registry.register("opensta", OpenSTAParser())
