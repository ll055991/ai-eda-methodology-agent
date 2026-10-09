"""MCPGateway: MCP tools behind the existing ToolGateway seam (M7.2).

The runtime already depends on one protocol (``ToolGateway``): this class
implements it over MCP, so the graph, the PolicyEngine, the ApprovalManager
and the checkpointer are untouched::

    runtime -> ToolGateway protocol -> { RegistryToolGateway  (local tools)
                                       MCPGateway            (MCP tools) }

Security properties this class owns:

* **allowlist** - only names in the code-owned allowlist are ever exposed
  or executed; a tool the server added is refused, not adopted;
* **no fallback** - an unreachable/erroring MCP server produces a
  structured failure, never a silent switch to the local tools;
* **bounded results** - MCP results become the same ``ToolResult`` the
  local tools produce, so the 32 KiB message bound still applies;
* **error normalization** - the model sees ``MCP_*``/Platform codes, never
  an SDK exception or an ``httpx`` traceback.
"""

from __future__ import annotations

import json
import time
from typing import Any

from jsonschema import Draft202012Validator

from agent_backend.config import MCPConfig
from agent_backend.logging import get_logger
from agent_backend.mcp.client import MCPClient
from agent_backend.mcp.errors import (
    MCP_INVALID_RESULT,
    MCP_TOOL_CALL_FAILED,
    MCP_TOOL_NOT_ALLOWED,
    MCP_TOOL_NOT_FOUND,
    MCPClientError,
)
from agent_backend.mcp.models import McpServerSpec, McpToolCallResult
from agent_backend.mcp.registry import DEFAULT_MCP_ALLOWLIST, MCPRegistry
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult

__all__ = ["MCPGateway", "build_mcp_gateway"]

logger = get_logger(__name__)

_MAX_DETAIL_CHARS = 500


class MCPGateway:
    """Allowlisted MCP tools, presented as the runtime's ``ToolGateway``."""

    def __init__(
        self,
        client: MCPClient,
        registry: MCPRegistry,
        *,
        server_name: str | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._server_name = server_name or client.server.name
        self._discovered = False
        self._last_error: MCPClientError | None = None

    # -- ToolGateway protocol -----------------------------------------------

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Tool schemas for the model (empty when discovery failed).

        Empty is the honest answer: without a successful ``tools/list`` the
        gateway has no schema to advertise, and inventing one would describe
        a capability it cannot prove.
        """
        error = self._ensure_discovery()
        if error is not None:
            return ()
        return tuple(
            spec.to_tool_definition() for spec in self._registry.allowed_tools()
        )

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Validate, then call exactly one MCP tool; never raise."""
        if not self._registry.is_allowed(tool_call.name):
            logger.warning(
                "mcp tool refused: server=%s tool=%s reason=not_allowlisted",
                self._server_name,
                tool_call.name,
            )
            return ToolResult.error(
                tool_call,
                MCP_TOOL_NOT_ALLOWED,
                f"tool {tool_call.name!r} is not in the agent MCP allowlist",
            )

        error = self._ensure_discovery()
        if error is not None:
            return ToolResult.error(
                tool_call,
                error.code,
                "MCP server is unavailable; no automatic fallback to local tools.",
            )

        spec = self._registry.tool(tool_call.name)
        if spec is None:
            return ToolResult.error(
                tool_call,
                MCP_TOOL_NOT_FOUND,
                f"the MCP server does not expose {tool_call.name!r}",
            )

        invalid = _first_schema_error(spec.input_schema, tool_call.arguments)
        if invalid is not None:
            return ToolResult.error(
                tool_call, "MCP_TOOL_ARGUMENTS_INVALID", invalid
            )

        started = time.monotonic()
        try:
            result = self._client.call_tool(tool_call.name, dict(tool_call.arguments))
        except MCPClientError as exc:
            _log_call(tool_call, self._server_name, "error", exc.code, started)
            return ToolResult.error(tool_call, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - never crash the agent loop
            logger.exception("mcp tool crashed: tool=%s", tool_call.name)
            _log_call(
                tool_call, self._server_name, "error", MCP_TOOL_CALL_FAILED, started
            )
            return ToolResult.error(tool_call, MCP_TOOL_CALL_FAILED, type(exc).__name__)

        if result.is_error:
            code, message = _error_from(result, tool_call.name)
            _log_call(tool_call, self._server_name, "error", code, started)
            return ToolResult.error(tool_call, code, message)

        data = _data_from(result)
        if data is None:
            _log_call(
                tool_call, self._server_name, "error", MCP_INVALID_RESULT, started
            )
            return ToolResult.error(
                tool_call,
                MCP_INVALID_RESULT,
                f"mcp tool {tool_call.name!r} returned no usable content",
            )
        _log_call(tool_call, self._server_name, "success", None, started)
        return ToolResult.success(tool_call, data)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def last_error(self) -> MCPClientError | None:
        """The most recent discovery failure (diagnostics/tests)."""
        return self._last_error

    # -- internals ----------------------------------------------------------

    def _ensure_discovery(self) -> MCPClientError | None:
        """Discover once; return the failure instead of raising."""
        if self._discovered:
            return None
        try:
            self._registry.discover(self._client)
        except MCPClientError as exc:
            self._last_error = exc
            logger.error(
                "mcp discovery failed: server=%s url=%s error_code=%s",
                self._server_name,
                self._client.server.url,
                exc.code,
            )
            return exc
        self._last_error = None
        self._discovered = True
        return None


def build_mcp_gateway(
    config: MCPConfig, *, allowlist: tuple[str, ...] = DEFAULT_MCP_ALLOWLIST
) -> MCPGateway:
    """Build the MCP gateway named by the configuration (factory helper)."""
    client = MCPClient(
        McpServerSpec(name=config.server_name, url=config.server_url),
        connect_timeout_s=config.connect_timeout_s,
        call_timeout_s=config.call_timeout_s,
    )
    return MCPGateway(client, MCPRegistry(allowlist=allowlist))


# -- result mapping -----------------------------------------------------------


def _error_from(result: McpToolCallResult, tool_name: str) -> tuple[str, str]:
    """Error code/message from one MCP error result.

    A Platform error contract inside ``structuredContent`` keeps its own
    code (``RUN_NOT_FOUND``, ``TOOL_ENVIRONMENT_UNAVAILABLE``, ...); the
    adapter codes are used only when the server sent no structured payload.
    """
    document = result.error_document()
    if document is not None:
        code = str(document.get("code") or "").strip()
        if code:
            message = str(document.get("message") or "").strip()
            return code, message or f"MCP tool {tool_name!r} failed"
    text = (result.text or "").strip()
    return MCP_TOOL_CALL_FAILED, text[:_MAX_DETAIL_CHARS] or f"MCP tool {tool_name!r} failed"


def _data_from(result: McpToolCallResult) -> Any | None:
    """Structured content first; JSON text as the fallback."""
    if result.structured is not None:
        return result.structured
    text = (result.text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"raw_text": text}
    return parsed


def _first_schema_error(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Return a bounded validation message, or ``None`` when arguments are valid."""
    errors = sorted(
        Draft202012Validator(schema).iter_errors(arguments),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    first = next(iter(errors), None)
    if first is None:
        return None
    path = ".".join(str(part) for part in first.absolute_path) or "<arguments>"
    return f"{path}: {first.message}"[:_MAX_DETAIL_CHARS]


def _log_call(
    tool_call: ToolCall,
    server_name: str,
    status: str,
    error_code: str | None,
    started: float,
) -> None:
    """Structured local trace (spec §43/§44): names/ids/status/latency only."""
    logger.info(
        "mcp_call: server=%s tool=%s call_id=%s status=%s error_code=%s latency_ms=%d",
        server_name,
        tool_call.name,
        tool_call.id,
        status,
        error_code,
        int((time.monotonic() - started) * 1000),
    )
