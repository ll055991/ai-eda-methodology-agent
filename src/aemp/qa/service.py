"""QAService: connects deterministic QA evaluation with the M1 run archive.

Responsibilities (and nothing more):
    run.json metrics -> RulePack -> QAEngine -> findings
    -> freeze the rule pack into the run (qa/rules.frozen.yaml)
    -> archive qa/qa.json as a run artifact
    -> record the QA verdict as the run's ENGINEERING status

The QA verdict NEVER touches execution.status: OpenSTA exiting 0 while
WNS=-0.01 stays "tool executed successfully, engineering verdict failed"
(AGENTS.md §18.8).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from aemp.core.logging import get_logger
from aemp.qa.engine import QAEngine
from aemp.qa.models import QASummary
from aemp.qa.rules import RulePackLoader
from aemp.runs.manager import RunManager

logger = get_logger(__name__)

_QA_SCHEMA_VERSION = "aemp.qa/v0"

__all__ = ["QAService"]


class QAService:
    """Evaluates a rule pack against a run's metrics and archives the QA evidence."""

    def __init__(self, *, manager: RunManager) -> None:
        self._manager = manager

    def evaluate_run(self, run_id: str, rules_path: Path) -> QASummary:
        """Evaluate *rules_path* against the metrics of an existing run.

        Archives the QA evidence into the run directory (rules frozen +
        qa.json) and records the overall verdict as the run's
        engineering status. execution.status is never modified.

        Returns:
            The :class:`QASummary` (also serialized to ``qa/qa.json``).
        """
        run = self._manager.load_run(run_id)
        pack = RulePackLoader.load(Path(rules_path))
        summary = QAEngine().evaluate(run.metrics, pack)

        qa_dir = run.run_dir / "qa"
        qa_dir.mkdir(exist_ok=True)

        # freeze the rules actually used - historical runs cannot be
        # retroactively re-judged by newer rules
        rules_frozen = qa_dir / "rules.frozen.yaml"
        shutil.copyfile(rules_path, rules_frozen)
        run.add_frozen_input(rules_frozen, role="qa_rules")

        document = {
            "schema": _QA_SCHEMA_VERSION,
            "run_id": run.run_id,
            "rule_pack": {"name": pack.name, "version": pack.version},
            "summary": {
                "status": summary.status,
                "total_rules": summary.total_rules,
                "passed": summary.passed,
                "failed": summary.failed,
                "unknown": summary.unknown,
                "warnings": summary.warnings,
            },
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "metric": f.metric,
                    "check": f.check,
                    "threshold": f.threshold,
                    "severity": f.severity,
                    "status": f.status,
                    "expected": f.expected,
                    "actual": f.actual,
                    "message": f.message,
                }
                for f in summary.findings
            ],
        }
        qa_json = qa_dir / "qa.json"
        qa_json.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="",
        )
        run.add_artifact(qa_json, role="qa_report")

        run.record_engineering(
            status=summary.status,
            summary=(
                f"QA pack {pack.name} v{pack.version}: "
                f"{summary.passed} passed, {summary.failed} failed, "
                f"{summary.unknown} unknown, {summary.warnings} warnings"
            ),
        )
        logger.info(
            "QA for run %s: %s (%d findings)", run.run_id, summary.status, len(summary.findings)
        )
        return summary
