"""Unit tests for QAService: run archive integration (qa.json, frozen
rules, engineering status recording) - fake metrics, no Docker."""

import json
from pathlib import Path

import pytest

from aemp.core.config import Settings
from aemp.core.errors import InvalidRequestError
from aemp.qa.service import QAService
from aemp.runs.manager import RunManager
from aemp.schema.result import validate_result

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "configs" / "rules" / "base_timing.yaml"


@pytest.fixture
def manager(tmp_path: Path) -> RunManager:
    return RunManager(Settings(project_root=tmp_path))


def make_run_with_metrics(manager: RunManager, metrics: dict[str, float]):
    run = manager.create_run("opensta.timing", tool_name="opensta")
    run.record_step("sta")
    run.mark_running()
    run.record_metrics(metrics)
    run.finalize("success", engineering_status="unknown")
    return run


class TestEvaluateRun:
    def test_clean_timing_passes_and_archives(self, manager: RunManager) -> None:
        run = make_run_with_metrics(
            manager, {"sta.wns_ns": 0.29, "sta.tns_ns": 0.0}
        )
        summary = QAService(manager=manager).evaluate_run(run.run_id, RULES)

        assert summary.status == "pass"
        assert summary.passed == 2 and summary.failed == 0

        # qa evidence archived inside the run
        qa_json = run.run_dir / "qa" / "qa.json"
        rules_frozen = run.run_dir / "qa" / "rules.frozen.yaml"
        assert qa_json.is_file() and rules_frozen.is_file()
        document = json.loads(qa_json.read_text(encoding="utf-8"))
        assert document["schema"] == "aemp.qa/v0"
        assert document["run_id"] == run.run_id
        assert document["rule_pack"] == {"name": "base_timing", "version": 1}
        assert document["summary"]["status"] == "pass"
        assert len(document["findings"]) == 2
        # frozen rules are byte-identical to the source pack
        assert rules_frozen.read_bytes() == RULES.read_bytes()

        # engineering status recorded from QA; execution untouched
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        validate_result(envelope)
        assert envelope["engineering"]["status"] == "pass"
        assert envelope["execution"]["status"] == "success"
        assert "base_timing" in envelope["engineering"]["summary"]
        # artifacts list now includes the QA report
        roles = {a["role"] for a in envelope["artifacts"]}
        assert "qa_report" in roles
        roles_in = {i["role"] for i in envelope["inputs"]}
        assert "qa_rules" in roles_in

    def test_violated_timing_fails_but_execution_stays_success(
        self, manager: RunManager
    ) -> None:
        run = make_run_with_metrics(
            manager, {"sta.wns_ns": -0.01, "sta.tns_ns": -0.02}
        )
        summary = QAService(manager=manager).evaluate_run(run.run_id, RULES)

        assert summary.status == "fail"
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        # the M1 dual-state principle: tool ran fine, engineering failed
        assert envelope["execution"]["status"] == "success"
        assert envelope["engineering"]["status"] == "fail"
        qa_document = json.loads(
            (run.run_dir / "qa" / "qa.json").read_text(encoding="utf-8")
        )
        findings = {f["rule_id"]: f for f in qa_document["findings"]}
        assert findings["timing.wns.non_negative"]["actual"] == pytest.approx(-0.01)
        assert findings["timing.wns.non_negative"]["status"] == "fail"

    def test_missing_metrics_yield_unknown_overall(self, manager: RunManager) -> None:
        run = make_run_with_metrics(manager, {})  # parser produced nothing
        summary = QAService(manager=manager).evaluate_run(run.run_id, RULES)
        assert summary.status == "unknown"
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        assert envelope["engineering"]["status"] == "unknown"

    def test_unknown_run_rejected(self, manager: RunManager) -> None:
        with pytest.raises(InvalidRequestError, match="does not exist"):
            QAService(manager=manager).evaluate_run("20260101T000000Z-nope", RULES)

    def test_invalid_rule_pack_rejected_before_archiving(
        self, manager: RunManager, tmp_path: Path
    ) -> None:
        run = make_run_with_metrics(manager, {"sta.wns_ns": 0.1})
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "pack: bad\nversion: 1\nrules:\n"
            "  - id: r\n    metric: m\n    check: nonsense\n    threshold: 0\n",
            encoding="utf-8",
        )
        with pytest.raises(InvalidRequestError, match="invalid check"):
            QAService(manager=manager).evaluate_run(run.run_id, bad)
        # nothing archived for a rejected pack
        assert not (run.run_dir / "qa" / "qa.json").exists()


class TestLoadRun:
    def test_load_run_round_trips_metrics(self, manager: RunManager) -> None:
        run = make_run_with_metrics(manager, {"sta.wns_ns": 0.29})
        loaded = manager.load_run(run.run_id)
        assert loaded.metrics == {"sta.wns_ns": 0.29}
        assert loaded.status == "success"

    def test_load_missing_run_rejected(self, manager: RunManager) -> None:
        with pytest.raises(InvalidRequestError, match="does not exist"):
            manager.load_run("20260101T000000Z-nope")
