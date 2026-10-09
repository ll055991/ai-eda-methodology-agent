"""REAL HTTP integration (M6.6): HITL interrupt -> approve/reject -> resume.

Agent (TestClient) -> LangGraph runtime -> Policy -> interrupt/checkpoint
-> /api/v1/chat/resume -> ToolGateway -> PlatformClient -> REAL Platform
HTTP Server. The model is a scripted fake LLM; the platform is a real
uvicorn subprocess. No Docker -> the approved run_flow reaches the
Platform and is answered with 503 (recorded honestly as no real EDA).
"""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_backend.api.http import create_app
from agent_backend.application.approval import ApprovalManager
from agent_backend.application.chat import ChatService
from agent_backend.application.langgraph_runtime import (
    LangGraphAgentRuntime,
    default_checkpointer,
)
from agent_backend.config import AgentConfig
from agent_backend.contracts import validate_document
from agent_backend.llm.openai_compatible import OpenAICompatibleProvider
from agent_backend.platform.client import PlatformClient
from agent_backend.policy import PolicyEngine
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.platform import build_platform_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
FAKE_SERVER = Path(__file__).resolve().parent / "fake_llm_server.py"
FLOW = "configs/flows/rtl_to_sta.yaml"


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


@pytest.fixture(scope="module")
def platform_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    port = _free_port()
    log_path = tmp_path_factory.mktemp("platform") / "platform.log"
    log = log_path.open("w", encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("AEMP_PROJECT_ROOT", "AEMP_PORT")
    }
    env.update(AEMP_HOST="127.0.0.1", AEMP_PORT=str(port), AEMP_LOG_LEVEL="INFO")
    # `python -m aemp.api.http` runs setup_logging, so the request middleware
    # log (the real-HTTP evidence) reaches this file.
    process = subprocess.Popen(
        [sys.executable, "-m", "aemp.api.http"],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    _wait_for_port(process, port, "platform server")
    try:
        yield f"http://127.0.0.1:{port}", log_path
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


@pytest.fixture
def fake_llm(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    port = _free_port()
    log_path = tmp_path / "fake-llm.jsonl"
    process = subprocess.Popen(
        [
            sys.executable,
            str(FAKE_SERVER),
            "--port",
            str(port),
            "--log",
            str(log_path),
            "--tool-call",
            "run_flow",
            "--tool-arguments",
            json.dumps({"flow": FLOW}),
            "--final-text",
            "fake-llm answer",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(process, port, "fake LLM server")
    try:
        yield f"http://127.0.0.1:{port}", log_path
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _run_flow_hits(platform_log: Path) -> int:
    # count the platform middleware line only (uvicorn's access log also
    # mentions the path, which would double-count a single request)
    return platform_log.read_text(encoding="utf-8", errors="replace").count(
        "/api/v1/flows/run ->"
    )


def _make_agent_client(
    platform_url: str, llm_url: str
) -> tuple[TestClient, PlatformClient]:
    client = PlatformClient(platform_url, contracts_dir=CONTRACTS)
    runtime = LangGraphAgentRuntime(
        provider=OpenAICompatibleProvider(
            base_url=llm_url, api_key="test-key", timeout_s=5.0
        ),
        model="fake-model",
        gateway=RegistryToolGateway(build_platform_registry(client)),
        policy=PolicyEngine.default(),
        approvals=ApprovalManager(),
        checkpointer=default_checkpointer(),
    )
    config = AgentConfig(platform_base_url=platform_url, contracts_dir=CONTRACTS)
    app = create_app(config=config, chat_service=ChatService(runtime=runtime))
    return TestClient(app), client


def test_approve_resumes_into_real_platform(
    platform_server: tuple[str, Path], fake_llm: tuple[str, Path]
) -> None:
    platform_url, platform_log = platform_server
    llm_url, llm_log = fake_llm
    client, platform_client = _make_agent_client(platform_url, llm_url)

    before = _run_flow_hits(platform_log)
    chat = client.post(
        "/api/v1/chat", json={"message": "Run rtl_to_sta.", "session_id": "hitl-1"}
    )
    assert chat.status_code == 200
    interrupted = chat.json()
    validate_document("agent-chat-response", interrupted, CONTRACTS)
    assert interrupted["status"] == "interrupted"
    assert interrupted["approval_required"] is True
    # NOTHING reached the Platform before the human decision
    assert _run_flow_hits(platform_log) == before

    resume = client.post(
        "/api/v1/chat/resume",
        json={
            "session_id": "hitl-1",
            "approval_id": interrupted["approval_id"],
            "decision": "approved",
        },
    )
    assert resume.status_code == 200
    resumed = resume.json()
    validate_document("agent-chat-response", resumed, CONTRACTS)
    assert resumed["status"] == "completed"
    assert resumed["message"] == "fake-llm answer"

    # the approved run_flow reached the real Platform (503: no Docker/EDA)
    assert _run_flow_hits(platform_log) == before + 1

    llm_requests = llm_log.read_text(encoding="utf-8").splitlines()
    assert len(llm_requests) == 2  # tool decision, then final answer
    tool_message = next(
        message
        for message in json.loads(llm_requests[1])["body"]["messages"]
        if message["role"] == "tool"
    )
    assert "TOOL_ENVIRONMENT_UNAVAILABLE" in tool_message["content"]

    platform_client.close()


def test_reject_never_reaches_platform(
    platform_server: tuple[str, Path], fake_llm: tuple[str, Path]
) -> None:
    platform_url, platform_log = platform_server
    llm_url, llm_log = fake_llm
    client, platform_client = _make_agent_client(platform_url, llm_url)

    before = _run_flow_hits(platform_log)
    chat = client.post(
        "/api/v1/chat", json={"message": "Run rtl_to_sta.", "session_id": "hitl-2"}
    ).json()
    assert chat["status"] == "interrupted"

    resume = client.post(
        "/api/v1/chat/resume",
        json={
            "session_id": "hitl-2",
            "approval_id": chat["approval_id"],
            "decision": "rejected",
            "reason": "not authorized",
        },
    )
    assert resume.status_code == 200
    assert resume.json()["status"] == "completed"
    # rejected tool: the Platform never saw the request
    assert _run_flow_hits(platform_log) == before

    llm_requests = llm_log.read_text(encoding="utf-8").splitlines()
    tool_message = next(
        message
        for message in json.loads(llm_requests[1])["body"]["messages"]
        if message["role"] == "tool"
    )
    assert "APPROVAL_REJECTED" in tool_message["content"]

    platform_client.close()
