"""Parsed report data models.

Parser output is FACTS, never verdicts: a metric carries a value and an
explicit unit, and nothing in this module can express PASS/FAIL (that is
the QA engine's job, M5).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Metric", "ParseDiagnostic", "ParsedReport", "TimingPath"]


@dataclass(frozen=True)
class Metric:
    """One structured measurement with an EXPLICIT unit.

    The name embeds the unit per the platform convention
    (``<domain>.<metric>_<unit>``, e.g. ``sta.wns_ns``) so QA rules can
    never misread a number's meaning.
    """

    name: str
    value: int | float
    unit: str
    source: str  # report file the value came from


@dataclass(frozen=True)
class TimingPath:
    """One timing path summary from a report_checks block."""

    startpoint: str
    endpoint: str
    path_group: str
    path_type: str  # "max" | "min" | "" when absent
    slack_ns: float | None  # None when the slack line could not be parsed
    status: str | None  # MET / VIOLATED / UNCONSTRAINED / None


@dataclass(frozen=True)
class ParseDiagnostic:
    """Something the parser could not confirm - never silently ignored."""

    severity: str  # info | warning | error
    code: str
    message: str


@dataclass(frozen=True)
class ParsedReport:
    """Structured facts extracted from one tool report.

    ``tool_version`` is ``None`` unless the report itself states it (a
    version is never guessed from file names or tool configurations).
    """

    tool: str
    source: str
    tool_version: str | None
    metrics: tuple[Metric, ...]
    paths: tuple[TimingPath, ...]
    diagnostics: tuple[ParseDiagnostic, ...]

    def metric_value(self, name: str) -> int | float | None:
        """Return the value of *name*, or ``None`` when not present."""
        for metric in self.metrics:
            if metric.name == name:
                return metric.value
        return None

    def metrics_dict(self) -> dict[str, int | float]:
        """Flat name -> value mapping (unit is embedded in the name)."""
        return {metric.name: metric.value for metric in self.metrics}
