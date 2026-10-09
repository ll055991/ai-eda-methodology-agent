"""Real HTTP integration: Agent LLM runtime -> fake OpenAI-compatible server.

The agent app runs in-process (TestClient); the model hop is a REAL HTTP
request over a socket to a stdlib fake server. No real API key, no real
LLM - the literal ``test-key`` is asserted on the wire.
"""

import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_backend.api.http import create_app
from agent_backend.config import AgentConfig, LLMConfig
from agent_backend.contracts import validate_document

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
FAKE_SERVER = Path(__file__).resolve().parent / "fake_llm_server.py"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def fake_llm(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    port = _free_port()
    log_path = tmp_path_factory.mktemp("fake-llm") / "requests.jsonl"
    process = subprocess.Popen(
        [sys.executable, str(FAKE_SERVER), "--port", str(port), "--log", str(log_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise RuntimeError("fake LLM server exited early")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("fake LLM server did not start in 15s") from None
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}", log_path
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def test_agent_llm_http_end_to_end(fake_llm: tuple[str, Path]) -> None:
    base_url, log_path = fake_llm
    config = AgentConfig(
        platform_base_url="http://127.0.0.1:9",  # dead: no platform access expected
        contracts_dir=CONTRACTS,
        runtime="llm",
        llm=LLMConfig(
            provider="openai_compatible",
            base_url=base_url,
            api_key="test-key",
            model="fake-model",
            timeout_s=5.0,
        ),
    )
    app = create_app(config=config)
    response = TestClient(app).post("/api/v1/chat", json={"message": "ping"})

    assert response.status_code == 200
    document = response.json()
    validate_document("agent-chat-response", document, CONTRACTS)
    assert document["status"] == "completed"
    assert document["message"] == "fake-llm answer"

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines, "fake LLM server received no request"
    received = json.loads(lines[-1])
    assert received["path"] == "/chat/completions"
    assert received["authorization"] == "Bearer test-key"
    assert received["body"]["model"] == "fake-model"
    assert [m["role"] for m in received["body"]["messages"]] == ["system", "user"]
    assert received["body"]["messages"][1]["content"] == "ping"


def test_llm_failure_surfaces_as_failed_status() -> None:
    """Provider unreachable -> status=failed, bounded message, HTTP 200."""
    config = AgentConfig(
        platform_base_url="http://127.0.0.1:9",
        contracts_dir=CONTRACTS,
        runtime="llm",
        llm=LLMConfig(
            provider="openai_compatible",
            base_url="http://127.0.0.1:9",  # nothing listens here
            api_key="test-key",
            model="fake-model",
            timeout_s=1.0,
        ),
    )
    app = create_app(config=config)
    response = TestClient(app).post("/api/v1/chat", json={"message": "ping"})

    assert response.status_code == 200
    document = response.json()
    validate_document("agent-chat-response", document, CONTRACTS)
    assert document["status"] == "failed"
    assert "LLM" in document["message"]
    assert "test-key" not in response.text
