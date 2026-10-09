"""MCP tools: the four contract capabilities, and nothing else (M7.2).

Each tool is a thin, declarative mapping onto one Platform API endpoint -
the tool schema is the only thing the model sees, the Platform document is
the only thing it gets back::

    run_flow       -> POST /api/v1/flows/run
    get_run        -> GET  /api/v1/runs/{run_id}
    run_qa         -> POST /api/v1/runs/{run_id}/run_qa
    get_artifacts  -> GET  /api/v1/runs/{run_id}/artifacts

Deliberate non-capabilities (spec §15/§29): there is no tool for shell,
subprocess, Docker, SQL, arbitrary file reads or arbitrary HTTP. A tool
never executes an EDA flow itself - it asks the Platform to.

This service holds the *behaviour*; the argument schemas are the handler
signatures in :mod:`aemp.mcp.server` (the MCP SDK derives the JSON Schema
from them, so there is exactly one declaration - spec §22).

A failure is returned as structured data (``isError`` + the Platform error
contract), never as a protocol-level crash: the Platform's own code
(``RUN_NOT_FOUND``, ``TOOL_ENVIRONMENT_UNAVAILABLE``, ...) survives intact.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aemp.core.logging import get_logger
from aemp.mcp.client import PlatformHttpClient
from aemp.mcp.config import DEFAULT_SERVER_NAME
from aemp.mcp.errors import (
    McpAdapterError,
    PlatformApiError,
    PlatformResponseError,
    PlatformUnavailableError,
    UnknownToolError,
)

__all__ = [
    "ERROR_PLATFORM_RESPONSE",
    "ERROR_PLATFORM_UNAVAILABLE",
    "ERROR_UNKNOWN_TOOL",
    "TOOL_NAMES",
    "McpToolService",
    "ToolOutcome",
]

logger = get_logger(__name__)

#: Adapter-level codes. Platform-level failures keep the PLATFORM's own code.
ERROR_UNKNOWN_TOOL = "MCP_TOOL_NOT_FOUND"
ERROR_PLATFORM_UNAVAILABLE = "MCP_PLATFORM_UNAVAILABLE"
ERROR_PLATFORM_RESPONSE = "MCP_INVALID_PLATFORM_RESPONSE"

#: Exactly the capabilities this server exposes (contract: x-agent-tools).
TOOL_NAMES: tuple[str, ...] = ("run_flow", "get_run", "run_qa", "get_artifacts")


@dataclass(frozen=True)
class ToolOutcome:
    """The structured result of one tool call.

    ``ok`` is the only success signal; ``data`` carries either the Platform
    document (success) or an error contract (failure). There is no third
    "maybe" state - a tool that cannot prove a result says so.
    """

    name: str
    ok: bool
    data: dict[str, Any]

    def to_document(self) -> dict[str, Any]:
        """The structured content handed to the MCP client."""
        return dict(self.data)

    @property
    def error_code(self) -> str | None:
        """The error contract's code, when this outcome failed."""
        if self.ok:
            return None
        error = self.data.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            return code or None
        return None


class McpToolService:
    """Adapts Platform HTTP calls into outcomes; adds no judgment."""

    def __init__(
        self, client: PlatformHttpClient, *, server_name: str = DEFAULT_SERVER_NAME
    ) -> None:
        self._client = client
        self._server_name = server_name

    def run_flow(
        self, flow: str, inputs: Mapping[str, str] | None = None
    ) -> ToolOutcome:
        """``run_flow`` -> ``POST /api/v1/flows/run``."""
        return self._call(
            "run_flow",
            lambda: self._client.run_flow(
                flow, inputs=dict(inputs) if inputs else None
            ),
        )

    def get_run(self, run_id: str) -> ToolOutcome:
        """``get_run`` -> ``GET /api/v1/runs/{run_id}``."""
        return self._call("get_run", lambda: self._client.get_run(run_id))

    def run_qa(self, run_id: str, rule_pack: str) -> ToolOutcome:
        """``run_qa`` -> ``POST /api/v1/runs/{run_id}/qa``."""
        return self._call(
            "run_qa", lambda: self._client.run_qa(run_id, rule_pack)
        )

    def get_artifacts(self, run_id: str) -> ToolOutcome:
        """``get_artifacts`` -> ``GET /api/v1/runs/{run_id}/artifacts``."""
        return self._call(
            "get_artifacts", lambda: self._client.list_artifacts(run_id)
        )

    # -- internals ----------------------------------------------------------

    def _call(self, name: str, request: Callable[[], dict[str, Any]]) -> ToolOutcome:
        """One Platform call; every failure becomes a structured outcome.

        The trace line carries names/status/latency only (spec §43/§44):
        never the tool arguments, never the Platform document body.
        """
        if name not in TOOL_NAMES:  # pragma: no cover - defensive
            raise UnknownToolError(f"unknown tool: {name!r}")
        started = time.monotonic()
        outcome = self._invoke(name, request)
        logger.info(
            "mcp_tool: server=%s tool=%s status=%s error_code=%s latency_ms=%d",
            self._server_name,
            name,
            "success" if outcome.ok else "error",
            outcome.error_code or "-",
            int((time.monotonic() - started) * 1000),
        )
        return outcome

    @staticmethod
    def _invoke(name: str, request: Callable[[], dict[str, Any]]) -> ToolOutcome:
        try:
            return ToolOutcome(name=name, ok=True, data=request())
        except PlatformApiError as exc:
            # The Platform's own verdict vocabulary is preserved verbatim.
            return ToolOutcome(name=name, ok=False, data=exc.to_document())
        except PlatformUnavailableError as exc:
            return ToolOutcome(
                name=name,
                ok=False,
                data=_error_document(ERROR_PLATFORM_UNAVAILABLE, str(exc)),
            )
        except PlatformResponseError as exc:
            return ToolOutcome(
                name=name,
                ok=False,
                data=_error_document(ERROR_PLATFORM_RESPONSE, str(exc)),
            )
        except McpAdapterError as exc:  # pragma: no cover - defensive
            return ToolOutcome(
                name=name,
                ok=False,
                data=_error_document(ERROR_UNKNOWN_TOOL, str(exc)),
            )


def _error_document(code: str, message: str) -> dict[str, Any]:
    """The Platform error contract shape, filled with an adapter code."""
    return {"error": {"code": code, "message": message[:4000], "details": {}}}
