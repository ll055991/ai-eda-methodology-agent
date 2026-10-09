"""Run manager: create runs, freeze configs, register evidence, finalize.

Consistency discipline (filesystem first, index second)::

    1. create runs/<run_id>/ tree
    2. write run.json (aemp.result/v0, validated before every write)
    3. register inputs/artifacts (each write updates run.json)
    4. only then upsert the SQLite index row

so the index can never claim a run whose directory does not exist.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import socket
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

from aemp import __version__
from aemp.core.config import Settings
from aemp.core.errors import InvalidRequestError, StorageError
from aemp.runs.artifacts import record_file
from aemp.runs.index import INDEX_FILENAME, RunIndex
from aemp.runs.models import (
    ENGINEERING_STATUSES,
    EXECUTION_STATUSES,
    RUN_STATUS_CREATED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    FileRecord,
)
from aemp.schema.result import SCHEMA_VERSION, validate_result

_RUN_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
_RUN_ID_TRIES = 5
_SUBDIRS = ("logs", "artifacts", "results", "qa")
_DEFAULT_FROZEN_NAME = "flow.spec.frozen.yaml"


def _utc_now_iso() -> str:
    """Current UTC time as ISO 8601 (``2026-10-07T10:30:00Z``)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_run_id() -> str:
    """Generate ``YYYYMMDDTHHMMSSZ-<6 hex>`` in UTC (readable, unique, no DB)."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{token_hex(3)}"


def _build_envelope(
    *,
    run_id: str,
    operation: str,
    tool_name: str,
    created_at: str,
    inputs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the run-level ``aemp.result/v0`` envelope (nothing executed yet)."""
    return {
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "step_id": None,
        "operation": operation,
        "tool": {"name": tool_name, "version": "unknown", "path": None, "argv": []},
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
        "inputs": inputs,
        "artifacts": artifacts,
        "metrics": {},
        "diagnostics": [],
        "provenance": {
            "aemp_version": __version__,
            "created_at": created_at,
            "finished_at": None,
            "run_status": RUN_STATUS_CREATED,
            "host": socket.gethostname(),
            "python_version": (
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
            "platform": f"{platform.system()}-{platform.machine()}",
        },
    }


class Run:
    """Handle for one run: register evidence, finalize, persist.

    Every mutation rewrites ``run.json`` (validated against
    ``aemp.result/v0`` before writing) and mirrors the change into the
    SQLite index afterwards.
    """

    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        project_root: Path,
        envelope: dict[str, Any],
        index: RunIndex,
    ) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.project_root = project_root
        self._envelope = envelope
        self._index = index
        self._started_monotonic = time.monotonic()

    @property
    def status(self) -> str:
        """Run lifecycle state (``created`` until :meth:`finalize`)."""
        return self._envelope["provenance"]["run_status"]

    @property
    def metrics(self) -> dict[str, int | float]:
        """Parser-derived metrics recorded for this run (the fact area)."""
        return dict(self._envelope["metrics"])

    def record_engineering(self, *, status: str, summary: str | None = None) -> None:
        """Record the QA verdict into the engineering block (M5 closure).

        This is the ONLY sanctioned writer of engineering.status: the
        deterministic QA engine (not a tool, not a driver) decides it.
        execution.status is never modified here.

        Raises:
            InvalidRequestError: On an invalid status value.
        """
        if status not in ENGINEERING_STATUSES:
            msg = f"Invalid engineering status: {status!r}"
            raise InvalidRequestError(msg)
        self._envelope["engineering"]["status"] = status
        if summary is not None:
            self._envelope["engineering"]["summary"] = summary[:4000]
        self._write_envelope()

    def add_input(self, path: Path | str, role: str = "input") -> FileRecord:
        """Register an input file.

        Inputs live in the workspace; relative paths are resolved against
        the project root. A missing input is recorded with ``exists=False``
        and no hash - an evidence gap, never a fabricated digest.
        """
        record = record_file(Path(path), role=role, base=self.project_root)
        self._register_record("inputs", record)
        return record

    def add_artifact(self, path: Path | str, role: str) -> FileRecord:
        """Register an artifact produced by the run.

        Artifacts live inside the run directory; relative paths are
        resolved against it. A claimed output must exist.

        Raises:
            InvalidRequestError: If the file does not exist.
        """
        record = record_file(Path(path), role=role, base=self.run_dir)
        if not record.exists:
            msg = f"Artifact does not exist (claimed outputs must exist): {record.path}"
            raise InvalidRequestError(msg)
        self._register_record("artifacts", record)
        return record

    def freeze_file(
        self, source: Path | str, destination_name: str = _DEFAULT_FROZEN_NAME
    ) -> FileRecord:
        """Copy *source* verbatim into the run directory and record it.

        The run must stay reproducible independently of the current
        workspace, so configuration inputs are frozen (copied, hashed) at
        creation time, not referenced.

        Raises:
            InvalidRequestError: If *source* does not exist.
        """
        src = Path(source)
        if not src.is_file():
            msg = f"Cannot freeze missing file: {src}"
            raise InvalidRequestError(msg)
        destination = self.run_dir / destination_name
        shutil.copyfile(src, destination)
        record = record_file(destination, role="flow_spec", base=self.run_dir)
        self._register_record("inputs", record)
        return record

    def add_frozen_input(self, path: Path, role: str) -> FileRecord:
        """Register an input that already lives INSIDE the run directory.

        Frozen inputs (copied/written into the workspace before execution)
        are recorded run-relative - the same view the container gets when
        the run directory is mounted. Missing files follow the M1 input
        semantics: ``exists=False`` without a fabricated hash.
        """
        record = record_file(Path(path), role=role, base=self.run_dir)
        self._register_record("inputs", record)
        return record

    # -- execution-fact recording (M2.4) ----------------------------------
    # Primitive-typed on purpose: the Run layer never imports drivers; the
    # orchestration service translates ToolExecutionResult values into these
    # plain fields. Every method rewrites run.json (schema-validated).

    def mark_running(self) -> None:
        """Transition the lifecycle created -> running (forensic visibility).

        Raises:
            InvalidRequestError: If the run is not in the created state.
        """
        if self.status != RUN_STATUS_CREATED:
            msg = f"Cannot mark running from state {self.status!r}"
            raise InvalidRequestError(msg)
        self._envelope["provenance"]["run_status"] = RUN_STATUS_RUNNING
        self._write_envelope()
        provenance = self._envelope["provenance"]
        self._index.upsert_run(
            run_id=self.run_id,
            status=RUN_STATUS_RUNNING,
            created_at=provenance["created_at"],
            started_at=provenance["created_at"],
            finished_at=None,
            operation=self._envelope["operation"],
            engineering_status=self._envelope["engineering"]["status"],
            run_dir=self._run_dir_column(),
        )

    def record_step(self, step_id: str) -> None:
        """Record which flow step this run's envelope describes (e.g. synth)."""
        if not step_id or not step_id.strip():
            msg = "step_id must be a non-empty string"
            raise InvalidRequestError(msg)
        self._envelope["step_id"] = step_id
        self._write_envelope()

    def record_tool(
        self, *, name: str, version: str, path: str | None, argv: Sequence[str]
    ) -> None:
        """Fill the envelope ``tool`` block from probed tool identity.

        Args:
            name: Tool label (e.g. ``yosys``).
            version: Self-reported version line, or ``UNKNOWN`` - never guessed.
            path: Container-side tool path if known; ``None`` otherwise.
            argv: The tool's own argv (container-side, without the launcher).
        """
        if not name or not name.strip():
            msg = "tool name must be a non-empty string"
            raise InvalidRequestError(msg)
        if not version:
            msg = "tool version must be a non-empty string (use UNKNOWN when unprobed)"
            raise InvalidRequestError(msg)
        self._envelope["tool"] = {
            "name": name,
            "version": version,
            "path": path,
            "argv": [str(part) for part in argv],
        }
        self._write_envelope()

    def record_execution(
        self,
        *,
        status: str,
        exit_code: int | None,
        duration_ms: int,
        command: str,
        cwd: str,
        log_path: str | None,
        log_bytes: int,
        log_truncated: bool,
    ) -> None:
        """Fill the envelope ``execution`` block from recorded facts."""
        if status not in EXECUTION_STATUSES:
            msg = f"Invalid execution status: {status!r}"
            raise InvalidRequestError(msg)
        self._envelope["execution"] = {
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "command": command,
            "cwd": cwd,
            "log_path": log_path,
            "log_bytes": log_bytes,
            "log_truncated": log_truncated,
        }
        self._write_envelope()

    def add_diagnostic(self, *, severity: str, code: str, message: str) -> None:
        """Append one diagnostic (e.g. timeout / environment failures)."""
        if severity not in ("info", "warning", "error"):
            msg = f"Invalid diagnostic severity: {severity!r}"
            raise InvalidRequestError(msg)
        if not code or not code.strip():
            msg = "diagnostic code must be a non-empty string"
            raise InvalidRequestError(msg)
        self._envelope["diagnostics"].append(
            {"severity": severity, "code": code, "message": message[:4000]}
        )
        self._write_envelope()

    def record_steps(self, entries: list[dict[str, object]]) -> None:
        """Record a per-step execution summary (flow runs) into provenance.

        provenance is an open object in aemp.result/v0, so multi-step
        summaries fit without a schema change; the single ``execution``
        block keeps reflecting the most recently executed step.
        """
        self._envelope["provenance"]["steps"] = entries
        self._write_envelope()

    def record_metrics(self, metrics: dict[str, int | float]) -> None:
        """Merge parser-derived metrics into the envelope metrics area.

        Keys follow the platform convention ``<domain>.<metric>_<unit>``
        (e.g. ``sta.wns_ns``); values are plain numbers. Missing metrics
        are simply absent - never recorded as 0 (unknown != 0).

        Raises:
            InvalidRequestError: On a non-numeric value or malformed key.
        """
        for name, value in metrics.items():
            if not isinstance(name, str) or not name.strip():
                msg = "metric names must be non-empty strings"
                raise InvalidRequestError(msg)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                msg = f"metric {name!r} must be a number, got {type(value).__name__}"
                raise InvalidRequestError(msg)
        self._envelope["metrics"].update(metrics)
        self._write_envelope()

    def finalize(
        self,
        status: str,
        *,
        engineering_status: str = "unknown",
        engineering_summary: str | None = None,
        execution_status: str | None = None,
    ) -> None:
        """Complete the run.

        Args:
            status: ``success`` or ``failed`` - the run lifecycle outcome,
                mirrored into the index and (by default) into
                ``execution.status``.
            engineering_status: Independent engineering verdict; defaults
                to ``unknown`` because there is no engineering judgment
                before the QA engine (M5).
            engineering_summary: Optional human-readable summary.
            execution_status: Optional override for the ``execution``
                block. Default (``None``) mirrors *status* - the M1
                behavior. Pass it when the lifecycle outcome differs from
                what actually executed: e.g. an environment failure fails
                the RUN while the tool never ran, so execution stays
                ``unknown`` (unknown != failed).

        Raises:
            InvalidRequestError: On an invalid status value.
        """
        if status not in (RUN_STATUS_SUCCESS, RUN_STATUS_FAILED):
            msg = f"Finalize status must be 'success' or 'failed', got {status!r}"
            raise InvalidRequestError(msg)
        if engineering_status not in ENGINEERING_STATUSES:
            msg = f"Invalid engineering status: {engineering_status!r}"
            raise InvalidRequestError(msg)
        if execution_status is not None and execution_status not in EXECUTION_STATUSES:
            msg = f"Invalid execution status: {execution_status!r}"
            raise InvalidRequestError(msg)

        self._envelope["execution"]["status"] = (
            status if execution_status is None else execution_status
        )
        execution = self._envelope["execution"]
        if execution["duration_ms"] is None:
            elapsed_ms = int((time.monotonic() - self._started_monotonic) * 1000)
            execution["duration_ms"] = elapsed_ms
        self._envelope["engineering"]["status"] = engineering_status
        self._envelope["engineering"]["summary"] = engineering_summary

        provenance = self._envelope["provenance"]
        provenance["finished_at"] = _utc_now_iso()
        provenance["run_status"] = status

        self._write_envelope()
        self._index.upsert_run(
            run_id=self.run_id,
            status=status,
            created_at=provenance["created_at"],
            started_at=provenance["created_at"],
            finished_at=provenance["finished_at"],
            operation=self._envelope["operation"],
            engineering_status=engineering_status,
            run_dir=self._run_dir_column(),
        )

    # -- internals --------------------------------------------------------

    def _register_record(self, kind: str, record: FileRecord) -> None:
        """Append (or replace) a record, persist run.json, then update index."""
        entries = [
            entry
            for entry in self._envelope[kind]
            if not (entry["role"] == record.role and entry["path"] == record.path)
        ]
        entries.append(record.to_dict())
        self._envelope[kind] = entries
        self._write_envelope()
        self._index.record_artifact(self.run_id, record)

    def _write_envelope(self) -> None:
        validate_result(self._envelope)
        path = self.run_dir / "run.json"
        text = json.dumps(self._envelope, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")

    def _run_dir_column(self) -> str:
        return self.run_dir.relative_to(self.project_root).as_posix()


class RunManager:
    """Create and manage runs under ``<project_root>/runs``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._index: RunIndex | None = None

    @property
    def runs_dir(self) -> Path:
        """Directory holding all run data (source of truth)."""
        return self._settings.runs_dir

    def load_run(self, run_id: str) -> Run:
        """Reconstruct a Run handle from its archived run.json.

        The filesystem is the source of truth: the envelope is read back,
        re-validated, and the handle can register further evidence (e.g.
        M5 QA artifacts) into the existing archive.

        Raises:
            InvalidRequestError: If the run directory/run.json is missing
                or the archived envelope fails schema validation.
        """
        run_dir = self.runs_dir / run_id
        if not (run_dir / "run.json").is_file():
            msg = f"Run does not exist: {run_id}"
            raise InvalidRequestError(msg)
        envelope = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        validate_result(envelope)
        return Run(
            run_id=run_id,
            run_dir=run_dir,
            project_root=self._settings.project_root,
            envelope=envelope,
            index=self.index,
        )

    @property
    def index(self) -> RunIndex:
        """The SQLite index for this workspace (query cache only)."""
        if self._index is None:
            self._index = RunIndex(self.runs_dir / INDEX_FILENAME)
        return self._index

    def create_run(
        self,
        operation: str,
        *,
        inputs: Sequence[Path | str] = (),
        flow_spec: Path | str | None = None,
        tool_name: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        """Create a run: directory tree, frozen config, envelope, index row.

        Args:
            operation: What this run does (free-form label until M3).
            inputs: Input files to register (workspace-relative or absolute).
            flow_spec: Optional configuration file to freeze into the run
                directory (``flow.spec.frozen.yaml``).
            tool_name: Tool label for the envelope; defaults to *operation*
                (M1 runs no real tool - ``version`` stays ``unknown``).
            run_id: Explicit id; defaults to a generated UTC-stamped id.

        Returns:
            A live :class:`Run` handle.
        """
        if not operation or not operation.strip():
            msg = "operation must be a non-empty string"
            raise InvalidRequestError(msg)
        resolved_id = self._resolve_run_id(run_id)
        run_dir = self.runs_dir / resolved_id
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            for sub in _SUBDIRS:
                (run_dir / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            msg = f"Cannot create run directory {run_dir}: {exc}"
            raise StorageError(msg) from exc

        created_at = _utc_now_iso()
        envelope = _build_envelope(
            run_id=resolved_id,
            operation=operation,
            tool_name=tool_name if tool_name is not None else operation,
            created_at=created_at,
            inputs=[],
            artifacts=[],
        )
        run = Run(
            run_id=resolved_id,
            run_dir=run_dir,
            project_root=self._settings.project_root,
            envelope=envelope,
            index=self.index,
        )
        # Filesystem first: freeze config, register inputs, write run.json.
        if flow_spec is not None:
            run.freeze_file(flow_spec)
        for input_path in inputs:
            run.add_input(input_path)
        run._write_envelope()
        # Index second.
        self.index.upsert_run(
            run_id=resolved_id,
            status=RUN_STATUS_CREATED,
            created_at=created_at,
            started_at=created_at,
            finished_at=None,
            operation=operation,
            engineering_status="unknown",
            run_dir=run._run_dir_column(),
        )
        return run

    def _resolve_run_id(self, run_id: str | None) -> str:
        if run_id is not None:
            if not _RUN_ID_PATTERN.fullmatch(run_id):
                msg = f"Malformed run id (expected YYYYMMDDTHHMMSSZ-<6 hex>): {run_id!r}"
                raise InvalidRequestError(msg)
            if (self.runs_dir / run_id).exists():
                msg = f"Run id already exists: {run_id}"
                raise InvalidRequestError(msg)
            return run_id
        for _ in range(_RUN_ID_TRIES):
            candidate = _generate_run_id()
            if not (self.runs_dir / candidate).exists():
                return candidate
        msg = "Could not generate a unique run id"
        raise StorageError(msg)
