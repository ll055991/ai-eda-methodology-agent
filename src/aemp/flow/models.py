"""Flow definition models: spec structure and reference parsing.

v1 deliberately supports whole-value references only -
``${inputs.<name>}`` and ``${steps.<id>.outputs.<name>}``. Interpolation
inside larger strings is not supported: a malformed reference is an
error, never silent string surgery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aemp.core.errors import InvalidRequestError

FLOW_SCHEMA_VERSION = "aemp.flow/v1"

_REF_PATTERN = re.compile(
    r"^\$\{inputs\.(?P<input>[A-Za-z0-9_]+)\}$"
    r"|^\$\{steps\.(?P<step>[A-Za-z0-9_]+)\.outputs\.(?P<output>[A-Za-z0-9_]+)\}$"
)


@dataclass(frozen=True)
class InputRef:
    """Reference to a flow input: ``${inputs.<name>}``."""

    name: str


@dataclass(frozen=True)
class OutputRef:
    """Reference to a prior step's declared output."""

    step_id: str
    output_name: str


Ref = InputRef | OutputRef


def parse_ref(value: str) -> Ref:
    """Parse a whole-value reference.

    Raises:
        InvalidRequestError: If *value* is not a well-formed reference.
    """
    match = _REF_PATTERN.match(value.strip())
    if match is None:
        msg = (
            "Malformed reference (expected ${inputs.X} or "
            f"${{steps.ID.outputs.X}}): {value!r}"
        )
        raise InvalidRequestError(msg)
    if match.group("input") is not None:
        return InputRef(name=match.group("input"))
    return OutputRef(step_id=match.group("step"), output_name=match.group("output"))


@dataclass(frozen=True)
class FlowInput:
    """One flow input: a file (frozen into the run workspace) or a param."""

    name: str
    kind: str  # "file" | "param"
    value: str


@dataclass(frozen=True)
class FlowStep:
    """One step of a flow DEFINITION - it knows nothing about Docker,
    RunManager or SQLite."""

    id: str
    tool: str
    operation: str
    inputs: dict[str, str]  # input name -> reference string
    outputs: dict[str, str]  # output name -> file name (workspace-relative)

    def refs(self) -> list[Ref]:
        return [parse_ref(value) for value in self.inputs.values()]


@dataclass(frozen=True)
class FlowSpec:
    """A validated flow: steps in topological execution order."""

    id: str
    name: str
    description: str
    inputs: dict[str, FlowInput]
    steps: tuple[FlowStep, ...]
