"""REAL end-to-end integration (M7.2): agent -> MCP -> Platform HTTP.

    Agent HTTP -> LangGraph -> Policy/HITL -> MCPGateway -> MCPClient
        --[MCP/Streamable HTTP]--> Methodology MCP Server
        --[HTTP]--> Platform API (fake, contract-shaped)

The Agent app runs in-process (TestClient); the MCP server and the Platform
endpoint are real subprocesses on free loopback ports. Per the Agent
Backend constitution the Platform end is a fixture server (no Platform
module import, no fabricated run state in the agent package); the *real*
Platform over real run directories is covered by the platform-side suite
``tests/integration/test_mcp_server.py``.

The runtime under test is built by the production factory
(:func:`create_agent_runtime`), so the M6.4 -> M7.2 transport switch is
covered, not simulated.
"""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_backend.api.http import create_app
from agent_backend.config import AgentConfig, LLMConfig, MCPConfig
from agent_backend.contracts import validate_document
from agent_backend.mcp.client import MCPClient
from agent_backend.mcp.errors import MCPClientError
from agent_backend.mcp.models import McpServerSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
SKILLS = REPO_ROOT / "agent-backend" / "skills"
DOCS = REPO_ROOT / "docs"
RULES = REPO_ROOT / "configs" / "rules" / "base_timing.yaml"
FLOW = "configs/flows/rtl_to_sta.yaml"
RUN_ID = "20260101T000000Z-abcdef"

FAKE_LLM = Path(__file__).resolve().parent / "fake_llm_server.py"
FAKE_PLATFORM = Path(__file__).resolve().parent / "fake_platform_server.py"


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


def _stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _spawn(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@dataclass(frozen=True)
class Stack:
    """A fully wired local deployment."""

    platform_url: str
    platform_log: Path
    mcp_url: str


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    work = tmp_path_factory.mktemp("mcp-stack")

    platform_port = _free_port()
    platform_log = work / "platform.log"
    platform = _spawn(
        [
            str(FAKE_PLATFORM),
            "--port",
            str(platform_port),
            "--log",
            str(platform_log),
            "--run-id",
            RUN_ID,
        ]
    )
    _wait_for_port(platform, platform_port, "fake platform")
    platform_url = f"http://127.0.0.1:{platform_port}"

    mcp_port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("AEMP_MCP_PORT", "AEMP_MCP_PLATFORM_URL")
    }
    env.update(
        AEMP_MCP_HOST="127.0.0.1",
        AEMP_MCP_PORT=str(mcp_port),
        AEMP_MCP_PLATFORM_URL=platform_url,
        AEMP_MCP_SKILLS_DIR=str(SKILLS),
        AEMP_MCP_DOCS_DIR=str(DOCS),
        AEMP_LOG_LEVEL="INFO",
    )
    mcp = subprocess.Popen(
        [sys.executable, "-m", "aemp.mcp.server"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(mcp, mcp_port, "mcp server")
    try:
        yield Stack(
            platform_url=platform_url,
            platform_log=platform_log,
            mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
        )
    finally:
        _stop(mcp)
        _stop(platform)


def _fake_llm(
    tmp_path: Path, *, tool_call: str | None, arguments: dict, final_text: str = "done"
) -> tuple[str, Path]:
    port = _free_port()
    log_path = tmp_path / f"fake-llm-{port}.jsonl"
    process = _spawn(
        [
            str(FAKE_LLM),
            "--port",
            str(port),
            "--log",
            str(log_path),
            "--tool-arguments",
            json.dumps(arguments),
            "--final-text",
            final_text,
            *(["--tool-call", tool_call] if tool_call else []),
        ]
    )
    _wait_for_port(process, port, "fake LLM server")
    return f"http://127.0.0.1:{port}", log_path


def _agent_client(
    stack: Stack,
    llm_url: str,
    *,
    mcp_url: str | None = None,
    connect_timeout_s: float = 5.0,
    call_timeout_s: float = 30.0,
) -> TestClient:
    """The production factory wiring, with MCP as the tool transport."""
    config = AgentConfig(
        platform_base_url=stack.platform_url,
        contracts_dir=CONTRACTS,
        runtime="llm",
        llm=LLMConfig(base_url=llm_url, api_key="test-key", model="fake-model"),
        mcp=MCPConfig(
            enabled=True,
            server_url=mcp_url or stack.mcp_url,
            connect_timeout_s=connect_timeout_s,
            call_timeout_s=call_timeout_s,
        ),
    )
    return TestClient(create_app(config=config))


def _tool_message(llm_log: Path) -> dict:
    """The tool message the model actually received (last request)."""
    requests = [
        json.loads(line) for line in llm_log.read_text(encoding="utf-8").splitlines()
    ]
    messages = requests[-1]["body"]["messages"]
    return next(message for message in messages if message["role"] == "tool")


def _hits(log_path: Path, needle: str) -> int:
    if not log_path.is_file():
        return 0
    return log_path.read_text(encoding="utf-8", errors="replace").count(needle)


def _log_text(log_path: Path) -> str:
    return log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""


# -- read-only tools through MCP ------------------------------------------------


def test_get_run_reaches_the_platform_through_mcp(
    stack: Stack, tmp_path: Path
) -> None:
    llm_url, llm_log = _fake_llm(
        tmp_path, tool_call="get_run", arguments={"run_id": RUN_ID}
    )
    client = _agent_client(stack, llm_url)

    before = _hits(stack.platform_log, f"GET /api/v1/runs/{RUN_ID}\n")
    response = client.post(
        "/api/v1/chat", json={"message": "Read the run.", "session_id": "m1"}
    )

    assert response.status_code == 200
    body = response.json()
    validate_document("agent-chat-response", body, CONTRACTS)
    assert body["status"] == "completed"

    message = _tool_message(llm_log)
    assert RUN_ID in message["content"]
    assert "engineering_status" in message["content"]
    # the request really travelled agent -> MCP -> Platform HTTP
    assert _hits(stack.platform_log, f"GET /api/v1/runs/{RUN_ID}\n") == before + 1


def test_run_qa_returns_the_platform_verdict(stack: Stack, tmp_path: Path) -> None:
    llm_url, llm_log = _fake_llm(
        tmp_path,
        tool_call="run_qa",
        arguments={"run_id": RUN_ID, "rule_pack": str(RULES)},
    )
    client = _agent_client(stack, llm_url)

    response = client.post(
        "/api/v1/chat", json={"message": "QA the run.", "session_id": "m2"}
    )

    assert response.json()["status"] == "completed"
    content = _tool_message(llm_log)["content"]
    assert "FAIL" in content  # the Platform's verdict, passed through unchanged
    assert RUN_ID in content


def test_get_artifacts_returns_metadata(stack: Stack, tmp_path: Path) -> None:
    llm_url, llm_log = _fake_llm(
        tmp_path, tool_call="get_artifacts", arguments={"run_id": RUN_ID}
    )
    client = _agent_client(stack, llm_url)

    response = client.post(
        "/api/v1/chat", json={"message": "Which artifacts?", "session_id": "m3"}
    )

    assert response.json()["status"] == "completed"
    content = _tool_message(llm_log)["content"]
    assert "artifacts/timing.rpt" in content
    assert "sha256" in content


# -- HITL through MCP ----------------------------------------------------------


def test_run_flow_interrupts_then_executes_once_on_approval(
    stack: Stack, tmp_path: Path
) -> None:
    llm_url, llm_log = _fake_llm(tmp_path, tool_call="run_flow", arguments={"flow": FLOW})
    client = _agent_client(stack, llm_url)

    before = _hits(stack.platform_log, "POST /api/v1/flows/run\n")
    chat = client.post(
        "/api/v1/chat", json={"message": "Run rtl_to_sta.", "session_id": "m4"}
    )
    interrupted = chat.json()
    validate_document("agent-chat-response", interrupted, CONTRACTS)
    assert interrupted["status"] == "interrupted"
    assert interrupted["approval_required"] is True
    # NOTHING reached the Platform before the human decision - not even the
    # MCP server: policy runs before the gateway.
    assert _hits(stack.platform_log, "POST /api/v1/flows/run\n") == before

    resume = client.post(
        "/api/v1/chat/resume",
        json={
            "session_id": "m4",
            "approval_id": interrupted["approval_id"],
            "decision": "approved",
        },
    )

    assert resume.status_code == 200
    assert resume.json()["status"] == "completed"
    assert _hits(stack.platform_log, "POST /api/v1/flows/run\n") == before + 1
    # the Platform's own environment verdict survived the whole MCP path
    assert "TOOL_ENVIRONMENT_UNAVAILABLE" in _tool_message(llm_log)["content"]


def test_rejected_run_flow_never_reaches_the_platform(
    stack: Stack, tmp_path: Path
) -> None:
    llm_url, llm_log = _fake_llm(tmp_path, tool_call="run_flow", arguments={"flow": FLOW})
    client = _agent_client(stack, llm_url)

    before = _hits(stack.platform_log, "POST /api/v1/flows/run\n")
    chat = client.post(
        "/api/v1/chat", json={"message": "Run rtl_to_sta.", "session_id": "m5"}
    ).json()
    assert chat["status"] == "interrupted"

    resume = client.post(
        "/api/v1/chat/resume",
        json={
            "session_id": "m5",
            "approval_id": chat["approval_id"],
            "decision": "rejected",
            "reason": "not authorized",
        },
    )

    assert resume.json()["status"] == "completed"
    assert _hits(stack.platform_log, "POST /api/v1/flows/run\n") == before
    assert "APPROVAL_REJECTED" in _tool_message(llm_log)["content"]


def test_skills_loaded_do_not_bypass_hitl_through_mcp(
    stack: Stack, tmp_path: Path
) -> None:
    """M7.1 skills + M7.2 MCP: the skill only informs, approval still gates."""
    llm_url, llm_log = _fake_llm(tmp_path, tool_call="run_flow", arguments={"flow": FLOW})
    client = _agent_client(stack, llm_url)

    before = _hits(stack.platform_log, "POST /api/v1/flows/run\n")
    # This message matches the flow-engineering / timing-analysis skills, so a
    # skill context IS injected into the system prompt.
    chat = client.post(
        "/api/v1/chat",
        json={"message": "Run RTL to STA and check timing.", "session_id": "m9"},
    ).json()

    assert chat["status"] == "interrupted"
    assert _hits(stack.platform_log, "POST /api/v1/flows/run\n") == before

    resume = client.post(
        "/api/v1/chat/resume",
        json={
            "session_id": "m9",
            "approval_id": chat["approval_id"],
            "decision": "approved",
        },
    ).json()

    assert resume["status"] == "completed"
    assert _hits(stack.platform_log, "POST /api/v1/flows/run\n") == before + 1
    # the skills reached the model, and the tool names did not change for them
    system = json.loads(llm_log.read_text(encoding="utf-8").splitlines()[0])["body"][
        "messages"
    ][0]["content"]
    assert "<skill" in system


# -- security matrix -----------------------------------------------------------


def test_tool_outside_the_allowlist_is_refused(stack: Stack, tmp_path: Path) -> None:
    llm_url, llm_log = _fake_llm(
        tmp_path, tool_call="shell", arguments={"cmd": "whoami"}
    )
    client = _agent_client(stack, llm_url)

    before = _log_text(stack.platform_log)
    response = client.post(
        "/api/v1/chat", json={"message": "Run a shell command.", "session_id": "m6"}
    )

    assert response.json()["status"] == "completed"
    content = _tool_message(llm_log)["content"]
    assert "MCP_TOOL_NOT_ALLOWED" in content
    # the Platform (and the MCP server) were never asked
    assert _log_text(stack.platform_log) == before


def test_invalid_arguments_are_refused(stack: Stack, tmp_path: Path) -> None:
    llm_url, llm_log = _fake_llm(tmp_path, tool_call="get_run", arguments={})
    client = _agent_client(stack, llm_url)

    before = stack.platform_log.read_text(encoding="utf-8", errors="replace")
    response = client.post(
        "/api/v1/chat", json={"message": "Read a run.", "session_id": "m7"}
    )

    assert response.json()["status"] == "completed"
    content = _tool_message(llm_log)["content"]
    # refused before any MCP/Platform call: the gateway validates the
    # discovered schema first (and the MCP server would reject it again)
    assert "MCP_TOOL_ARGUMENTS_INVALID" in content
    assert _log_text(stack.platform_log) == before


def test_unavailable_mcp_server_is_a_structured_failure(
    stack: Stack, tmp_path: Path
) -> None:
    """Enabled but unreachable: explicit failure, never a silent fallback."""
    dead = f"http://127.0.0.1:{_free_port()}/mcp"
    llm_url, llm_log = _fake_llm(
        tmp_path, tool_call="get_run", arguments={"run_id": RUN_ID}
    )
    client = _agent_client(
        stack, llm_url, mcp_url=dead, connect_timeout_s=0.5, call_timeout_s=0.5
    )

    before = _hits(stack.platform_log, f"GET /api/v1/runs/{RUN_ID}\n")
    response = client.post(
        "/api/v1/chat", json={"message": "Read the run.", "session_id": "m8"}
    )

    assert response.json()["status"] == "completed"
    content = _tool_message(llm_log)["content"]
    assert "MCP_SERVER_UNAVAILABLE" in content or "MCP_CONNECTION_FAILED" in content
    # no automatic fallback to the local platform tools
    assert _hits(stack.platform_log, f"GET /api/v1/runs/{RUN_ID}\n") == before


# -- client-level checks against the real MCP server ---------------------------


def _client(stack: Stack, **kwargs) -> MCPClient:
    return MCPClient(
        McpServerSpec(name="aemp-methodology", url=stack.mcp_url),
        connect_timeout_s=kwargs.get("connect_timeout_s", 5.0),
        call_timeout_s=kwargs.get("call_timeout_s", 30.0),
    )


def test_discovery_lists_the_contract_tools(stack: Stack) -> None:
    client = _client(stack)
    try:
        names = [tool.name for tool in client.list_tools()]
        assert names == ["run_flow", "get_run", "run_qa", "get_artifacts"]
        assert client.server_info is not None
        assert client.server_info["name"] == "aemp-methodology"
        assert client.server_info["version"]
    finally:
        client.close()


def test_resources_are_reachable_and_traversal_is_refused(stack: Stack) -> None:
    client = _client(stack)
    try:
        uris = [resource.uri for resource in client.list_resources()]
        assert "methodology://skills/timing-analysis" in uris

        content = client.read_resource("methodology://skills/timing-analysis")
        assert "timing-analysis" in content.text

        with pytest.raises(MCPClientError) as excinfo:
            client.read_resource("methodology://skills/../../secret")
        assert excinfo.value.code == "MCP_RESOURCE_READ_FAILED"
    finally:
        client.close()


def test_mcp_call_timeout_is_reported(stack: Stack) -> None:
    client = _client(stack, call_timeout_s=0.001)
    try:
        with pytest.raises(MCPClientError) as excinfo:
            client.list_tools()
        assert excinfo.value.code in ("MCP_CALL_TIMEOUT", "MCP_CONNECTION_FAILED")
    finally:
        client.close()


def test_disconnected_client_reports_server_unavailable() -> None:
    client = MCPClient(
        McpServerSpec(name="aemp-methodology", url=f"http://127.0.0.1:{_free_port()}/mcp"),
        connect_timeout_s=0.5,
        call_timeout_s=0.5,
    )
    try:
        with pytest.raises(MCPClientError) as excinfo:
            client.list_tools()
        assert excinfo.value.code in ("MCP_SERVER_UNAVAILABLE", "MCP_CONNECTION_FAILED")
    finally:
        client.close()
