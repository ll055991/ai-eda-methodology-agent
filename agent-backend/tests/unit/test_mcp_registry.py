"""MCPRegistry tests (M7.2): discovery is not authorization.

The registry records what a server advertises; only the code-owned
allowlist decides what the model may ever call. A server that adds a
dangerous tool changes nothing for the agent.
"""

import pytest

from agent_backend.mcp.errors import MCPClientError
from agent_backend.mcp.models import McpResourceSpec, McpServerSpec, McpToolSpec
from agent_backend.mcp.registry import DEFAULT_MCP_ALLOWLIST, MCPRegistry


class FakeMCPClient:
    """Structural stand-in: the registry only needs these three members."""

    def __init__(
        self,
        *,
        tools: tuple[McpToolSpec, ...] = (),
        resources: tuple[McpResourceSpec, ...] = (),
        resource_error: MCPClientError | None = None,
        name: str = "aemp-methodology",
    ) -> None:
        self.server = McpServerSpec(name=name, url="http://127.0.0.1:8770/mcp")
        self._tools = tools
        self._resources = resources
        self._resource_error = resource_error
        self.tool_calls = 0
        self.resource_calls = 0

    def list_tools(self) -> tuple[McpToolSpec, ...]:
        self.tool_calls += 1
        return self._tools

    def list_resources(self) -> tuple[McpResourceSpec, ...]:
        self.resource_calls += 1
        if self._resource_error is not None:
            raise self._resource_error
        return self._resources


def _tool(name: str) -> McpToolSpec:
    return McpToolSpec(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
    )


def test_default_allowlist_is_the_four_contract_tools() -> None:
    assert DEFAULT_MCP_ALLOWLIST == ("run_flow", "get_run", "run_qa", "get_artifacts")


def test_discovery_records_everything_but_exposes_the_allowlist() -> None:
    client = FakeMCPClient(
        tools=(_tool("get_run"), _tool("run_flow"), _tool("shell"), _tool("read_file")),
        resources=(
            McpResourceSpec(uri="methodology://skills/catalog", name="catalog"),
        ),
    )
    registry = MCPRegistry()

    registry.discover(client)

    assert {tool.name for tool in registry.tools()} == {
        "get_run",
        "run_flow",
        "shell",
        "read_file",
    }
    assert {tool.name for tool in registry.allowed_tools()} == {"get_run", "run_flow"}
    assert registry.is_allowed("get_run") is True
    assert registry.is_allowed("shell") is False
    assert registry.resources()[0].uri == "methodology://skills/catalog"
    assert registry.servers()[0].name == "aemp-methodology"


def test_discovery_is_cached() -> None:
    client = FakeMCPClient(tools=(_tool("get_run"),))
    registry = MCPRegistry()

    registry.discover(client)
    registry.discover(client)

    assert client.tool_calls == 1


def test_forget_forces_rediscovery() -> None:
    client = FakeMCPClient(tools=(_tool("get_run"),))
    registry = MCPRegistry()
    registry.discover(client)

    registry.forget("aemp-methodology")
    registry.discover(client)

    assert client.tool_calls == 2


def test_missing_resources_do_not_fail_discovery() -> None:
    client = FakeMCPClient(
        tools=(_tool("get_run"),),
        resource_error=MCPClientError("MCP_TOOL_CALL_FAILED", "no resources"),
    )
    registry = MCPRegistry()

    registry.discover(client)

    assert registry.tools(), "tools survive a resource failure"
    assert registry.resources() == ()


def test_tool_lookup_and_empty_allowlist() -> None:
    registry = MCPRegistry()
    assert registry.tool("get_run") is None

    with pytest.raises(ValueError):
        MCPRegistry(allowlist=())


def test_registry_holds_multiple_servers() -> None:
    first = FakeMCPClient(tools=(_tool("get_run"),))
    second = FakeMCPClient(tools=(_tool("run_flow"),), name="aemp-qa")
    registry = MCPRegistry()

    registry.discover(first)
    registry.discover(second)

    assert {server.name for server in registry.servers()} == {
        "aemp-methodology",
        "aemp-qa",
    }
    assert {tool.server_name for tool in registry.allowed_tools()} == {
        "aemp-methodology",
        "aemp-qa",
    }


def test_discovery_error_propagates() -> None:
    class FailingClient(FakeMCPClient):
        def list_tools(self) -> tuple[McpToolSpec, ...]:
            raise MCPClientError("MCP_SERVER_UNAVAILABLE", "down")

    with pytest.raises(MCPClientError):
        MCPRegistry().discover(FailingClient())
