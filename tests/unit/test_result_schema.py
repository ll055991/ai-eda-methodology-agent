"""Tests for the aemp.result/v0 result envelope schema."""

import pytest

from aemp.core.errors import SchemaValidationError
from aemp.schema.result import SCHEMA_VERSION, validate_result


def minimal_envelope() -> dict:
    """A minimal valid run-level envelope (M1 shape)."""
    return {
        "schema": SCHEMA_VERSION,
        "run_id": "20261007T103000Z-a1b2c3",
        "step_id": None,
        "operation": "example",
        "tool": {"name": "example", "version": "unknown", "path": None, "argv": []},
        "execution": {
            "status": "unknown",
            "exit_code": None,
            "duration_ms": None,
            "command": None,
            "cwd": None,
            "log_path": None,
            "log_bytes": 0,
            "log_truncated": False,
        },
        "engineering": {"status": "unknown", "summary": None},
        "inputs": [],
        "artifacts": [],
        "metrics": {},
        "diagnostics": [],
        "provenance": {"created_at": "2026-10-07T10:30:00Z"},
    }


def test_valid_envelope_passes() -> None:
    assert validate_result(minimal_envelope()) is None


def test_schema_constant_is_enforced() -> None:
    envelope = minimal_envelope()
    envelope["schema"] = "other.result/v9"
    with pytest.raises(SchemaValidationError):
        validate_result(envelope)


def test_missing_required_field_raises() -> None:
    envelope = minimal_envelope()
    del envelope["engineering"]
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_result(envelope)
    assert excinfo.value.path == ""
    assert "engineering" in excinfo.value.detail


def test_execution_status_enum() -> None:
    envelope = minimal_envelope()
    envelope["execution"]["status"] = "running"  # lifecycle state, not an execution state
    with pytest.raises(SchemaValidationError):
        validate_result(envelope)


@pytest.mark.parametrize("status", ["success", "failed", "unknown"])
def test_execution_status_allowed_values(status: str) -> None:
    envelope = minimal_envelope()
    envelope["execution"]["status"] = status
    assert validate_result(envelope) is None


@pytest.mark.parametrize("status", ["success", "failed", "unknown"])
def test_engineering_status_allowed_values(status: str) -> None:
    envelope = minimal_envelope()
    envelope["engineering"]["status"] = status
    assert validate_result(envelope) is None


def test_engineering_status_rejects_lifecycle_values() -> None:
    envelope = minimal_envelope()
    envelope["engineering"]["status"] = "created"
    with pytest.raises(SchemaValidationError):
        validate_result(envelope)


def test_unknown_is_valid_for_both_statuses() -> None:
    envelope = minimal_envelope()
    envelope["execution"]["status"] = "unknown"
    envelope["engineering"]["status"] = "unknown"
    assert validate_result(envelope) is None


def test_run_id_pattern_enforced() -> None:
    envelope = minimal_envelope()
    envelope["run_id"] = "1"  # database-style id is forbidden
    with pytest.raises(SchemaValidationError):
        validate_result(envelope)


def test_missing_file_must_not_carry_hash() -> None:
    envelope = minimal_envelope()
    envelope["inputs"] = [
        {"role": "input", "path": "data/missing.v", "exists": False, "sha256": "0" * 64}
    ]
    with pytest.raises(SchemaValidationError):
        validate_result(envelope)


def test_existing_file_requires_bytes_and_sha256() -> None:
    envelope = minimal_envelope()
    envelope["inputs"] = [{"role": "input", "path": "data/input.v", "exists": True}]
    with pytest.raises(SchemaValidationError):
        validate_result(envelope)


def test_existing_file_record_valid() -> None:
    envelope = minimal_envelope()
    envelope["inputs"] = [
        {
            "role": "input",
            "path": "data/input.v",
            "exists": True,
            "bytes": 10,
            "sha256": "a" * 64,
        }
    ]
    assert validate_result(envelope) is None


def test_metrics_is_an_open_area() -> None:
    envelope = minimal_envelope()
    envelope["metrics"] = {"anything": {"nested": [1, 2, 3]}}
    assert validate_result(envelope) is None
