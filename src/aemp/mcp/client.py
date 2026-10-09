"""PlatformHttpClient: the MCP server's ONLY capability source (M7.2).

Architecture rule (spec §2): the MCP server never reaches EDA, Docker,
SQLite or ``runs/``. It speaks the *existing* Platform API contract over
HTTP and nothing else::

    MCP tool -> PlatformHttpClient -> POST /api/v1/flows/run -> PlatformApi

This client is transport + error mapping only:

* it does NOT judge results (a failed run is a valid 200 document);
* it does NOT re-implement the contract (the Platform validates its own
  responses against ``contracts/`` before writing them);
* it does NOT swallow errors - a Platform error document is raised as
  :class:`~aemp.mcp.errors.PlatformApiError` with the code intact, so the
  tool layer can hand the model the Platform's own verdict vocabulary
  (``RUN_NOT_FOUND``, ``TOOL_ENVIRONMENT_UNAVAILABLE``, ...).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from aemp.core.logging import get_logger
from aemp.mcp.config import DEFAULT_MCP_REQUEST_TIMEOUT_S
from aemp.mcp.errors import (
    PlatformApiError,
    PlatformResponseError,
    PlatformUnavailableError,
)

__all__ = ["PlatformHttpClient"]

logger = get_logger(__name__)


class PlatformHttpClient:
    """Small synchronous client over the Platform HTTP API contract.

    Args:
        base_url: Platform API base URL (e.g. ``http://127.0.0.1:8765``).
        timeout_s: Per-request timeout (never infinite).
        http_client: Optional injected ``httpx.Client`` (tests inject
            ``httpx.MockTransport``); the caller then owns its lifecycle.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_MCP_REQUEST_TIMEOUT_S,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
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

    def __enter__(self) -> PlatformHttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- contract endpoints (1:1 with contracts/platform-api.yaml) ----------

    def run_flow(
        self, flow: str, *, inputs: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """``POST /api/v1/flows/run``."""
        body: dict[str, Any] = {"flow": flow}
        if inputs:
            body["inputs"] = inputs
        return self._request("POST", "/api/v1/flows/run", json=body)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """``GET /api/v1/runs/{run_id}``."""
        return self._request("GET", f"/api/v1/runs/{run_id}")

    def run_qa(self, run_id: str, rule_pack: str) -> dict[str, Any]:
        """``POST /api/v1/runs/{run_id}/qa``."""
        return self._request(
            "POST", f"/api/v1/runs/{run_id}/qa", json={"rule_pack": rule_pack}
        )

    def list_artifacts(self, run_id: str) -> dict[str, Any]:
        """``GET /api/v1/runs/{run_id}/artifacts``."""
        return self._request("GET", f"/api/v1/runs/{run_id}/artifacts")

    # -- internals ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = self._http.request(method, path, json=json)
        except httpx.HTTPError as exc:
            logger.error(
                "platform request failed: %s %s transport=%s",
                method,
                path,
                type(exc).__name__,
            )
            raise PlatformUnavailableError(
                f"Platform request failed ({method} {path}): {type(exc).__name__}"
            ) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        # Trace only: method/path/status/duration/platform request id.
        # Never bodies, never headers, never query values.
        logger.info(
            "platform %s %s -> %d (%d ms) request_id=%s",
            method,
            path,
            response.status_code,
            duration_ms,
            response.headers.get("X-Request-ID") or "-",
        )

        if response.status_code >= 400:
            raise self._api_error(response, method, path)
        return self._document(response, method, path)

    @staticmethod
    def _api_error(
        response: httpx.Response, method: str, path: str
    ) -> PlatformApiError | PlatformResponseError:
        """Translate an error response into the Platform's own error contract."""
        try:
            document = response.json()
            error = document["error"]
            code = str(error["code"])
            message = str(error["message"])
            details = error.get("details") or {}
            if not isinstance(details, dict):
                raise TypeError("details must be an object")
        except (ValueError, KeyError, TypeError):
            return PlatformResponseError(
                f"Platform returned {response.status_code} for {method} {path} "
                "without a valid error document"
            )
        return PlatformApiError(
            code, message, details=details, http_status=response.status_code
        )

    @staticmethod
    def _document(
        response: httpx.Response, method: str, path: str
    ) -> dict[str, Any]:
        try:
            document = response.json()
        except ValueError as exc:
            raise PlatformResponseError(
                f"Platform returned a non-JSON response for {method} {path}"
            ) from exc
        if not isinstance(document, dict):
            raise PlatformResponseError(
                f"Platform returned {type(document).__name__} for {method} {path}; "
                "a contract document is a JSON object"
            )
        return document
