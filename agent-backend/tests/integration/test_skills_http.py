"""REAL HTTP integration (M7.1): Skill -> Tool -> Platform.

Agent (TestClient) -> LangGraph runtime -> SkillService (timing-analysis)
-> ToolGateway -> PlatformClient -> REAL Platform HTTP Server.

The scripted fake LLM asks for get_run on a non-existent run so the real
404 travels back through the tool message; the system prompt sent to the
model must carry the selected SKILL.md and the catalog - and NOT other
skills' bodies.
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
from agent_backend.skills import SkillLoader, SkillRegistry, SkillService
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.platform import build_platform_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"
SKILLS = AGENT_ROOT / "skills"
FAKE_SERVER = Path(__file__).resolve().parent / "fake_llm_server.py"
MISSING_RUN_ID = "20260101T000000Z-abcdef"


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
def platform_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    log = (tmp_path_factory.mktemp("platform") / "platform.log").open(
        "w", encoding="utf-8"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("AEMP_PROJECT_ROOT", "AEMP_PORT")
    }
    env.update(AEMP_HOST="127.0.0.1", AEMP_PORT=str(port), AEMP_LOG_LEVEL="INFO")
    process = subprocess.Popen(
        [sys.executable, "-m", "aemp.api.http"],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    _wait_for_port(process, port, "platform server")
    try:
        yield f"http://127.0.0.1:{port}"
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
            "get_run",
            "--tool-arguments",
            json.dumps({"run_id": MISSING_RUN_ID}),
            "--final-text",
            "run_not_found",
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


def test_skill_context_and_tool_chain_over_real_http(
    platform_url: str, fake_llm: tuple[str, Path]
) -> None:
    llm_url, llm_log = fake_llm
    platform_client = PlatformClient(platform_url, contracts_dir=CONTRACTS)
    runtime = LangGraphAgentRuntime(
        provider=OpenAICompatibleProvider(
            base_url=llm_url, api_key="test-key", timeout_s=5.0
        ),
        model="fake-model",
        gateway=RegistryToolGateway(build_platform_registry(platform_client)),
        policy=PolicyEngine.default(),
        approvals=ApprovalManager(),
        checkpointer=default_checkpointer(),
        skills=SkillService(SkillRegistry(SKILLS), SkillLoader(SKILLS)),
    )
    config = AgentConfig(platform_base_url=platform_url, contracts_dir=CONTRACTS)
    app = create_app(config=config, chat_service=ChatService(runtime=runtime))

    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "message": f"Analyze the STA timing results of run {MISSING_RUN_ID}",
            "session_id": "skill-1",
        },
    )
    assert response.status_code == 200
    document = response.json()
    validate_document("agent-chat-response", document, CONTRACTS)
    assert document["status"] == "completed"
    assert document["message"] == "run_not_found"

    lines = llm_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # tool decision, then final answer
    first_request = json.loads(lines[0])["body"]
    system_message = first_request["messages"][0]
    assert system_message["role"] == "system"
    # Level 1 catalog + Level 2 selected skill, explicit boundaries
    assert "Available skills" in system_message["content"]
    assert '<skill name="timing-analysis"' in system_message["content"]
    assert "sta.wns_ns" in system_message["content"]
    assert '<skill name="library-qa"' not in system_message["content"]
    # the base safety rules must survive the skill injection
    assert "Do not invent tools" in system_message["content"]

    # the tool result came from the real Platform (404 through HTTP)
    second_request = json.loads(lines[1])["body"]
    tool_message = next(m for m in second_request["messages"] if m["role"] == "tool")
    assert "RUN_NOT_FOUND" in tool_message["content"]

    platform_client.close()
