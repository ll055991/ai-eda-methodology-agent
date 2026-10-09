"""PlatformApi: the application service between the API contract and the
existing deterministic core.

Responsibilities (and nothing more):

    HTTP request -> contract validation -> EXISTING service -> response DTO

The API layer contains no Flow / Run / QA business logic: the FlowEngine
orchestrates, RunManager records, QAService judges. This module only maps
their outputs onto the contract DTOs and translates failures into
:class:`~aemp.api.errors.ApiError` codes.

Security posture (M6.0):
    - ``run_id`` is format-validated before touching the filesystem;
    - artifacts are addressable only if the run registry recorded them
      (no arbitrary file access);
    - callers name a flow spec or rule pack; they never pass tool
      commands.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aemp.api.errors import (
    ARTIFACT_NOT_FOUND,
    FLOW_NOT_FOUND,
    FLOW_VALIDATION_FAILED,
    INVALID_REQUEST,
    QA_EVALUATION_FAILED,
    RUN_NOT_FOUND,
    TOOL_ENVIRONMENT_UNAVAILABLE,
    ApiError,
)
from aemp.api.schemas import validate_contract
from aemp.core.config import Settings
from aemp.core.errors import (
    ConfigurationError,
    InvalidRequestError,
    SchemaValidationError,
)
from aemp.drivers.errors import ToolEnvironmentError
from aemp.flow.engine import FlowEngine, default_registry
from aemp.qa.service import QAService
from aemp.runs.manager import RunManager

__all__ = ["PlatformApi"]

_RUN_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")


class PlatformApi:
    """In-process implementation of the Platform API contract.

    The future HTTP server (M6.1) mounts this class; the Agent Backend
    never imports it - it talks to the HTTP contract instead.

    Args:
        settings: Platform project settings (runs directory, configs).
        manager: Optional RunManager (tests inject one; default: lazy).
        flow_engine: Optional FlowEngine (default: lazy, built from
            ``configs/tools`` on first use).
        qa_service: Optional QAService (default: lazy).
        contracts_dir: Directory holding the contract schemas; defaults to
            ``<project_root>/contracts``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        manager: RunManager | None = None,
        flow_engine: FlowEngine | None = None,
        qa_service: QAService | None = None,
        contracts_dir: Path | None = None,
    ) -> None:
        self._settings = settings
        self._manager = manager
        self._flow_engine = flow_engine
        self._qa_service = qa_service
        self._contracts_dir = (
            Path(contracts_dir)
            if contracts_dir is not None
            else settings.project_root / "contracts"
        )

    # -- lazy collaborators ------------------------------------------------

    @property
    def manager(self) -> RunManager:
        if self._manager is None:
            self._manager = RunManager(self._settings)
        return self._manager

    @property
    def flow_engine(self) -> FlowEngine:
        if self._flow_engine is None:
            self._flow_engine = FlowEngine(
                registry=default_registry(self._settings), manager=self.manager
            )
        return self._flow_engine

    @property
    def qa_service(self) -> QAService:
        if self._qa_service is None:
            self._qa_service = QAService(manager=self.manager)
        return self._qa_service

    # -- endpoints (contract: contracts/platform-api.yaml) -----------------

    def run_flow(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """POST /api/v1/flows/run: execute a flow spec as one tracked run.

        The flow path is resolved against the invocation working
        directory (M3 semantics). ``inputs`` overrides are reserved for
        M6.1 and rejected when non-empty in M6.0.
        """
        self._validate_request("flow", dict(request))
        flow_path = Path(str(request["flow"]))
        if not flow_path.is_file():
            raise ApiError(FLOW_NOT_FOUND, f"Flow spec not found: {request['flow']}")
        if request.get("inputs"):
            raise ApiError(
                INVALID_REQUEST,
                "input overrides are reserved for M6.1; M6.0 rejects non-empty 'inputs'",
            )
        try:
            run = self.flow_engine.run(flow_path)
        except ToolEnvironmentError as exc:
            raise ApiError(TOOL_ENVIRONMENT_UNAVAILABLE, str(exc)) from exc
        except (InvalidRequestError, ConfigurationError) as exc:
            raise ApiError(FLOW_VALIDATION_FAILED, str(exc)) from exc
        summary = self._run_summary(self._read_envelope(run.run_id))
        self._validate("run-summary", summary)
        return summary

    def get_run(self, run_id: str) -> dict[str, Any]:
        """GET /api/v1/runs/{run_id}: one run's statuses, facts, evidence."""
        document = self._run_detail(self._read_envelope(run_id))
        self._validate("run", document)
        return document

    def run_qa(self, run_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        """POST /api/v1/runs/{run_id}/qa: evaluate a rule pack (M5 engine)."""
        self._validate_request("qa-request", dict(request))
        self._read_envelope(run_id)  # RUN_NOT_FOUND guard before evaluating
        rule_pack = Path(str(request["rule_pack"]))
        if not rule_pack.is_file():
            raise ApiError(INVALID_REQUEST, f"Rule pack not found: {request['rule_pack']}")
        try:
            summary = self.qa_service.evaluate_run(run_id, rule_pack)
        except InvalidRequestError as exc:
            raise ApiError(QA_EVALUATION_FAILED, str(exc)) from exc
        document = self._qa_document(run_id, summary)
        self._validate("qa", document)
        return document

    def list_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/runs: history page from the SQLite index."""
        try:
            rows = self.manager.index.query_runs(limit=limit, offset=offset, status=status)
        except InvalidRequestError as exc:
            raise ApiError(INVALID_REQUEST, str(exc)) from exc
        document = {
            "runs": [
                {
                    "run_id": row["run_id"],
                    "lifecycle_status": row["status"],
                    "operation": row["operation"],
                    "created_at": row["created_at"],
                    "engineering_status": row["engineering_status"],
                }
                for row in rows
            ],
            "limit": limit,
            "offset": offset,
        }
        self._validate("run-list", document)
        return document

    def list_artifacts(self, run_id: str) -> dict[str, Any]:
        """GET /api/v1/runs/{run_id}/artifacts: registered artifact metadata."""
        envelope = self._read_envelope(run_id)
        document = {
            "run_id": run_id,
            "artifacts": [self._artifact_record(record) for record in envelope["artifacts"]],
        }
        self._validate("artifact-list", document)
        return document

    def get_artifact(self, run_id: str, artifact_id: str) -> dict[str, Any]:
        """GET /api/v1/runs/{run_id}/artifacts/{artifact_id}: allowlisted read.

        Only artifacts already recorded in the run's envelope are
        addressable; anything else is ARTIFACT_NOT_FOUND.
        """
        envelope = self._read_envelope(run_id)
        for record in envelope["artifacts"]:
            if record["path"] == artifact_id:
                document = self._artifact_record(record)
                self._validate("artifact", document)
                return document
        raise ApiError(
            ARTIFACT_NOT_FOUND,
            f"Artifact not registered for run {run_id}: {artifact_id!r}",
        )

    # -- internals ----------------------------------------------------------

    def _validate(self, name: str, document: Any) -> None:
        validate_contract(name, document, self._contracts_dir)

    def _validate_request(self, name: str, document: Any) -> None:
        """Request-contract validation failure is INVALID_REQUEST, not 500."""
        try:
            self._validate(name, document)
        except SchemaValidationError as exc:
            raise ApiError(
                INVALID_REQUEST,
                f"Request does not match the {name} contract",
                details={"path": exc.path, "detail": exc.detail},
            ) from exc

    def _read_envelope(self, run_id: str) -> dict[str, Any]:
        """Read ``runs/<run_id>/run.json`` (filesystem = source of truth).

        The id is format-validated first: the API is a trust boundary and
        must never join an unvalidated string onto the runs directory.
        """
        if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ApiError(RUN_NOT_FOUND, f"Run not found: {run_id!r}")
        path = self.manager.runs_dir / run_id / "run.json"
        if not path.is_file():
            raise ApiError(RUN_NOT_FOUND, f"Run not found: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _run_summary(envelope: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "run_id": envelope["run_id"],
            "status": envelope["provenance"]["run_status"],
            "execution_status": envelope["execution"]["status"],
            "engineering_status": envelope["engineering"]["status"],
        }

    @staticmethod
    def _file_record(record: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "role": record["role"],
            "path": record["path"],
            "exists": record["exists"],
        }
        if record["exists"]:
            out["bytes"] = record["bytes"]
            out["sha256"] = record["sha256"]
        return out

    @classmethod
    def _artifact_record(cls, record: Mapping[str, Any]) -> dict[str, Any]:
        return {"artifact_id": record["path"], **cls._file_record(record)}

    @classmethod
    def _run_detail(cls, envelope: Mapping[str, Any]) -> dict[str, Any]:
        provenance = envelope["provenance"]
        return {
            "run_id": envelope["run_id"],
            "operation": envelope["operation"],
            "step_id": envelope.get("step_id"),
            "lifecycle_status": provenance["run_status"],
            "execution_status": envelope["execution"]["status"],
            "engineering_status": envelope["engineering"]["status"],
            "tool": {
                "name": envelope["tool"]["name"],
                "version": envelope["tool"]["version"],
            },
            "metrics": dict(envelope["metrics"]),
            "inputs": [cls._file_record(record) for record in envelope["inputs"]],
            "artifacts": [cls._artifact_record(record) for record in envelope["artifacts"]],
            "diagnostics": [
                {
                    "severity": diagnostic["severity"],
                    "code": diagnostic["code"],
                    "message": diagnostic["message"],
                }
                for diagnostic in envelope["diagnostics"]
            ],
            "provenance": dict(provenance),
        }

    @staticmethod
    def _qa_document(run_id: str, summary: Any) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "status": summary.status.upper(),
            "rule_pack": {"name": summary.rule_pack, "version": summary.rule_pack_version},
            "summary": {
                "total_rules": summary.total_rules,
                "passed": summary.passed,
                "failed": summary.failed,
                "unknown": summary.unknown,
                "warnings": summary.warnings,
            },
            "findings": [
                {
                    "rule_id": finding.rule_id,
                    "metric": finding.metric,
                    "check": finding.check,
                    "threshold": (
                        list(finding.threshold)
                        if isinstance(finding.threshold, tuple)
                        else finding.threshold
                    ),
                    "severity": finding.severity,
                    "status": finding.status,
                    "expected": finding.expected,
                    "actual": finding.actual,
                    "message": finding.message,
                }
                for finding in summary.findings
            ],
        }
