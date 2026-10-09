"""MCP adapter package (M7.2): the Methodology MCP Server.

Layering (root AGENTS.md §4): ``mcp`` is an outermost transport adapter,
like ``api``/``cli``. It depends on ``core`` only, talks to the Platform
over its HTTP contract, and is imported by nothing in the deterministic
core. The core pipeline runs end-to-end with this package absent.
"""

from aemp.mcp.client import PlatformHttpClient
from aemp.mcp.config import McpServerConfig
from aemp.mcp.errors import (
    McpAdapterError,
    PlatformApiError,
    PlatformResponseError,
    PlatformUnavailableError,
    ResourceNotFoundError,
    ResourcePathError,
    ResourceTooLargeError,
)
from aemp.mcp.resources import ResourceContent, ResourceService, ResourceSpec
from aemp.mcp.tools import TOOL_NAMES, McpToolService, ToolOutcome

__all__ = [
    "TOOL_NAMES",
    "McpAdapterError",
    "McpServerConfig",
    "McpToolService",
    "PlatformApiError",
    "PlatformHttpClient",
    "PlatformResponseError",
    "PlatformUnavailableError",
    "ResourceContent",
    "ResourceNotFoundError",
    "ResourcePathError",
    "ResourceService",
    "ResourceSpec",
    "ResourceTooLargeError",
    "ToolOutcome",
]
