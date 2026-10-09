"""MCP client-side errors (M7.2).

Transport failures must never reach the model as raw SDK exceptions
(``ExceptionGroup``/``httpx.ConnectError``/``MCPError``); they are
normalized into these codes first (spec §34):

    MCP_SERVER_UNAVAILABLE   the endpoint refused / is not listening
    MCP_CONNECTION_FAILED    the handshake failed or timed out
    MCP_CALL_TIMEOUT         one call exceeded the configured deadline
    MCP_TOOL_NOT_FOUND       the server does not expose that tool
    MCP_TOOL_NOT_ALLOWED     the server exposes it, the agent allowlist does not
    MCP_TOOL_CALL_FAILED     the call itself failed (bad args, handler crash)
    MCP_RESOURCE_READ_FAILED a resource could not be read (unknown URI, too big)
    MCP_INVALID_RESULT       the result was neither structured nor text

There is deliberately no automatic fallback to the local tools (spec §35):
an unavailable MCP server is a failure, not a silent downgrade.
"""

from __future__ import annotations

__all__ = [
    "MCP_CALL_TIMEOUT",
    "MCP_CONNECTION_FAILED",
    "MCP_INVALID_RESULT",
    "MCP_RESOURCE_READ_FAILED",
    "MCP_SERVER_UNAVAILABLE",
    "MCP_TOOL_CALL_FAILED",
    "MCP_TOOL_NOT_ALLOWED",
    "MCP_TOOL_NOT_FOUND",
    "MCPClientError",
]

MCP_SERVER_UNAVAILABLE = "MCP_SERVER_UNAVAILABLE"
MCP_CONNECTION_FAILED = "MCP_CONNECTION_FAILED"
MCP_CALL_TIMEOUT = "MCP_CALL_TIMEOUT"
MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
MCP_TOOL_NOT_ALLOWED = "MCP_TOOL_NOT_ALLOWED"
MCP_TOOL_CALL_FAILED = "MCP_TOOL_CALL_FAILED"
MCP_RESOURCE_READ_FAILED = "MCP_RESOURCE_READ_FAILED"
MCP_INVALID_RESULT = "MCP_INVALID_RESULT"


class MCPClientError(Exception):
    """A normalized MCP client failure (``code`` is one of the constants).

    Not an :class:`~agent_backend.errors.AgentError`: MCP codes are internal
    to the agent loop and never part of the Agent API error enum. The
    gateway turns them into bounded tool results for the model.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message[:1000])
        self.code = code
        self.message = message[:1000]
