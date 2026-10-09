"""FlowEngine: validated flow specs -> steps -> drivers -> one tracked Run.

The engine is pure ORCHESTRATION (AGENTS.md §18): it resolves the flow
definition, picks drivers via the registry and delegates run bookkeeping
to RunManager. It never starts a process itself and never touches
run.json/SQLite outside Run's record methods.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from aemp.core.config import Settings
from aemp.core.errors import InvalidRequestError
from aemp.core.logging import get_logger
from aemp.drivers.errors import ToolEnvironmentError
from aemp.drivers.opensta import OpenSTADriver
from aemp.drivers.yosys import YosysDriver
from aemp.flow.models import (
    FlowSpec,
    FlowStep,
    InputRef,
    parse_ref,
)
from aemp.flow.operations import OpenSTATimingOperation, YosysSynthesizeOperation
from aemp.flow.spec import load_flow_spec
from aemp.runs.manager import Run, RunManager
from aemp.service import ToolDriverProtocol

logger = get_logger(__name__)


@dataclass(frozen=True)
class ToolEntry:
    """One registered tool: its driver and the operation it can perform."""

    driver: ToolDriverProtocol
    operation: object  # a StepOperation (structural; see flow.operations)


class ToolRegistry:
    """Name -> (driver, operation). No if/elif chains in the engine."""

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(self, name: str, driver: ToolDriverProtocol, operation: object) -> None:
        if name in self._entries:
            msg = f"Tool already registered: {name!r}"
            raise InvalidRequestError(msg)
        self._entries[name] = ToolEntry(driver=driver, operation=operation)

    def resolve(self, name: str) -> ToolEntry:
        entry = self._entries.get(name)
        if entry is None:
            msg = f"Unknown tool: {name!r} (registered: {sorted(self._entries)})"
            raise InvalidRequestError(msg)
        return entry


def default_registry(settings: Settings) -> ToolRegistry:
    """Registry wired with the platform's built-in tools from configs/tools."""
    registry = ToolRegistry()
    registry.register(
        "yosys",
        YosysDriver.from_config(settings.config_dir / "tools" / "yosys.yaml"),
        YosysSynthesizeOperation(),
    )
    registry.register(
        "opensta",
        OpenSTADriver.from_config(settings.config_dir / "tools" / "opensta.yaml"),
        OpenSTATimingOperation(),
    )
    return registry


class FlowEngine:
    """Executes a validated flow spec as one tracked Run (fail-fast)."""

    def __init__(self, *, registry: ToolRegistry, manager: RunManager) -> None:
        self._registry = registry
        self._manager = manager

    def run(
        self,
        spec_path: Path,
        *,
        timeout_s: float = 900.0,
        run_id: str | None = None,
    ) -> Run:
        """Load, validate and execute a flow spec as one Run.

        Validation (references, dependencies, cycles, input files, tool
        availability) completes BEFORE the run is created or any tool
        starts. Steps run in topological order with fail-fast semantics:
        the first tool failure or missing declared output stops the flow
        and fails the run.

        Returns:
            The finalized :class:`Run`.
        """
        spec = load_flow_spec(Path(spec_path))
        for step in spec.steps:  # tool availability is a pre-run check
            self._registry.resolve(step.tool)

        run = self._manager.create_run(
            f"flow.{spec.id}", tool_name=spec.id, run_id=run_id
        )
        run.freeze_file(Path(spec_path), destination_name="flow.spec.frozen.yaml")
        self._freeze_file_inputs(run, spec)
        run.mark_running()

        steps_summary: list[dict[str, object]] = []
        produced: dict[str, dict[str, str]] = {}  # step id -> output name -> path
        flow_ok = True
        for step in spec.steps:
            entry = self._registry.resolve(step.tool)
            resolved = self._resolve_inputs(spec, step, produced)
            script_path, tool_argv = entry.operation.prepare(run, step, resolved)
            run.record_step(step.id)
            try:
                version = entry.driver.get_version()
                result = entry.driver.run_script(
                    workspace=run.run_dir, script=script_path, timeout_s=timeout_s
                )
            except ToolEnvironmentError as exc:
                logger.warning("flow %s: step %s environment failure", spec.id, step.id)
                run.add_diagnostic(severity="error", code="environment", message=str(exc))
                run.finalize(
                    "failed",
                    execution_status="unknown",  # this tool never ran
                    engineering_summary=f"EDA environment failure: {exc}"[:4000],
                )
                raise

            stdout_log = run.run_dir / "logs" / f"{step.id}.stdout.log"
            stderr_log = run.run_dir / "logs" / f"{step.id}.stderr.log"
            stdout_log.write_text(result.stdout, encoding="utf-8", newline="")
            stderr_log.write_text(result.stderr, encoding="utf-8", newline="")
            run.record_tool(
                name=entry.operation.tool_name, version=version, path=None, argv=tool_argv
            )
            run.record_execution(
                status=result.status,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                command=result.command,
                cwd=str(result.cwd),
                log_path=stdout_log.relative_to(run.run_dir).as_posix(),
                log_bytes=stdout_log.stat().st_size,
                log_truncated=result.stdout_truncated,
            )
            run.add_artifact(stdout_log, role=f"{step.id}.stdout")
            run.add_artifact(stderr_log, role=f"{step.id}.stderr")
            if result.timed_out:
                run.add_diagnostic(
                    severity="error",
                    code="timeout",
                    message=f"step {step.id!r} timed out; the container was removed",
                )

            produced_files = entry.operation.collect_outputs(run, step, result)
            for path, role in produced_files:
                if path.is_file():  # missing files are handled below, honestly
                    run.add_artifact(path, role=role)

            step_summary: dict[str, object] = {
                "id": step.id,
                "tool": step.tool,
                "status": result.status,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
            }

            if not result.succeeded:
                flow_ok = False
                steps_summary.append(step_summary)
                logger.warning(
                    "flow %s: step %s failed - fail-fast, remaining steps skipped",
                    spec.id,
                    step.id,
                )
                break

            # honest artifact validation: every declared output must exist
            missing = [
                name
                for name, filename in step.outputs.items()
                if not (run.run_dir / "artifacts" / filename).is_file()
            ]
            for out_name in missing:
                run.add_diagnostic(
                    severity="error",
                    code="missing_artifact",
                    message=(
                        f"step {step.id!r} succeeded but declared output "
                        f"{out_name!r} (artifacts/{step.outputs[out_name]}) "
                        f"does not exist"
                    ),
                )
            step_summary["missing_artifacts"] = missing
            steps_summary.append(step_summary)
            if missing:
                flow_ok = False
                break  # fail-fast: never feed a broken interface downstream

            produced[step.id] = {
                name: f"artifacts/{filename}" for name, filename in step.outputs.items()
            }

            metrics = entry.operation.parse_metrics(run.run_dir, step)
            if metrics:
                run.record_metrics(metrics)

        run.record_steps(steps_summary)
        run.finalize(
            "success" if flow_ok else "failed",
            engineering_status="unknown",
            engineering_summary=(
                "flow execution facts only; engineering judgment arrives "
                "with the parser and the QA engine (M4/M5)"
            ),
        )
        return run

    # -- internals --------------------------------------------------------

    def _freeze_file_inputs(self, run: Run, spec: FlowSpec) -> dict[str, str]:
        """Copy file inputs into inputs/ and return their container paths."""
        inputs_dir = run.run_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)
        container_paths: dict[str, str] = {}
        for flow_input in spec.inputs.values():
            if flow_input.kind != "file":
                continue
            target = inputs_dir / Path(flow_input.value).name
            shutil.copyfile(flow_input.value, target)
            run.add_frozen_input(target, role=flow_input.name)
            container_paths[flow_input.name] = target.relative_to(run.run_dir).as_posix()
        return container_paths

    def _resolve_inputs(
        self,
        spec: FlowSpec,
        step: FlowStep,
        produced: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        """Resolve a step's input references to container-side paths/values.

        Raises:
            InvalidRequestError: On any unknown reference (defense in
                depth - load_flow_spec already validated the spec).
        """
        resolved: dict[str, str] = {}
        refs = {name: parse_ref(value) for name, value in step.inputs.items()}
        for name, ref in refs.items():
            if isinstance(ref, InputRef):
                flow_input = spec.inputs[ref.name]
                if flow_input.kind == "file":
                    resolved[name] = f"inputs/{Path(flow_input.value).name}"
                else:
                    resolved[name] = flow_input.value
            else:
                outputs = produced.get(ref.step_id)
                if outputs is None or ref.output_name not in outputs:
                    msg = (
                        f"Step {step.id!r} references unavailable output "
                        f"{ref.step_id!r}.{ref.output_name!r}"
                    )
                    raise InvalidRequestError(msg)
                resolved[name] = outputs[ref.output_name]
        return resolved
