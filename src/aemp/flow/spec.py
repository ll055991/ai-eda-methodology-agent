"""Flow spec loading, structural validation and dependency ordering.

Everything checkable WITHOUT running a tool is checked here - unknown
references, duplicate ids, circular dependencies, missing input files -
so an invalid flow can never reach run creation or EDA execution.
"""

from __future__ import annotations

from pathlib import Path

from aemp.core.config import load_yaml
from aemp.core.errors import InvalidRequestError
from aemp.flow.models import (
    FLOW_SCHEMA_VERSION,
    FlowInput,
    FlowSpec,
    FlowStep,
    InputRef,
    OutputRef,
)

__all__ = ["load_flow_spec"]


def load_flow_spec(path: Path) -> FlowSpec:
    """Load and validate a flow spec; steps come back topologically ordered.

    Raises:
        InvalidRequestError: On version mismatch, malformed structure,
            duplicate step ids, unknown references, circular dependencies
            or missing input files.
    """
    data = load_yaml(Path(path))
    version = data.get("version")
    if version != FLOW_SCHEMA_VERSION:
        msg = (
            f"{path}: unsupported flow version {version!r} "
            f"(expected {FLOW_SCHEMA_VERSION!r})"
        )
        raise InvalidRequestError(msg)

    flow = data.get("flow") or {}
    flow_id = flow.get("id")
    if not isinstance(flow_id, str) or not flow_id.strip():
        msg = f"{path}: 'flow.id' must be a non-empty string"
        raise InvalidRequestError(msg)
    name = flow.get("name", flow_id)
    description = flow.get("description", "")

    inputs = _parse_inputs(path, data.get("inputs") or {})
    steps = _parse_steps(path, data.get("steps"))
    _validate_references(path, inputs, steps)
    ordered = _topo_order(path, steps)
    _validate_input_files(inputs)

    return FlowSpec(
        id=flow_id, name=str(name), description=str(description),
        inputs=inputs, steps=tuple(ordered),
    )


def _parse_inputs(path: Path, data: object) -> dict[str, FlowInput]:
    if not isinstance(data, dict):
        msg = f"{path}: 'inputs' must be a mapping with 'files' and 'params'"
        raise InvalidRequestError(msg)
    files = data.get("files") or {}
    params = data.get("params") or {}
    if not isinstance(files, dict) or not isinstance(params, dict):
        msg = f"{path}: 'inputs.files' and 'inputs.params' must be mappings"
        raise InvalidRequestError(msg)

    inputs: dict[str, FlowInput] = {}
    for name, value in (*files.items(), *params.items()):
        if not isinstance(value, str) or not value:
            msg = f"{path}: input {name!r} must have a non-empty string value"
            raise InvalidRequestError(msg)
    for name, value in files.items():
        if name in inputs:
            msg = f"{path}: duplicate input name {name!r}"
            raise InvalidRequestError(msg)
        inputs[name] = FlowInput(name=str(name), kind="file", value=value)
    for name, value in params.items():
        if name in inputs:
            msg = f"{path}: duplicate input name {name!r}"
            raise InvalidRequestError(msg)
        inputs[name] = FlowInput(name=str(name), kind="param", value=value)
    return inputs


def _parse_steps(path: Path, data: object) -> list[FlowStep]:
    if not isinstance(data, list) or not data:
        msg = f"{path}: 'steps' must be a non-empty list"
        raise InvalidRequestError(msg)
    steps: list[FlowStep] = []
    seen: set[str] = set()
    for raw in data:
        if not isinstance(raw, dict):
            msg = f"{path}: each step must be a mapping"
            raise InvalidRequestError(msg)
        step_id = raw.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            msg = f"{path}: every step needs a non-empty string 'id'"
            raise InvalidRequestError(msg)
        if step_id in seen:
            msg = f"{path}: duplicate step id {step_id!r}"
            raise InvalidRequestError(msg)
        seen.add(step_id)
        tool = raw.get("tool")
        operation = raw.get("operation")
        step_inputs = raw.get("inputs") or {}
        step_outputs = raw.get("outputs") or {}
        if not isinstance(tool, str) or not tool:
            msg = f"{path}: step {step_id!r} needs a non-empty string 'tool'"
            raise InvalidRequestError(msg)
        if not isinstance(operation, str) or not operation:
            msg = f"{path}: step {step_id!r} needs a non-empty string 'operation'"
            raise InvalidRequestError(msg)
        if not isinstance(step_inputs, dict) or not isinstance(step_outputs, dict):
            msg = f"{path}: step {step_id!r} 'inputs'/'outputs' must be mappings"
            raise InvalidRequestError(msg)
        if not all(isinstance(v, str) for v in step_inputs.values()) or not all(
            isinstance(v, str) for v in step_outputs.values()
        ):
            msg = f"{path}: step {step_id!r} 'inputs'/'outputs' values must be strings"
            raise InvalidRequestError(msg)
        steps.append(
            FlowStep(
                id=step_id,
                tool=tool,
                operation=operation,
                inputs=dict(step_inputs),
                outputs=dict(step_outputs),
            )
        )
    return steps


def _validate_references(
    path: Path, inputs: dict[str, FlowInput], steps: list[FlowStep]
) -> None:
    steps_by_id = {step.id: step for step in steps}
    for step in steps:
        for ref in step.refs():
            if isinstance(ref, InputRef):
                if ref.name not in inputs:
                    msg = f"{path}: step {step.id!r} references unknown input {ref.name!r}"
                    raise InvalidRequestError(msg)
            else:
                target = steps_by_id.get(ref.step_id)
                if target is None:
                    msg = (
                        f"{path}: step {step.id!r} references unknown step "
                        f"{ref.step_id!r}"
                    )
                    raise InvalidRequestError(msg)
                if ref.output_name not in target.outputs:
                    msg = (
                        f"{path}: step {step.id!r} references output "
                        f"{ref.output_name!r} which step {ref.step_id!r} "
                        f"does not declare"
                    )
                    raise InvalidRequestError(msg)


def _topo_order(path: Path, steps: list[FlowStep]) -> list[FlowStep]:
    """Kahn topological sort over artifact-reference dependency edges."""
    edges: dict[str, set[str]] = {step.id: set() for step in steps}
    for step in steps:
        for ref in step.refs():
            if isinstance(ref, OutputRef):
                edges[step.id].add(ref.step_id)

    indegree = {step.id: len(edges[step.id]) for step in steps}
    dependents: dict[str, list[str]] = {step.id: [] for step in steps}
    for step in steps:
        for dep in edges[step.id]:
            dependents[dep].append(step.id)

    by_id = {step.id: step for step in steps}
    ready = [step for step in steps if indegree[step.id] == 0]
    ordered: list[FlowStep] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for dependent_id in dependents[current.id]:
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(by_id[dependent_id])

    if len(ordered) != len(steps):
        ordered_ids = {step.id for step in ordered}
        stuck = [step.id for step in steps if step.id not in ordered_ids]
        msg = f"{path}: circular dependency among flow steps: {', '.join(stuck)}"
        raise InvalidRequestError(msg)
    return ordered


def _validate_input_files(inputs: dict[str, FlowInput]) -> None:
    for flow_input in inputs.values():
        if flow_input.kind == "file" and not Path(flow_input.value).is_file():
            msg = f"Flow input file does not exist: {flow_input.value}"
            raise InvalidRequestError(msg)
