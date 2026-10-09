"""Tests for the run manager (all filesystem work inside tmp_path; no EDA tools)."""

import json
import re
from pathlib import Path

import pytest

from aemp.core.config import Settings
from aemp.core.errors import InvalidRequestError
from aemp.core.hashing import sha256_bytes
from aemp.runs.manager import RunManager
from aemp.schema.result import validate_result


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(project_root=tmp_path)


class TestCreateRun:
    def test_builds_directory_tree(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        for sub in ("logs", "artifacts", "results", "qa"):
            assert (run.run_dir / sub).is_dir()
        assert (run.run_dir / "run.json").is_file()
        assert run.run_dir.parent == settings.runs_dir

    def test_run_id_format(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        assert re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}", run.run_id)

    def test_run_ids_are_unique(self, settings: Settings) -> None:
        manager = RunManager(settings)
        ids = {manager.create_run("example").run_id for _ in range(3)}
        assert len(ids) == 3

    def test_duplicate_explicit_run_id_rejected(self, settings: Settings) -> None:
        manager = RunManager(settings)
        manager.create_run("example", run_id="20261007T103000Z-a1b2c3")
        with pytest.raises(InvalidRequestError, match="already exists"):
            manager.create_run("example", run_id="20261007T103000Z-a1b2c3")

    def test_malformed_run_id_rejected(self, settings: Settings) -> None:
        with pytest.raises(InvalidRequestError, match="Malformed run id"):
            RunManager(settings).create_run("example", run_id="1")

    def test_empty_operation_rejected(self, settings: Settings) -> None:
        with pytest.raises(InvalidRequestError, match="operation"):
            RunManager(settings).create_run("  ")

    def test_created_envelope_is_valid_and_unexecuted(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        on_disk = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        validate_result(on_disk)  # must not raise
        assert on_disk["schema"] == "aemp.result/v0"
        assert on_disk["run_id"] == run.run_id
        assert on_disk["execution"]["status"] == "unknown"
        assert on_disk["engineering"]["status"] == "unknown"
        assert on_disk["provenance"]["run_status"] == "created"
        assert on_disk["provenance"]["created_at"].endswith("Z")

    def test_index_row_created_after_filesystem(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        rows = RunManager(settings).index.list_runs()
        assert len(rows) == 1
        assert rows[0]["run_id"] == run.run_id
        assert rows[0]["status"] == "created"


class TestEvidence:
    def test_add_input_hashes_file(self, settings: Settings, tmp_path: Path) -> None:
        content = b"module demo;\nendmodule\n"
        source = tmp_path / "data" / "input.v"
        source.parent.mkdir(parents=True)
        source.write_bytes(content)

        run = RunManager(settings).create_run("example")
        record = run.add_input(source, role="rtl")

        assert record.exists is True
        assert record.bytes == len(content)
        assert record.sha256 == sha256_bytes(content)
        assert record.path == "data/input.v"  # project-relative, POSIX separators
        on_disk = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        assert on_disk["inputs"][0]["sha256"] == record.sha256

    def test_missing_input_recorded_without_hash(self, settings: Settings, tmp_path: Path) -> None:
        run = RunManager(settings).create_run("example")
        record = run.add_input(tmp_path / "data" / "missing.v")

        assert record.exists is False
        assert record.bytes is None
        assert record.sha256 is None
        on_disk = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        assert "sha256" not in on_disk["inputs"][0]
        validate_result(on_disk)  # still a valid envelope

    def test_missing_artifact_rejected(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        missing = run.run_dir / "artifacts" / "nope.v"
        with pytest.raises(InvalidRequestError, match="does not exist"):
            run.add_artifact(missing, role="netlist")

    def test_add_artifact_stores_run_relative_path(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        artifact = run.run_dir / "artifacts" / "design.v"
        artifact.write_bytes(b"netlist content\n")

        record = run.add_artifact(artifact, role="netlist")

        assert record.path == "artifacts/design.v"
        assert record.sha256 == sha256_bytes(b"netlist content\n")

    def test_freeze_file_copies_and_records(self, settings: Settings, tmp_path: Path) -> None:
        spec = tmp_path / "configs" / "flows" / "demo.yaml"
        spec.parent.mkdir(parents=True)
        spec.write_text("operation: example\n", encoding="utf-8")

        run = RunManager(settings).create_run("example", flow_spec=spec)
        frozen = run.run_dir / "flow.spec.frozen.yaml"

        assert frozen.read_text(encoding="utf-8") == "operation: example\n"
        inputs = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))["inputs"]
        flow_records = [entry for entry in inputs if entry["role"] == "flow_spec"]
        assert len(flow_records) == 1
        assert flow_records[0]["path"] == "flow.spec.frozen.yaml"
        assert flow_records[0]["sha256"]


class TestFinalize:
    def test_finalize_success(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        run.finalize(
            "success",
            engineering_status="unknown",
            engineering_summary="M1 demo: no engineering judgment",
        )

        on_disk = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        assert on_disk["execution"]["status"] == "success"
        assert on_disk["execution"]["duration_ms"] >= 0
        assert on_disk["engineering"]["status"] == "unknown"  # execution != engineering
        assert on_disk["provenance"]["run_status"] == "success"
        assert on_disk["provenance"]["finished_at"] is not None
        assert run.status == "success"
        assert RunManager(settings).index.list_runs()[0]["status"] == "success"

    def test_execution_failed_with_independent_engineering_verdict(
        self, settings: Settings
    ) -> None:
        run = RunManager(settings).create_run("example")
        run.finalize("failed", engineering_status="failed", engineering_summary="broken")

        on_disk = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        assert on_disk["execution"]["status"] == "failed"
        assert on_disk["engineering"]["status"] == "failed"

    def test_invalid_status_rejected(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        with pytest.raises(InvalidRequestError, match="Finalize status"):
            run.finalize("cancelled")

    def test_invalid_engineering_status_rejected(self, settings: Settings) -> None:
        run = RunManager(settings).create_run("example")
        with pytest.raises(InvalidRequestError, match="engineering status"):
            run.finalize("success", engineering_status="running")
