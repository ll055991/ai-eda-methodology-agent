"""Shared contract schemas: load and validate (read-only, never copied).

Schemas live in the parent repository's ``contracts/schemas/`` directory
(``AgentConfig.contracts_dir``). This module is the Agent Backend's only
knowledge of contract shapes: it validates documents, it does not define
DTO classes per response.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent_backend.config import default_contracts_dir

SCHEMA_FILES = {
    "run-summary": "run-summary.json",
    "run-list": "run-list.json",
    "run": "run.json",
    "qa": "qa.json",
    "artifact": "artifact.json",
    "artifact-list": "artifact-list.json",
    "error": "error.json",
    "agent-chat-request": "agent-chat-request.json",
    "agent-chat-response": "agent-chat-response.json",
    "agent-chat-resume-request": "agent-chat-resume-request.json",
}

__all__ = ["SCHEMA_FILES", "ContractViolation", "load_schema", "validate_document"]


class ContractViolation(Exception):
    """A document does not satisfy the named contract schema."""

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"{name}: {detail}")
        self.name = name
        self.detail = detail


@functools.cache
def load_schema(name: str, contracts_dir: Path | None = None) -> dict[str, Any]:
    """Load one shared contract schema by logical name."""
    if name not in SCHEMA_FILES:
        raise KeyError(f"Unknown contract schema {name!r}")
    directory = Path(contracts_dir) if contracts_dir is not None else default_contracts_dir()
    path = directory / "schemas" / SCHEMA_FILES[name]
    return json.loads(path.read_text(encoding="utf-8"))


def validate_document(
    name: str, document: Any, contracts_dir: Path | None = None
) -> None:
    """Validate *document* against the named schema.

    Raises:
        ContractViolation: If the document does not satisfy the contract.
    """
    schema = load_schema(name, contracts_dir)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda err: [str(part) for part in err.absolute_path],
    )
    first = next(iter(errors), None)
    if first is not None:
        path = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ContractViolation(name, f"path={path}: {first.message}")
