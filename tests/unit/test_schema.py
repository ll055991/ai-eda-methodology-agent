"""Tests for aemp.schema.validator."""

import pytest

from aemp.core.errors import SchemaValidationError
from aemp.schema.validator import validate_json

# A minimal stand-in schema shaped like the future result envelope
# (closed outer object, open typed data): it tests the validation
# infrastructure only and is NOT the result schema itself.
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["data"],
    "additionalProperties": False,
    "properties": {
        "data": {
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "object",
                    "properties": {
                        "wns": {"type": "number", "minimum": 0.0},
                    },
                },
            },
        },
    },
}


def test_valid_instance_passes() -> None:
    assert validate_json({"data": {"metrics": {"wns": 0.12}}}, SCHEMA) is None


def test_invalid_instance_raises() -> None:
    with pytest.raises(SchemaValidationError):
        validate_json({"data": {"metrics": {"wns": "fast"}}}, SCHEMA)


def test_missing_required_field_raises() -> None:
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_json({}, SCHEMA)
    assert excinfo.value.path == ""
    assert "required" in excinfo.value.detail


def test_nested_validation_error_path_is_reported() -> None:
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_json({"data": {"metrics": {"wns": -0.2}}}, SCHEMA)
    assert excinfo.value.path == "data.metrics.wns"
    assert "less than" in excinfo.value.detail
    assert "path=data.metrics.wns" in str(excinfo.value)
    assert "message=" in str(excinfo.value)


def test_additional_properties_rejected() -> None:
    # additionalProperties violations are reported at the document root,
    # with the offending key named in the message.
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_json({"data": {"metrics": {}}, "surprise": True}, SCHEMA)
    assert excinfo.value.path == ""
    assert "surprise" in excinfo.value.detail


def test_invalid_schema_itself_raises() -> None:
    with pytest.raises(SchemaValidationError, match="Invalid schema"):
        validate_json({}, {"type": "not-a-known-type"})
