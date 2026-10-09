"""The ``aemp.result/v0`` result envelope schema.

This is the versioned contract for every result the platform records.
Semantics: ``execution.status`` (did the tool run?) is independent of
``engineering.status`` (did the engineering assertion hold?); missing
evidence is ``unknown``, never ``fail``. Changing the shape requires a
new schema version - never a silent edit of the bundled JSON file.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any

from aemp.schema.validator import validate_json

SCHEMA_VERSION = "aemp.result/v0"

__all__ = ["SCHEMA_VERSION", "load_result_schema", "validate_result"]


@functools.lru_cache(maxsize=1)
def load_result_schema() -> dict[str, Any]:
    """Load the bundled ``aemp.result/v0`` JSON Schema (draft 2020-12)."""
    text = (resources.files("aemp.schema") / "result_v0.json").read_text(encoding="utf-8")
    return json.loads(text)


def validate_result(result: dict[str, Any]) -> None:
    """Validate a result envelope against ``aemp.result/v0``.

    Raises:
        SchemaValidationError: If *result* violates the envelope schema.
    """
    validate_json(result, load_result_schema())
