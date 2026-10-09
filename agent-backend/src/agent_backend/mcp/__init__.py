"""MCP integration (M7.2): MCP is a Tool Transport, never the agent itself.

    ChatService -> LangGraph runtime -> MCPGateway -> MCPClient -> MCP server
                                                            -> Platform HTTP

Layering rules this package keeps:

* the runtime depends on the ``ToolGateway`` protocol only - it never sees
  the MCP SDK, a URL or a session (``MCPGateway`` is the seam);
* MCP is optional: with ``AEMP_MCP_ENABLED=false`` nothing here is
  instantiated and the M6.4 local tools are used unchanged;
* no automatic fallback: an unavailable MCP server is a structured
  failure, never a silent downgrade to the local tools (spec §35);
* PolicyEngine/HITL stay in front of execution: the gateway is only
  reachable through the graph's policy-checked tool node.
"""

from agent_backend.mcp.client import MCPClient
from agent_backend.mcp.errors import (
    MCP_CALL_TIMEOUT,
    MCP_CONNECTION_FAILED,
    MCP_INVALID_RESULT,
    MCP_RESOURCE_READ_FAILED,
    MCP_SERVER_UNAVAILABLE,
    MCP_TOOL_CALL_FAILED,
    MCP_TOOL_NOT_ALLOWED,
    MCP_TOOL_NOT_FOUND,
    MCPClientError,
)
from agent_backend.mcp.gateway import MCPGateway, build_mcp_gateway
from agent_backend.mcp.models import (
    McpResourceContent,
    McpResourceSpec,
    McpServerSpec,
    McpToolCallResult,
    McpToolSpec,
)
from agent_backend.mcp.registry import DEFAULT_MCP_ALLOWLIST, MCPRegistry

__all__ = [
    "DEFAULT_MCP_ALLOWLIST",
    "MCP_CALL_TIMEOUT",
    "MCP_CONNECTION_FAILED",
    "MCP_INVALID_RESULT",
    "MCP_RESOURCE_READ_FAILED",
    "MCP_SERVER_UNAVAILABLE",
    "MCP_TOOL_CALL_FAILED",
    "MCP_TOOL_NOT_ALLOWED",
    "MCP_TOOL_NOT_FOUND",
    "MCPClient",
    "MCPClientError",
    "MCPGateway",
    "MCPRegistry",
    "McpResourceContent",
    "McpResourceSpec",
    "McpServerSpec",
    "McpToolCallResult",
    "McpToolSpec",
    "build_mcp_gateway",
]
