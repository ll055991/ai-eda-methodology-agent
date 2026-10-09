"""LLMAgentRuntime tests with a FakeModelProvider (no network, no key)."""

from pathlib import Path

from agent_backend.application.chat import (
    PROMPT_VERSION,
    LLMAgentRuntime,
)
from agent_backend.contracts import validate_document
from agent_backend.llm.errors import LLMProviderError
from agent_backend.llm.models import ModelRequest, ModelResponse, ModelUsage
from agent_backend.models.chat import CHAT_STATUS_COMPLETED, CHAT_STATUS_FAILED, ChatRequest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"


class FakeModelProvider:
    """Records requests; returns a fixed answer or raises a fixed error."""

    name = "fake"

    def __init__(self, *, response: ModelResponse | None = None, error: Exception | None = None):
        self.requests: list[ModelRequest] = []
        self._response = response
        self._error = error

    def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def make_runtime(provider: FakeModelProvider) -> LLMAgentRuntime:
    return LLMAgentRuntime(provider=provider, model="fake-model")


class TestHappyPath:
    def test_response_maps_to_chat_response(self) -> None:
        provider = FakeModelProvider(
            response=ModelResponse(
                content="WNS is the worst negative slack.",
                provider="fake",
                model="fake-model",
                usage=ModelUsage(total_tokens=9),
            )
        )
        response = make_runtime(provider).handle(ChatRequest(message="what is WNS?"))

        assert response.status == CHAT_STATUS_COMPLETED
        assert response.message == "WNS is the worst negative slack."
        assert response.session_id
        assert response.run_id is None
        validate_document("agent-chat-response", response.to_dict(), CONTRACTS)

    def test_request_contains_system_prompt_and_user_message(self) -> None:
        provider = FakeModelProvider(
            response=ModelResponse(content="ok", provider="fake")
        )
        make_runtime(provider).handle(ChatRequest(message="hello"))

        sent = provider.requests[0]
        assert sent.model == "fake-model"
        assert [message.role for message in sent.messages] == ["system", "user"]
        assert PROMPT_VERSION == "eda-methodology-assistant/v2"
        assert "Do not invent tools" in sent.messages[0].content
        assert "Do not execute shell commands" in sent.messages[0].content
        assert sent.messages[1].content == "hello"

    def test_temperature_and_max_tokens_are_forwarded(self) -> None:
        provider = FakeModelProvider(
            response=ModelResponse(content="ok", provider="fake")
        )
        runtime = LLMAgentRuntime(
            provider=provider, model="fake-model", temperature=0.1, max_tokens=32
        )
        runtime.handle(ChatRequest(message="hi"))
        assert provider.requests[0].temperature == 0.1
        assert provider.requests[0].max_tokens == 32


class TestFailure:
    def test_provider_error_becomes_failed_status_not_exception(self) -> None:
        provider = FakeModelProvider(error=LLMProviderError("provider HTTP 500"))
        response = make_runtime(provider).handle(ChatRequest(message="hi"))

        assert response.status == CHAT_STATUS_FAILED
        assert "LLMProviderError" in response.message
        assert response.session_id
        validate_document("agent-chat-response", response.to_dict(), CONTRACTS)

    def test_failure_message_contains_no_secret(self) -> None:
        provider = FakeModelProvider(error=LLMProviderError("provider HTTP 500"))
        response = make_runtime(provider).handle(ChatRequest(message="hi"))
        assert "test-key" not in response.message


class TestBoundaries:
    def test_runtime_does_not_hold_a_platform_client(self) -> None:
        runtime = make_runtime(FakeModelProvider(response=ModelResponse("x", "fake")))
        assert not hasattr(runtime, "_platform_client")
        assert not hasattr(runtime, "_platform")
