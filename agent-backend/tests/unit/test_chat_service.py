"""ChatService / StubAgentRuntime tests (M6.2: no LLM)."""

from pathlib import Path

from agent_backend.application.chat import (
    STUB_MESSAGE,
    AgentRuntime,
    ChatService,
    StubAgentRuntime,
)
from agent_backend.contracts import validate_document
from agent_backend.models.chat import (
    CHAT_STATUS_COMPLETED,
    ChatRequest,
    ChatResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"


class TestStubRuntime:
    def test_stub_response_matches_agent_contract(self) -> None:
        response = ChatService().handle(ChatRequest(message="check the last run"))
        document = response.to_dict()
        validate_document("agent-chat-response", document, CONTRACTS)
        assert response.status == CHAT_STATUS_COMPLETED
        assert response.message == STUB_MESSAGE
        assert response.run_id is None
        assert response.approval_required is False

    def test_client_session_id_is_echoed(self) -> None:
        response = ChatService().handle(ChatRequest(message="hi", session_id="s-42"))
        assert response.session_id == "s-42"

    def test_server_generates_session_id_when_absent(self) -> None:
        response = ChatService().handle(ChatRequest(message="hi"))
        assert response.session_id
        assert len(response.session_id) >= 8

    def test_stub_runtime_does_not_touch_the_platform(self) -> None:
        # StubAgentRuntime has no PlatformClient attribute at all
        runtime = StubAgentRuntime()
        assert not hasattr(runtime, "_platform")
        assert not hasattr(runtime, "client")


class TestRuntimeSeam:
    def test_custom_runtime_is_used(self) -> None:
        class FixedRuntime:
            def handle(self, request: ChatRequest) -> ChatResponse:
                return ChatResponse(
                    message=f"echo: {request.message}",
                    status=CHAT_STATUS_COMPLETED,
                    session_id=request.session_id,
                )

        runtime: AgentRuntime = FixedRuntime()
        response = ChatService(runtime=runtime).handle(ChatRequest(message="hello"))
        assert response.message == "echo: hello"
