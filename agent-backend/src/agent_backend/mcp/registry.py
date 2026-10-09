"""MCPRegistry: what the servers offer vs what the agent may call (M7.2).

Discovery is not authorization (spec §21/§33):

    MCP server -> tools/list -> MCPRegistry -> agent ALLOWLIST -> model tools

The registry holds the discovered metadata of every known MCP server
(name, endpoint, tools, resources - spec §28 multi-server ready), while
``allowed_tools()`` is the only view the model is ever shown. A dangerous
tool added to a server therefore changes nothing for the agent until the
code-owned allowlist changes too.

The default allowlist is exactly the four tools declared in
``contracts/agent-api.yaml`` (x-agent-tools); a test pins the two together.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from agent_backend.logging import get_logger
from agent_backend.mcp.client import MCPClient
from agent_backend.mcp.errors import MCPClientError
from agent_backend.mcp.models import McpResourceSpec, McpServerSpec, McpToolSpec

__all__ = ["DEFAULT_MCP_ALLOWLIST", "MCPRegistry"]

logger = get_logger(__name__)

#: Code-owned allowlist. Never derived from what a server advertises.
DEFAULT_MCP_ALLOWLIST: tuple[str, ...] = (
    "run_flow",
    "get_run",
    "run_qa",
    "get_artifacts",
)


@dataclass
class _ServerEntry:
    spec: McpServerSpec
    tools: tuple[McpToolSpec, ...] = ()
    resources: tuple[McpResourceSpec, ...] = ()


@dataclass
class MCPRegistry:
    """Discovered MCP servers + the agent-side allowlist."""

    allowlist: tuple[str, ...] = DEFAULT_MCP_ALLOWLIST
    _servers: dict[str, _ServerEntry] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.allowlist = tuple(self.allowlist)
        if not self.allowlist:
            raise ValueError("the MCP allowlist must not be empty")

    # -- discovery ----------------------------------------------------------

    def discover(self, client: MCPClient) -> None:
        """Connect once and record this server's tools and resources.

        Raises:
            MCPClientError: When the server cannot be reached or its
                handshake fails. Errors are never swallowed here.
        """
        spec = client.server
        if spec.name in self._servers:
            return
        # Provenance is owned here, not by the client: every discovered item
        # is stamped with the server it actually came from.
        tools = tuple(
            replace(tool, server_name=spec.name) for tool in client.list_tools()
        )
        try:
            resources = tuple(
                replace(resource, server_name=spec.name)
                for resource in client.list_resources()
            )
        except MCPClientError as exc:
            # Resources are optional knowledge; tools are the capability.
            logger.warning(
                "mcp resources unavailable: server=%s error_code=%s",
                spec.name,
                exc.code,
            )
            resources = ()
        self._servers[spec.name] = _ServerEntry(
            spec=spec, tools=tools, resources=resources
        )
        denied = [tool.name for tool in tools if tool.name not in self.allowlist]
        logger.info(
            "mcp discovery: server=%s tools=%d resources=%d allowlisted=%d denied=%d",
            spec.name,
            len(tools),
            len(resources),
            len(self.allowed_tools()),
            len(denied),
        )
        if denied:
            # A server advertising more than the contract allows is worth a
            # warning: it is exactly the case the allowlist exists for.
            logger.warning(
                "mcp tools not in the agent allowlist (not exposed): %s",
                ", ".join(sorted(denied)),
            )

    def forget(self, server_name: str) -> None:
        """Drop cached discovery for one server (reconnect path)."""
        self._servers.pop(server_name, None)

    # -- queries ------------------------------------------------------------

    def servers(self) -> tuple[McpServerSpec, ...]:
        return tuple(entry.spec for entry in self._servers.values())

    def tools(self) -> tuple[McpToolSpec, ...]:
        """Every discovered tool (never shown to the model as-is)."""
        return tuple(tool for entry in self._servers.values() for tool in entry.tools)

    def allowed_tools(self) -> tuple[McpToolSpec, ...]:
        """Discovered tools that the allowlist also permits."""
        return tuple(
            tool for tool in self.tools() if self.is_allowed(tool.name)
        )

    def tool(self, name: str) -> McpToolSpec | None:
        for tool in self.tools():
            if tool.name == name:
                return tool
        return None

    def resources(self) -> tuple[McpResourceSpec, ...]:
        return tuple(
            resource for entry in self._servers.values() for resource in entry.resources
        )

    def is_allowed(self, name: str) -> bool:
        return name in self.allowlist
