"""QA Rule Engine (M5): metrics + rules -> findings. Deterministic,
data-driven, verdict-producing — and deliberately LLM-free.

Layers stay separated: parsers produce metrics (facts, M4); this package
applies YAML rules (thresholds) and produces findings (verdicts); the
future agent (M6) will explain findings. See AGENTS.md §18.
"""

from aemp.qa.engine import QAEngine
from aemp.qa.models import (
    CHECKS,
    RULE_SEVERITIES,
    QAFinding,
    QARule,
    QASummary,
    RulePack,
)
from aemp.qa.rules import RulePackLoader
from aemp.qa.service import QAService

__all__ = [
    "CHECKS",
    "QAFinding",
    "QARule",
    "QASummary",
    "RULE_SEVERITIES",
    "RulePack",
    "RulePackLoader",
    "QAEngine",
    "QAService",
]
