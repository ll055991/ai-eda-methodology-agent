"""Report parsers (M4): EDA tool reports -> structured, unit-explicit facts.

Parsers are PURE: report text in, facts out. No Docker, no tool
execution, no RunManager, no SQLite - and no engineering verdicts:
"the report says WNS=-0.42ns" is a fact; "the design fails" is a QA
finding (M5).

v1 registers OpenSTA only; the registry lets openroad/klayout/yosys
parsers plug in later without touching any engine.
"""

from aemp.parsers.base import ReportParser
from aemp.parsers.errors import ReportNotFoundError, ReportParseError
from aemp.parsers.models import (
    Metric,
    ParseDiagnostic,
    ParsedReport,
    TimingPath,
)
from aemp.parsers.opensta import OpenSTAParser
from aemp.parsers.registry import ReportParserRegistry

__all__ = [
    "Metric",
    "OpenSTAParser",
    "ParseDiagnostic",
    "ParsedReport",
    "ReportNotFoundError",
    "ReportParseError",
    "ReportParser",
    "ReportParserRegistry",
    "TimingPath",
]
