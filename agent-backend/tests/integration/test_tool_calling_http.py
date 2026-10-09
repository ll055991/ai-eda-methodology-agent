"""REAL HTTP integration (M6.4/M6.5): the full tool-calling chain.

Agent (TestClient) -> LangGraphAgentRuntime (StateGraph) -> Tool Gateway
-> Platform Tools -> PlatformClient -> REAL Platform HTTP Server
-> Tool Result -> LLM.

The model is scripted in-process (no real LLM, no API key); the Platform
hop is a real socket to a uvicorn subprocess. The scripted call targets a
valid-format, non-existent run, so the real 404 travels back through the
gateway as a structured PLATFORM_API_ERROR and the model then answers.
"""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_backend.api.http import create_app
from agent_backend.application.chat import ChatService
from agent_backend.application.langgraph_runtime import LangGraphAgentRuntime
from agent_backend.config import AgentConfig
from agent_backend.contracts import validate_document
from agent_backend.llm.models import ModelRequest, ModelResponse
from agent_backend.platform.client import PlatformClient
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.models import ToolCall
from agent_backend.tools.platform import build_platform_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
MISSING_RUN_ID = "20260101T000000Z-abcdef"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def platform_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    log_path = tmp_path_factory.mktemp("platform-server") / "uvicorn.log"
    log = log_path.open("w", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "AEMP_PROJECT_ROOT"}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "aemp.api.http:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                log.close()
                raise RuntimeError(
                    "platform server exited early:\n"
                    + log_path.read_text(encoding="utf-8")
                )
            try:
                if httpx.get(f"{url}/healthz", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("platform server did not become healthy in 30s")
            time.sleep(0.2)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


class ScriptedProvider:
    """First call: ask for get_run on a missing run; second: answer."""

    name = "scripted"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                content="",
                provider="scripted",
                tool_calls=(
                    ToolCall(
                        id="call_1", name="get_run", arguments={"run_id": MISSING_RUN_ID}
                    ),
                ),
            )
        return ModelResponse(
            content="That run does not exist on the platform.",
            provider="scripted",
        )


def test_tool_calling_full_chain_over_real_http(platform_url: str) -> None:
    provider = ScriptedProvider()
    client = PlatformClient(platform_url, contracts_dir=CONTRACTS)
    gateway = RegistryToolGateway(build_platform_registry(client))
    runtime = LangGraphAgentRuntime(
        provider=provider, model="fake-model", gateway=gateway, max_tool_rounds=3
    )
    config = AgentConfig(platform_base_url=platform_url, contracts_dir=CONTRACTS)
    app = create_app(config=config, chat_service=ChatService(runtime=runtime))

    response = TestClient(app).post(
        "/api/v1/chat", json={"message": f"what is in run {MISSING_RUN_ID}?"}
    )

    assert response.status_code == 200
    document = response.json()
    validate_document("agent-chat-response", document, CONTRACTS)
    assert document["status"] == "completed"
    assert document["message"] == "That run does not exist on the platform."

    # the model saw a real HTTP 404 from the Platform, mapped to a tool error
    assert len(provider.requests) == 2
    second_request = provider.requests[1]
    assert [message.role for message in second_request.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    tool_message = second_request.messages[-1]
    assert tool_message.tool_call_id == "call_1"
    payload = json.loads(tool_message.content)
    assert payload["status"] == "error"
    assert payload["error_code"] == "PLATFORM_API_ERROR"
    assert "RUN_NOT_FOUND" in payload["error_message"]

    client.close()
