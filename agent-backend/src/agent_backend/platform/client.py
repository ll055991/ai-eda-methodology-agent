"""PlatformClient: the Agent Backend's only door to the Platform.

Transport only — it wraps the Platform HTTP API (contracts/platform-api.yaml)
into typed calls and validates every response against the shared JSON
Schemas. It performs NO business judgment: QA verdicts and run statuses
are Platform facts that pass through unchanged.

Errors are separated on purpose:

    PlatformApiError        business error document (404 RUN_NOT_FOUND, ...)
    PlatformConnectionError transport failure (refused / timeout / DNS)
    PlatformContractError   response violates the shared contract
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from agent_backend.config import DEFAULT_REQUEST_TIMEOUT_S, default_contracts_dir
from agent_backend.contracts import ContractViolation, validate_document
from agent_backend.logging import get_logger
from agent_backend.platform.errors import (
    PlatformApiError,
    PlatformConnectionError,
    PlatformContractError,
)

__all__ = ["PlatformClient"]

logger = get_logger(__name__)


class PlatformClient:
    """Synchronous HTTP client for the Platform API contract.

    Args:
        base_url: Platform API base URL (e.g. ``http://127.0.0.1:8000``).
        timeout_s: Per-request timeout (never infinite).
        contracts_dir: Shared contract directory; defaults to the
            repository ``contracts/`` (or ``AEMP_CONTRACTS_DIR``).
        http_client: Optional injected ``httpx.Client`` (tests use
            ``httpx.MockTransport``); the caller then owns its lifecycle.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        contracts_dir: Path | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._contracts_dir = (
            Path(contracts_dir) if contracts_dir is not None else default_contracts_dir()
        )
        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.Client(base_url=self._base_url, timeout=timeout_s)
            self._owns_http = True

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> PlatformClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- contract endpoints --------------------------------------------------

    def run_flow(
        self, flow: str, *, inputs: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """POST /api/v1/flows/run — execute a flow spec as one run."""
        body: dict[str, Any] = {"flow": flow}
        if inputs is not None:
            body["inputs"] = inputs
        return self._request("POST", "/api/v1/flows/run", schema="run-summary", json=body)

    def list_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/runs — run history page."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        return self._request("GET", "/api/v1/runs", schema="run-list", params=params)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """GET /api/v1/runs/{run_id} — one run's statuses, facts, evidence."""
        return self._request("GET", f"/api/v1/runs/{run_id}", schema="run")

    def run_qa(self, run_id: str, rule_pack: str) -> dict[str, Any]:
        """POST /api/v1/runs/{run_id}/qa — evaluate a rule pack (Platform verdict)."""
        return self._request(
            "POST",
            f"/api/v1/runs/{run_id}/qa",
            schema="qa",
            json={"rule_pack": rule_pack},
        )

    def list_artifacts(self, run_id: str) -> dict[str, Any]:
        """GET /api/v1/runs/{run_id}/artifacts — registered artifact metadata."""
        return self._request(
            "GET", f"/api/v1/runs/{run_id}/artifacts", schema="artifact-list"
        )

    def get_artifact(self, run_id: str, artifact_id: str) -> dict[str, Any]:
        """GET /api/v1/runs/{run_id}/artifacts/{artifact_id} — metadata only."""
        return self._request(
            "GET",
            f"/api/v1/runs/{run_id}/artifacts/{artifact_id}",
            schema="artifact",
        )

    # -- internals -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        schema: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = self._http.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            # transport-level: refused / timeout / DNS / protocol
            raise PlatformConnectionError(
                f"Platform request failed ({method} {path}): {exc.__class__.__name__}"
            ) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "platform %s %s -> %d (%d ms)",
            method,
            path,
            response.status_code,
            duration_ms,
        )

        if response.status_code >= 400:
            self._raise_api_error(response, method, path)

        try:
            document = response.json()
        except ValueError as exc:
            raise PlatformContractError(
                f"non-JSON response for {method} {path}"
            ) from exc
        self._validate(schema, document, method, path)
        return document

    def _raise_api_error(
        self, response: httpx.Response, method: str, path: str
    ) -> None:
        try:
            document = response.json()
            error = document["error"]
            code = str(error["code"])
            message = str(error["message"])
            details = error.get("details", {})
            if not isinstance(details, dict):
                raise TypeError("details must be an object")
        except (ValueError, KeyError, TypeError) as exc:
            raise PlatformContractError(
                f"invalid error document for {method} {path}"
            ) from exc
        self._validate("error", document, method, path)
        raise PlatformApiError(
            code, message, details=details, http_status=response.status_code
        )

    def _validate(self, schema: str, document: Any, method: str, path: str) -> None:
        try:
            validate_document(schema, document, self._contracts_dir)
        except ContractViolation as exc:
            raise PlatformContractError(
                f"response for {method} {path} violates {schema} contract: {exc.detail}"
            ) from exc
