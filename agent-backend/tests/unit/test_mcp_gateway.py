"""MCPGateway tests (M7.2): the security seam between MCP and the agent.

The gateway is a ``ToolGateway``: same protocol, same ``ToolResult`` shape,
same 32 KiB message bound. What it adds is MCP-specific discipline - the
allowlist, no automatic fallback, and error normalization.
"""

import json
from pathlib import Path

import pytest
import yaml

from agent_backend.config import MCPConfig
from agent_backend.mcp.errors import MCPClientError
from agent_backend.mcp.gateway import MCPGateway, build_mcp_gateway
from agent_backend.mcp.models import (
    McpServerSpec,
    McpToolCallResult,
    McpToolSpec,
)
from agent_backend.mcp.registry import DEFAULT_MCP_ALLOWLIST, MCPRegistry
from agent_backend.tools.models import MAX_TOOL_RESULT_BYTES, ToolCall

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_CONTRACT = REPO_ROOT / "contracts" / "agent-api.yaml"

_RUN_ID_SCHEMA = {
    "type": "object",
    "required": ["run_id"],
    "additionalProperties": False,
    "properties": {"run_id": {"type": "string"}},
}


def _tool(name: str, schema: dict | None = None) -> McpToolSpec:
    return McpToolSpec(
        name=name,
        description=f"{name} tool",
        input_schema=schema or _RUN_ID_SCHEMA,
        server_name="aemp-methodology",
    )


class FakeMCPClient:
    """A scripted MCP client: no sockets, fully deterministic."""

    def __init__(
        self,
        *,
        tools: tuple[McpToolSpec, ...] = (),
        results: dict[str, McpToolCallResult] | None = None,
        discovery_error: MCPClientError | None = None,
        call_error: MCPClientError | None = None,
    ) -> None:
        self.server = McpServerSpec(name="aemp-methodology", url="http://x/mcp")
        self._tools = tools
        self._results = results or {}
        self._discovery_error = discovery_error
        self._call_error = call_error
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self) -> tuple[McpToolSpec, ...]:
        if self._discovery_error is not None:
            raise self._discovery_error
        return self._tools

    def list_resources(self):
        return ()

    def call_tool(self, name: str, arguments: dict | None = None) -> McpToolCallResult:
        self.calls.append((name, dict(arguments or {})))
        if self._call_error is not None:
            raise self._call_error
        return self._results.get(name, McpToolCallResult(is_error=False, structured={}))


def _gateway(client: FakeMCPClient, *, allowlist=DEFAULT_MCP_ALLOWLIST) -> MCPGateway:
    return MCPGateway(client, MCPRegistry(allowlist=allowlist))


def _call(name: str, **arguments) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


# -- discovery / definitions ---------------------------------------------------


def test_definitions_expose_only_allowlisted_tools() -> None:
    client = FakeMCPClient(tools=(_tool("get_run"), _tool("shell")))
    gateway = _gateway(client)

    names = [definition.name for definition in gateway.definitions()]

    assert names == ["get_run"]


def test_definitions_carry_the_mcp_schema_and_description() -> None:
    client = FakeMCPClient(tools=(_tool("get_run"),))
    gateway = _gateway(client)

    definition = gateway.definitions()[0]

    assert definition.description == "get_run tool"
    assert definition.input_schema["required"] == ["run_id"]
    assert definition.to_openai_schema()["function"]["name"] == "get_run"


def test_discovery_failure_exposes_no_tools_and_is_remembered() -> None:
    client = FakeMCPClient(
        discovery_error=MCPClientError("MCP_SERVER_UNAVAILABLE", "down")
    )
    gateway = _gateway(client)

    assert gateway.definitions() == ()
    assert gateway.last_error is not None
    assert gateway.last_error.code == "MCP_SERVER_UNAVAILABLE"


# -- the allowlist is the security boundary ------------------------------------


def test_tool_outside_the_allowlist_is_refused_without_any_call() -> None:
    client = FakeMCPClient(tools=(_tool("shell")))
    gateway = _gateway(client)

    result = gateway.execute(_call("shell", cmd="whoami"))

    assert result.status == "error"
    assert result.error_code == "MCP_TOOL_NOT_ALLOWED"
    assert client.calls == []  # never even asked the server


def test_allowlisted_tool_the_server_does_not_expose() -> None:
    client = FakeMCPClient(tools=(_tool("get_run"),))
    gateway = _gateway(client)

    result = gateway.execute(_call("run_qa", run_id="x", rule_pack="y"))

    assert result.error_code == "MCP_TOOL_NOT_FOUND"
    assert client.calls == []


def test_arguments_are_validated_against_the_mcp_schema() -> None:
    client = FakeMCPClient(tools=(_tool("get_run"),))
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run"))  # run_id missing

    assert result.error_code == "MCP_TOOL_ARGUMENTS_INVALID"
    assert client.calls == []


# -- results -------------------------------------------------------------------


def test_success_keeps_the_structured_content() -> None:
    document = {"run_id": "r1", "engineering_status": "unknown"}
    client = FakeMCPClient(
        tools=(_tool("get_run"),),
        results={"get_run": McpToolCallResult(is_error=False, structured=document)},
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run", run_id="r1"))

    assert result.status == "success"
    assert result.data == document
    assert client.calls == [("get_run", {"run_id": "r1"})]


def test_platform_error_code_survives_as_structured_data() -> None:
    client = FakeMCPClient(
        tools=(_tool("run_flow", {"type": "object", "required": ["flow"]}),),
        results={
            "run_flow": McpToolCallResult(
                is_error=True,
                structured={
                    "error": {
                        "code": "TOOL_ENVIRONMENT_UNAVAILABLE",
                        "message": "no docker",
                        "details": {},
                    }
                },
            )
        },
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("run_flow", flow="f.yaml"))

    assert result.status == "error"
    assert result.error_code == "TOOL_ENVIRONMENT_UNAVAILABLE"
    assert result.error_message == "no docker"


def test_unstructured_error_result_becomes_a_bounded_message() -> None:
    client = FakeMCPClient(
        tools=(_tool("get_run"),),
        results={
            "get_run": McpToolCallResult(
                is_error=True, text="Error executing tool get_run: bad args"
            )
        },
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run", run_id="r1"))

    assert result.error_code == "MCP_TOOL_CALL_FAILED"
    assert "bad args" in (result.error_message or "")


def test_json_text_success_is_parsed_not_wrapped() -> None:
    client = FakeMCPClient(
        tools=(_tool("get_run"),),
        results={
            "get_run": McpToolCallResult(
                is_error=False, text=json.dumps({"run_id": "r1"})
            )
        },
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run", run_id="r1"))

    assert result.data == {"run_id": "r1"}


def test_empty_result_is_invalid_not_success() -> None:
    client = FakeMCPClient(
        tools=(_tool("get_run"),),
        results={"get_run": McpToolCallResult(is_error=False)},
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run", run_id="r1"))

    assert result.error_code == "MCP_INVALID_RESULT"


def test_client_failure_is_normalized() -> None:
    client = FakeMCPClient(
        tools=(_tool("get_run"),),
        call_error=MCPClientError("MCP_CALL_TIMEOUT", "timed out"),
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run", run_id="r1"))

    assert result.error_code == "MCP_CALL_TIMEOUT"


def test_unavailable_server_never_falls_back_to_local_tools() -> None:
    client = FakeMCPClient(
        discovery_error=MCPClientError("MCP_SERVER_UNAVAILABLE", "down")
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run", run_id="r1"))

    assert result.error_code == "MCP_SERVER_UNAVAILABLE"
    assert "no automatic fallback" in (result.error_message or "")
    assert client.calls == []


def test_oversized_result_stays_bounded_for_the_model() -> None:
    huge = {"run_id": "r1", "blob": "x" * (MAX_TOOL_RESULT_BYTES * 2)}
    client = FakeMCPClient(
        tools=(_tool("get_run"),),
        results={"get_run": McpToolCallResult(is_error=False, structured=huge)},
    )
    gateway = _gateway(client)

    result = gateway.execute(_call("get_run", run_id="r1"))
    message = result.to_message_content()

    assert len(message.encode("utf-8")) <= MAX_TOOL_RESULT_BYTES
    assert json.loads(message)["truncated"] is True


# -- contract alignment --------------------------------------------------------


def test_allowlist_matches_the_agent_contract() -> None:
    contract = yaml.safe_load(AGENT_CONTRACT.read_text(encoding="utf-8"))
    declared = tuple(tool["name"] for tool in contract["x-agent-tools"]["tools"])

    assert DEFAULT_MCP_ALLOWLIST == declared


def test_gateway_never_imports_the_platform_client() -> None:
    source = (
        REPO_ROOT / "agent-backend" / "src" / "agent_backend" / "mcp" / "gateway.py"
    ).read_text(encoding="utf-8")
    assert "PlatformClient" not in source
    assert "import httpx" not in source
    assert "from agent_backend.platform" not in source


def test_build_mcp_gateway_uses_the_configuration() -> None:
    gateway = build_mcp_gateway(
        MCPConfig(
            enabled=True,
            server_name="aemp-qa",
            server_url="http://127.0.0.1:1/mcp",
            connect_timeout_s=0.2,
            call_timeout_s=0.2,
        )
    )

    assert gateway.server_name == "aemp-qa"
    assert gateway.definitions() == ()  # nothing is reachable -> no fake tools


@pytest.mark.parametrize("missing", ["run_flow", "get_run", "run_qa", "get_artifacts"])
def test_every_contract_tool_is_allowlisted(missing: str) -> None:
    assert missing in DEFAULT_MCP_ALLOWLIST
