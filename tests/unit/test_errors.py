"""Tests for aemp.core.errors."""

import pytest

from aemp.core.errors import (
    AEMPError,
    ConfigurationError,
    InvalidRequestError,
    SchemaValidationError,
    StorageError,
    ToolNotFoundError,
)


@pytest.mark.parametrize(
    "exc_type",
    [
        ConfigurationError,
        SchemaValidationError,
        ToolNotFoundError,
        InvalidRequestError,
        StorageError,
    ],
)
def test_all_public_errors_subclass_aemp_error(exc_type: type[Exception]) -> None:
    assert issubclass(exc_type, AEMPError)


def test_schema_validation_error_carries_path_and_detail() -> None:
    exc = SchemaValidationError("boom", path="data.metrics.wns", detail="-0.2 is too small")
    assert exc.path == "data.metrics.wns"
    assert exc.detail == "-0.2 is too small"
    assert "boom" in str(exc)


def test_schema_validation_error_defaults() -> None:
    exc = SchemaValidationError("whole document is bad")
    assert exc.path == ""
    assert exc.detail == ""


def test_tool_not_found_error_names_the_tool() -> None:
    exc = ToolNotFoundError("yosys")
    assert exc.tool == "yosys"
    assert "yosys" in str(exc)
    assert isinstance(exc, AEMPError)


def test_tool_not_found_error_custom_message() -> None:
    exc = ToolNotFoundError("yosys", message="yosys >= 0.36 required")
    assert str(exc) == "yosys >= 0.36 required"
