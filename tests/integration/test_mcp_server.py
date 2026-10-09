"""Real-socket integration test for the Methodology MCP Server (M7.2).

    MCP client --[MCP/Streamable HTTP]--> MCP server --[HTTP]--> Platform API

Both servers are real subprocesses on free loopback ports; the runs are
fixtures created through the real ``RunManager`` in a temporary project, so
no Docker and no EDA tool is needed. Docker absence is *reported*, never
confused with an MCP failure (spec §42): ``run_flow`` then answers the
Platform's own ``TOOL_ENVIRONMENT_UNAVAILABLE``, which is a recorded fact.

The client here is the SDK's own high-level ``Client``: this file proves the
server on the wire, independent of the agent-side MCP client.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from mcp.client.client import Client
from mcp.shared.exceptions import MCPError

from aemp.api.schemas import validate_contract
from aemp.core.config import Settings
from aemp.runs.manager import Run, RunManager

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"
SKILLS = REPO_ROOT / "agent-backend" / "skills"
DOCS = REPO_ROOT / "docs"
RULES = REPO_ROOT / "configs" / "rules" / "base_timing.yaml"
FLOW_SPEC = REPO_ROOT / "configs" / "flows" / "rtl_to_sta.yaml"


@dataclass(frozen=True)
class PlatformFixture:
    """A real Platform HTTP server over a temporary project."""

    url: str
    failing_run_id: str
    clean_run_id: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(process: subprocess.Popen, port: int, name: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited early")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise RuntimeError(f"{name} did not start in 30s") from None
            time.sleep(0.2)


def _make_run(manager: RunManager, metrics: dict[str, float], *, artifact: str = "") -> Run:
    run = manager.create_run("opensta.timing", tool_name="opensta")
    run.record_step("sta")
    run.mark_running()
    run.record_metrics(metrics)
    if artifact:
        path = run.run_dir / "artifacts" / artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("worst slack max 0.29\n", encoding="utf-8")
        run.add_artifact(path, role="sta.report")
    run.finalize("success", engineering_status="unknown")
    return run


@pytest.fixture(scope="module")
def platform_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PlatformFixture]:
    """The real Platform HTTP server over a temporary project (no Docker).

    The temporary project is a real platform project root: it gets the
    repository's own ``contracts/`` and ``configs/`` (copied at test time,
    never committed) plus fixture runs created through the real RunManager.
    """
    project = tmp_path_factory.mktemp("platform-project")
    shutil.copytree(CONTRACTS, project / "contracts")
    shutil.copytree(REPO_ROOT / "configs", project / "configs")

    runs = RunManager(Settings(project_root=project))
    failing = _make_run(
        runs, {"sta.wns_ns": -0.01, "sta.tns_ns": -0.02}, artifact="timing.rpt"
    )
    clean = _make_run(runs, {"sta.wns_ns": 0.29, "sta.tns_ns": 0.0})

    port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("AEMP_PORT", "AEMP_PROJECT_ROOT")
    }
    env.update(
        AEMP_HOST="127.0.0.1",
        AEMP_PORT=str(port),
        AEMP_PROJECT_ROOT=str(project),
        AEMP_LOG_LEVEL="INFO",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "aemp.api.http"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(process, port, "platform server")
    try:
        yield PlatformFixture(
            url=f"http://127.0.0.1:{port}",
            failing_run_id=failing.run_id,
            clean_run_id=clean.run_id,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@pytest.fixture(scope="module")
def mcp_server(
    platform_server: PlatformFixture,
) -> Iterator[tuple[str, PlatformFixture]]:
    """The Methodology MCP server adapting that Platform over HTTP."""
    port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("AEMP_MCP_PORT", "AEMP_MCP_PLATFORM_URL")
    }
    env.update(
        AEMP_MCP_HOST="127.0.0.1",
        AEMP_MCP_PORT=str(port),
        AEMP_MCP_PLATFORM_URL=platform_server.url,
        AEMP_MCP_SKILLS_DIR=str(SKILLS),
        AEMP_MCP_DOCS_DIR=str(DOCS),
        AEMP_LOG_LEVEL="INFO",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "aemp.mcp.server"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(process, port, "mcp server")
    try:
        yield f"http://127.0.0.1:{port}/mcp", platform_server
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _call(url: str, scenario):
    """Run one client interaction against the server (fresh session)."""

    async def runner():
        async with Client(url, read_timeout_seconds=30.0) as client:
            return await scenario(client)

    return asyncio.run(runner())


# -- tools --------------------------------------------------------------------


def test_tools_list_exposes_the_contract_tools(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client) -> list[tuple[str, dict]]:
        result = await client.list_tools()
        return [(tool.name, tool.input_schema) for tool in result.tools]

    tools = _call(url, scenario)

    assert [name for name, _schema in tools] == [
        "run_flow",
        "get_run",
        "run_qa",
        "get_artifacts",
    ]
    schemas = dict(tools)
    assert schemas["get_run"]["required"] == ["run_id"]
    assert schemas["run_qa"]["required"] == ["run_id", "rule_pack"]


def test_health_endpoint_reports_mcp_liveness(mcp_server) -> None:
    """The operator health check (spec §37) is HTTP, not a custom protocol."""
    url, _platform = mcp_server
    base = url.rsplit("/mcp", 1)[0]

    response = httpx.get(f"{base}/healthz", timeout=10.0)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["server"] == "aemp-methodology"
    assert body["mcp_path"] == "/mcp"


def test_get_run_returns_the_platform_document(mcp_server) -> None:
    url, platform = mcp_server

    async def scenario(client: Client):
        return await client.call_tool("get_run", {"run_id": platform.failing_run_id})

    result = _call(url, scenario)

    assert result.is_error is False
    document = result.structured_content
    validate_contract("run", document, CONTRACTS)
    # dual-state survives the whole MCP path
    assert document["execution_status"] == "success"
    assert document["engineering_status"] == "unknown"
    assert document["metrics"]["sta.wns_ns"] == -0.01


def test_run_qa_returns_the_platform_verdict(mcp_server) -> None:
    url, platform = mcp_server

    async def scenario(client: Client, run_id: str):
        return await client.call_tool("run_qa", {"run_id": run_id, "rule_pack": str(RULES)})

    failing = _call(url, lambda client: scenario(client, platform.failing_run_id))
    clean = _call(url, lambda client: scenario(client, platform.clean_run_id))

    assert failing.is_error is False
    validate_contract("qa", failing.structured_content, CONTRACTS)
    assert failing.structured_content["status"] == "FAIL"
    assert clean.structured_content["status"] == "PASS"


def test_get_artifacts_returns_metadata_only(mcp_server) -> None:
    url, platform = mcp_server

    async def scenario(client: Client):
        return await client.call_tool(
            "get_artifacts", {"run_id": platform.failing_run_id}
        )

    result = _call(url, scenario)

    assert result.is_error is False
    document = result.structured_content
    validate_contract("artifact-list", document, CONTRACTS)
    assert document["artifacts"]
    record = document["artifacts"][0]
    assert record["exists"] is True
    assert len(record["sha256"]) == 64


def test_run_flow_reports_the_environment_honestly(mcp_server) -> None:
    """MCP/Platform correctness is proven; real EDA is Docker-dependent."""
    url, _platform = mcp_server

    async def scenario(client: Client):
        return await client.call_tool("run_flow", {"flow": str(FLOW_SPEC)})

    result = _call(url, scenario)

    if result.is_error:
        # No Docker/EDA in this environment: the Platform's own code must
        # survive the MCP path verbatim (never a generic MCP failure).
        assert result.structured_content["error"]["code"] == "TOOL_ENVIRONMENT_UNAVAILABLE"
    else:
        validate_contract("run-summary", result.structured_content, CONTRACTS)


def test_unknown_flow_is_a_structured_platform_error(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client):
        return await client.call_tool("run_flow", {"flow": "does/not/exist.yaml"})

    result = _call(url, scenario)

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "FLOW_NOT_FOUND"


def test_run_not_found_is_a_structured_platform_error(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client):
        return await client.call_tool("get_run", {"run_id": "20260101T000000Z-aaaaaa"})

    result = _call(url, scenario)

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "RUN_NOT_FOUND"


def test_unknown_tool_is_refused(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client):
        return await client.call_tool("shell", {"cmd": "whoami"})

    result = _call(url, scenario)

    assert result.is_error is True
    assert "shell" in result.content[0].text.lower()


def test_invalid_arguments_are_refused(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client):
        return await client.call_tool("get_run", {})

    result = _call(url, scenario)

    assert result.is_error is True
    assert result.structured_content is None  # rejected before any platform call


# -- resources ----------------------------------------------------------------


def test_resources_list_and_read_a_skill(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client) -> tuple[list[str], str]:
        listing = await client.list_resources()
        uris = [str(resource.uri) for resource in listing.resources]
        content = await client.read_resource("methodology://skills/timing-analysis")
        return uris, content.contents[0].text

    uris, text = _call(url, scenario)

    assert "methodology://skills/catalog" in uris
    assert "methodology://skills/timing-analysis" in uris
    assert "timing-analysis" in text


def test_resource_traversal_is_rejected(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client) -> str:
        try:
            await client.read_resource("methodology://skills/../../secret")
        except MCPError as exc:
            return str(exc)
        return "NOT REJECTED"

    message = _call(url, scenario)

    assert message != "NOT REJECTED"
    assert "secret" in message


def test_file_scheme_is_rejected(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client) -> str:
        try:
            await client.read_resource("file:///etc/passwd")
        except MCPError as exc:
            return str(exc)
        return "NOT REJECTED"

    assert _call(url, scenario) != "NOT REJECTED"


def test_unknown_resource_is_rejected(mcp_server) -> None:
    url, _platform = mcp_server

    async def scenario(client: Client) -> str:
        try:
            await client.read_resource("methodology://docs/no-such-document.md")
        except MCPError as exc:
            return str(exc)
        return "NOT REJECTED"

    assert _call(url, scenario) != "NOT REJECTED"
