"""MCPClient unit tests (M7.2): error normalization and lifecycle.

The SDK reports failures in its own vocabulary (``ExceptionGroup`` wrapping
``MCPError``, timeouts as protocol errors). None of that may reach the
model: every failure must arrive as one of the ``MCP_*`` codes. These tests
drive the normalizers directly, plus the not-connected guard.
"""

import pytest

from agent_backend.mcp.client import (
    MCPClient,
    _normalize_call_error,
    _normalize_connect_error,
    _root_cause,
)
from agent_backend.mcp.errors import MCPClientError
from agent_backend.mcp.models import McpServerSpec


class FakeMCPError(Exception):
    """Stands in for mcp.shared.exceptions.MCPError (same shape)."""


def test_root_cause_unwraps_nested_groups() -> None:
    leaf = FakeMCPError("Request 'initialize' timed out")
    nested: BaseException = leaf
    for _ in range(3):
        nested = BaseExceptionGroup("boom", [nested])

    assert _root_cause(nested) is leaf


def test_connect_timeout_is_server_unavailable() -> None:
    error = _normalize_connect_error(
        ExceptionGroup("x", [FakeMCPError("Request 'initialize' timed out")])
    )

    assert error.code == "MCP_SERVER_UNAVAILABLE"


def test_connect_failure_is_connection_failed() -> None:
    error = _normalize_connect_error(FakeMCPError("protocol handshake rejected"))

    assert error.code == "MCP_CONNECTION_FAILED"


def test_call_timeout_is_reported_as_a_timeout() -> None:
    error = _normalize_call_error(
        ExceptionGroup("x", [FakeMCPError("Request 'tools/call' timed out")]),
        "tools/call get_run",
    )

    assert error.code == "MCP_CALL_TIMEOUT"


def test_resource_failure_has_its_own_code() -> None:
    error = _normalize_call_error(
        FakeMCPError("Unknown resource: methodology://skills/../../secret"),
        "resources/read",
    )

    assert error.code == "MCP_RESOURCE_READ_FAILED"


def test_tool_call_failure_is_reported_as_such() -> None:
    error = _normalize_call_error(FakeMCPError("Unknown tool: nope"), "tools/call nope")

    assert error.code == "MCP_TOOL_CALL_FAILED"


def test_refused_connection_is_server_unavailable() -> None:
    class ConnectError(Exception):
        pass

    error = _normalize_call_error(ConnectError("connection refused"), "tools/list")

    assert error.code == "MCP_SERVER_UNAVAILABLE"


def test_calls_require_a_connection() -> None:
    client = MCPClient(McpServerSpec(name="aemp-methodology", url="http://127.0.0.1:1/mcp"))

    with pytest.raises(MCPClientError) as excinfo:
        client.list_tools()

    assert excinfo.value.code in ("MCP_CONNECTION_FAILED", "MCP_SERVER_UNAVAILABLE")


def test_close_without_connect_is_safe() -> None:
    client = MCPClient(McpServerSpec(name="aemp-methodology", url="http://127.0.0.1:1/mcp"))

    client.close()
    client.close()

    assert client.server_info is None


def test_timeouts_must_be_positive() -> None:
    with pytest.raises(ValueError):
        MCPClient(
            McpServerSpec(name="aemp-methodology", url="http://127.0.0.1:1/mcp"),
            connect_timeout_s=0,
        )


def test_server_spec_is_validated() -> None:
    with pytest.raises(ValueError):
        McpServerSpec(name="", url="http://127.0.0.1:1/mcp")
    with pytest.raises(ValueError):
        McpServerSpec(name="aemp-methodology", url="")
