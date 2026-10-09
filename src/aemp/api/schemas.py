"""Contract schema loading and validation.

``contracts/schemas/*.json`` is the normative, language-neutral contract;
this module only loads and validates against it, reusing the M0 generic
JSON Schema validator. No DTO shape is defined in Python.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from aemp.core.config import discover_settings
from aemp.core.errors import ConfigurationError, InvalidRequestError
from aemp.schema.validator import validate_json

__all__ = [
    "CONTRACT_SCHEMA_FILES",
    "default_contracts_dir",
    "load_contract_schema",
    "validate_contract",
]

CONTRACT_SCHEMA_FILES = {
    "flow": "flow.json",
    "run": "run.json",
    "run-summary": "run-summary.json",
    "run-list": "run-list.json",
    "qa": "qa.json",
    "qa-request": "qa-request.json",
    "artifact": "artifact.json",
    "artifact-list": "artifact-list.json",
    "error": "error.json",
    "agent-chat-request": "agent-chat-request.json",
    "agent-chat-response": "agent-chat-response.json",
}


def default_contracts_dir() -> Path:
    """The ``contracts/`` directory of the platform project root."""
    return discover_settings().project_root / "contracts"


@functools.cache
def load_contract_schema(name: str, contracts_dir: Path | None = None) -> dict[str, Any]:
    """Load one contract schema by logical name.

    Raises:
        InvalidRequestError: On an unknown schema name.
        ConfigurationError: If the schema file is missing.
    """
    try:
        filename = CONTRACT_SCHEMA_FILES[name]
    except KeyError:
        msg = f"Unknown contract schema {name!r} (known: {sorted(CONTRACT_SCHEMA_FILES)})"
        raise InvalidRequestError(msg) from None
    directory = Path(contracts_dir) if contracts_dir is not None else default_contracts_dir()
    path = directory / "schemas" / filename
    if not path.is_file():
        msg = f"Contract schema not found: {path}"
        raise ConfigurationError(msg)
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract(
    name: str, document: Any, contracts_dir: Path | None = None
) -> None:
    """Validate *document* against the named contract schema.

    Raises:
        SchemaValidationError: If the document violates the contract.
    """
    validate_json(document, load_contract_schema(name, contracts_dir))
