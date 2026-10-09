"""Schema validation infrastructure.

M0 scope only: validate a JSON-like instance against a schema and raise a
rich :class:`~aemp.core.errors.SchemaValidationError`. The
``aemp.result/v0`` result envelope is a later milestone and is
intentionally not defined here.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from aemp.core.errors import SchemaValidationError

__all__ = ["validate_json"]


def _dot_path(error: ValidationError) -> str:
    """Render a validator error's instance location as a dot-separated path."""
    return ".".join(str(part) for part in error.absolute_path)


def validate_json(instance: Any, schema: dict[str, Any]) -> None:
    """Validate *instance* against *schema* (JSON Schema draft 2020-12).

    Args:
        instance: The JSON-like document to validate.
        schema: The JSON Schema to validate against.

    Raises:
        SchemaValidationError: If the schema itself is invalid, or if the
            instance violates it. The exception exposes ``path`` (dot path
            into the instance, ``""`` for whole-document errors) and
            ``detail`` (the first validator message); its string form
            reads::

                Schema validation failed:
                path=data.metrics.wns
                message=-0.2 is less than the minimum of 0
    """
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SchemaValidationError(
            f"Invalid schema:\npath=<schema>\nmessage={exc.message}",
            path="",
            detail=exc.message,
        ) from exc

    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda err: [str(part) for part in err.absolute_path],
    )
    first = next(iter(errors), None)
    if first is None:
        return
    path = _dot_path(first)
    detail = first.message
    raise SchemaValidationError(
        f"Schema validation failed:\npath={path or '<root>'}\nmessage={detail}",
        path=path,
        detail=detail,
    )
