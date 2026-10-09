"""MCP server assembly tests (M7.2): tools, schemas, structured results.

The server is an adapter: these tests pin that exactly the four contract
tools are exposed, that their argument schemas come from the handler
signatures, and that a Platform failure arrives as a structured ``isError``
result instead of a protocol crash.
"""

import asyncio
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from aemp.mcp.client import PlatformHttpClient
from aemp.mcp.config import McpServerConfig
from aemp.mcp.resources import ResourceService
from aemp.mcp.server import SERVER_NAME, SERVER_VERSION, build_server
from aemp.mcp.tools import TOOL_NAMES, McpToolService


def _server(handler, *, skills_dir: Path | None = None, docs_dir: Path | None = None):
    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://platform.test"
    )
    tools = McpToolService(
        PlatformHttpClient("http://platform.test", http_client=http)
    )
    resources = ResourceService(skills_dir=skills_dir, docs_dir=docs_dir)
    config = McpServerConfig(platform_base_url="http://platform.test")
    return build_server(config, tools=tools, resources=resources)


def test_server_exposes_exactly_the_contract_tools() -> None:
    server = _server(lambda _request: httpx.Response(200, json={}))

    tools = asyncio.run(server.list_tools())
    names = [tool.name for tool in tools]

    assert names == list(TOOL_NAMES)
    server_info = server.name, server.version
    assert server_info == (SERVER_NAME, SERVER_VERSION)


def test_tool_schemas_are_derived_from_the_handler_signatures() -> None:
    server = _server(lambda _request: httpx.Response(200, json={}))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    run_flow = tools["run_flow"].input_schema
    assert run_flow["required"] == ["flow"]
    assert run_flow["properties"]["flow"]["minLength"] == 1
    assert "inputs" in run_flow["properties"]

    for name in ("get_run", "get_artifacts"):
        assert tools[name].input_schema["required"] == ["run_id"]
    assert tools["run_qa"].input_schema["required"] == ["run_id", "rule_pack"]


def test_success_is_structured_content_without_error() -> None:
    document = {"run_id": "20260101T000000Z-abcdef", "engineering_status": "unknown"}
    server = _server(lambda _request: httpx.Response(200, json=document))

    result = asyncio.run(server.call_tool("get_run", {"run_id": "x"}))

    assert result.is_error is False
    assert result.structured_content == document


def test_platform_failure_is_a_structured_error_result() -> None:
    server = _server(
        lambda _request: httpx.Response(
            503,
            json={
                "error": {
                    "code": "TOOL_ENVIRONMENT_UNAVAILABLE",
                    "message": "no docker",
                    "details": {},
                }
            },
        )
    )

    result = asyncio.run(server.call_tool("run_flow", {"flow": "f.yaml"}))

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "TOOL_ENVIRONMENT_UNAVAILABLE"
    # the text content mirrors the structured payload (no bare "success")
    assert "TOOL_ENVIRONMENT_UNAVAILABLE" in result.content[0].text


def test_unknown_tool_is_refused_by_the_server() -> None:
    server = _server(lambda _request: httpx.Response(200, json={}))

    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("shell", {"cmd": "rm -rf /"}))


def test_invalid_arguments_are_refused_before_any_platform_call() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    server = _server(handler)

    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("get_run", {}))
    assert calls == []  # the Platform was never asked


def test_resources_are_registered_from_the_roots(tmp_path: Path) -> None:
    skills = tmp_path / "skills" / "timing-analysis"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        '---\nname: timing-analysis\ndescription: STA\nversion: "1.0"\ntags: [sta]\n---\n\nBody\n',
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "01-architecture.md").write_text("# Arch\n", encoding="utf-8")

    server = _server(
        lambda _request: httpx.Response(200, json={}),
        skills_dir=tmp_path / "skills",
        docs_dir=docs,
    )
    resources = asyncio.run(server.list_resources())
    uris = [str(resource.uri) for resource in resources]

    assert "methodology://skills/catalog" in uris
    assert "methodology://skills/timing-analysis" in uris
    assert "methodology://docs/01-architecture.md" in uris


def test_no_resources_is_valid(tmp_path: Path) -> None:
    server = _server(
        lambda _request: httpx.Response(200, json={}),
        skills_dir=tmp_path / "missing",
        docs_dir=None,
    )

    assert asyncio.run(server.list_resources()) == []
    assert [tool.name for tool in asyncio.run(server.list_tools())] == list(TOOL_NAMES)
