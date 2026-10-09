"""Agent HTTP transport tests (in-process TestClient, no network)."""

import re
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from agent_backend.api.http import create_app
from agent_backend.application.chat import ChatService, LLMAgentRuntime
from agent_backend.config import AgentConfig
from agent_backend.contracts import validate_document
from agent_backend.llm.models import ModelRequest, ModelResponse
from agent_backend.models.chat import CHAT_STATUS_COMPLETED, ChatRequest, ChatResponse
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"

# a dead Platform URL proves /healthz and /chat never call the Platform
DEAD_PLATFORM = "http://127.0.0.1:9"


def make_client(**config_overrides) -> TestClient:
    config = AgentConfig(
        platform_base_url=DEAD_PLATFORM, contracts_dir=CONTRACTS, **config_overrides
    )
    return TestClient(create_app(config=config))


class TestHealth:
    def test_healthz_ok_without_platform(self) -> None:
        response = make_client().get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["X-Request-ID"]

    def test_request_id_echoed(self) -> None:
        response = make_client().get("/healthz", headers={"X-Request-ID": "rid-9"})
        assert response.headers["X-Request-ID"] == "rid-9"


class TestChat:
    def test_stub_chat_matches_agent_contract(self) -> None:
        response = make_client().post("/api/v1/chat", json={"message": "最近一次运行"})
        assert response.status_code == 200
        document = response.json()
        validate_document("agent-chat-response", document, CONTRACTS)
        assert document["status"] == CHAT_STATUS_COMPLETED
        assert "stub" in document["message"]
        assert document["run_id"] is None
        assert document["approval_required"] is False
        assert document["session_id"]

    def test_session_id_is_echoed(self) -> None:
        response = make_client().post(
            "/api/v1/chat", json={"message": "hi", "session_id": "s-7"}
        )
        assert response.json()["session_id"] == "s-7"

    def test_unknown_field_rejected(self) -> None:
        response = make_client().post(
            "/api/v1/chat", json={"message": "hi", "tools": ["shell"]}
        )
        assert response.status_code == 400
        document = response.json()
        validate_document("error", document, CONTRACTS)
        assert document["error"]["code"] == "INVALID_REQUEST"

    def test_missing_message_rejected(self) -> None:
        response = make_client().post("/api/v1/chat", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"

    def test_empty_message_rejected(self) -> None:
        response = make_client().post("/api/v1/chat", json={"message": ""})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"


class TestLLMMode:
    def test_llm_runtime_through_http(self) -> None:
        class FixedProvider:
            name = "fake"

            def chat(self, request: ModelRequest) -> ModelResponse:
                return ModelResponse(
                    content="llm says hi", provider="fake", model=request.model
                )

        config = AgentConfig(platform_base_url=DEAD_PLATFORM, contracts_dir=CONTRACTS)
        app = create_app(
            config=config,
            chat_service=ChatService(
                runtime=LLMAgentRuntime(provider=FixedProvider(), model="fake-model")
            ),
        )
        response = TestClient(app).post("/api/v1/chat", json={"message": "hi"})

        assert response.status_code == 200
        document = response.json()
        validate_document("agent-chat-response", document, CONTRACTS)
        assert document["status"] == CHAT_STATUS_COMPLETED
        assert document["message"] == "llm says hi"

    def test_misconfigured_llm_runtime_fails_at_startup(self) -> None:
        from agent_backend.llm.errors import LLMConfigurationError

        config = AgentConfig(
            platform_base_url=DEAD_PLATFORM,
            contracts_dir=CONTRACTS,
            runtime="llm",  # no LLM credentials configured
        )
        try:
            create_app(config=config)
        except LLMConfigurationError as exc:
            assert "AEMP_LLM_" in str(exc)
        else:  # pragma: no cover - the app must not start misconfigured
            raise AssertionError("create_app must reject an unconfigured llm runtime")


class TestToolCallingMode:
    def test_tool_calling_through_http(self) -> None:
        class ScriptedProvider:
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
                            ToolCall(id="c1", name="count_runs", arguments={"limit": 2}),
                        ),
                    )
                return ModelResponse(content="There are runs.", provider="scripted")

        class CountTool:
            definition = ToolDefinition(
                name="count_runs",
                description="Count runs.",
                input_schema={
                    "type": "object",
                    "required": ["limit"],
                    "additionalProperties": False,
                    "properties": {"limit": {"type": "integer"}},
                },
            )

            def __init__(self) -> None:
                self.calls: list[ToolCall] = []

            def execute(self, call: ToolCall) -> ToolResult:
                self.calls.append(call)
                return ToolResult.success(call, {"count": 3})

        tool = CountTool()
        registry = ToolRegistry()
        registry.register(tool)
        runtime = LLMAgentRuntime(
            provider=ScriptedProvider(),
            model="fake-model",
            gateway=RegistryToolGateway(registry),
        )
        config = AgentConfig(platform_base_url=DEAD_PLATFORM, contracts_dir=CONTRACTS)
        app = create_app(config=config, chat_service=ChatService(runtime=runtime))

        response = TestClient(app).post(
            "/api/v1/chat", json={"message": "how many runs?"}
        )
        assert response.status_code == 200
        document = response.json()
        validate_document("agent-chat-response", document, CONTRACTS)
        assert document["status"] == CHAT_STATUS_COMPLETED
        assert document["message"] == "There are runs."
        assert len(tool.calls) == 1


class TestInternalError:
    def test_runtime_crash_is_generic_500(self) -> None:
        class ExplodingRuntime:
            def handle(self, request: ChatRequest) -> ChatResponse:
                raise RuntimeError("boom: secret detail")

        config = AgentConfig(platform_base_url=DEAD_PLATFORM, contracts_dir=CONTRACTS)
        app = create_app(config=config, chat_service=ChatService(runtime=ExplodingRuntime()))
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/api/v1/chat", json={"message": "hi"})
        assert response.status_code == 500
        document = response.json()
        validate_document("error", document, CONTRACTS)
        assert document["error"]["code"] == "INTERNAL_ERROR"
        assert document["error"]["message"] == "Internal server error"
        assert "boom" not in response.text


class TestContractAlignment:
    def test_served_openapi_is_the_agent_contract(self) -> None:
        client = make_client()
        response = client.get("/openapi.json")
        assert response.status_code == 200
        served = response.json()
        contract = yaml.safe_load(
            (CONTRACTS / "agent-api.yaml").read_text(encoding="utf-8")
        )
        assert served["info"]["title"] == contract["info"]["title"]
        assert set(served["paths"]) == set(contract["paths"])
        assert not _has_external_ref(served)

    def test_docs_available(self) -> None:
        assert make_client().get("/docs").status_code == 200

    def test_routes_match_contract_paths(self) -> None:
        client = make_client()
        contract = yaml.safe_load(
            (CONTRACTS / "agent-api.yaml").read_text(encoding="utf-8")
        )
        contract_ops = {
            (path, method.upper())
            for path, operations in contract["paths"].items()
            for method in operations
            if method != "parameters"
        }
        app_ops = {
            (re.sub(r"\{(\w+):\w+\}", r"{\1}", route.path), method)
            for route in client.app.routes
            if getattr(route, "methods", None) and route.path.startswith("/api/")
            for method in route.methods
            if method != "HEAD"
        }
        assert app_ops == contract_ops


class TestHITLMode:
    class _ScriptedProvider:
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
                        ToolCall(id="c1", name="run_flow", arguments={"flow": "x.yaml"}),
                    ),
                )
            return ModelResponse(content="done", provider="scripted")

    class _RunFlowTool:
        definition = ToolDefinition(
            name="run_flow",
            description="Run a flow.",
            input_schema={
                "type": "object",
                "required": ["flow"],
                "additionalProperties": False,
                "properties": {"flow": {"type": "string"}},
            },
        )

        def __init__(self) -> None:
            self.calls: list[ToolCall] = []

        def execute(self, call: ToolCall) -> ToolResult:
            self.calls.append(call)
            return ToolResult.success(call, {"run_id": "r1"})

    def _client(self):
        tool = self._RunFlowTool()
        registry = ToolRegistry()
        registry.register(tool)
        runtime = LLMAgentRuntime(
            provider=self._ScriptedProvider(),
            model="fake-model",
            gateway=RegistryToolGateway(registry),
        )
        config = AgentConfig(platform_base_url=DEAD_PLATFORM, contracts_dir=CONTRACTS)
        app = create_app(config=config, chat_service=ChatService(runtime=runtime))
        return TestClient(app), tool

    def test_chat_interrupts_then_resume_approves(self) -> None:
        client, tool = self._client()
        chat = client.post(
            "/api/v1/chat", json={"message": "run it", "session_id": "s-1"}
        )
        assert chat.status_code == 200
        document = chat.json()
        validate_document("agent-chat-response", document, CONTRACTS)
        assert document["status"] == "interrupted"
        assert document["approval_required"] is True
        assert document["approval_id"]
        assert tool.calls == []  # never executed before approval

        resume = client.post(
            "/api/v1/chat/resume",
            json={
                "session_id": "s-1",
                "approval_id": document["approval_id"],
                "decision": "approved",
            },
        )
        assert resume.status_code == 200
        resumed = resume.json()
        validate_document("agent-chat-response", resumed, CONTRACTS)
        assert resumed["status"] == CHAT_STATUS_COMPLETED
        assert len(tool.calls) == 1

    def test_resume_reject_skips_tool(self) -> None:
        client, tool = self._client()
        chat = client.post(
            "/api/v1/chat", json={"message": "run it", "session_id": "s-2"}
        ).json()
        resume = client.post(
            "/api/v1/chat/resume",
            json={
                "session_id": "s-2",
                "approval_id": chat["approval_id"],
                "decision": "rejected",
                "reason": "no",
            },
        )
        assert resume.status_code == 200
        assert resume.json()["status"] == CHAT_STATUS_COMPLETED
        assert tool.calls == []

    def test_resume_unknown_approval_is_404(self) -> None:
        client, _tool = self._client()
        response = client.post(
            "/api/v1/chat/resume",
            json={
                "session_id": "s-3",
                "approval_id": "approval_missing",
                "decision": "approved",
            },
        )
        assert response.status_code == 404
        document = response.json()
        validate_document("error", document, CONTRACTS)
        assert document["error"]["code"] == "APPROVAL_NOT_FOUND"

    def test_resume_invalid_body_is_400(self) -> None:
        client, _tool = self._client()
        response = client.post(
            "/api/v1/chat/resume", json={"session_id": "s", "decision": "approved"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"

    def test_stub_runtime_cannot_resume(self) -> None:
        response = make_client().post(
            "/api/v1/chat/resume",
            json={
                "session_id": "s",
                "approval_id": "approval_x",
                "decision": "approved",
            },
        )
        assert response.status_code == 400
        assert "does not support resume" in response.json()["error"]["message"]


def _has_external_ref(node: Any) -> bool:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            return True
        return any(_has_external_ref(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_external_ref(item) for item in node)
    return False
