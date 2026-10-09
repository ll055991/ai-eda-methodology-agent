"""OpenAICompatibleProvider tests over a fake HTTP transport.

No network, no real API key: the transport is ``httpx.MockTransport``
and the key is the literal ``test-key``.
"""

import json
from typing import Any

import httpx
import pytest

from agent_backend.llm.errors import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from agent_backend.llm.models import ChatMessage, ModelRequest
from agent_backend.llm.openai_compatible import OpenAICompatibleProvider
from agent_backend.tools.models import ToolCall, ToolDefinition

BASE_URL = "http://llm.test/v1"


def make_provider(handler) -> OpenAICompatibleProvider:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(
        base_url=BASE_URL, api_key="test-key", timeout_s=12.5, http_client=http
    )


def request(**overrides: Any) -> ModelRequest:
    defaults: dict[str, Any] = {
        "model": "fake-model",
        "messages": (
            ChatMessage("system", "be nice"),
            ChatMessage("user", "hello"),
        ),
    }
    defaults.update(overrides)
    return ModelRequest(**defaults)


def completion(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "fake-model",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hi there"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    payload.update(overrides)
    return payload


class TestRequestShape:
    def test_request_url_method_headers_body(self) -> None:
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["method"] = req.method
            seen["url"] = str(req.url)
            seen["auth"] = req.headers.get("Authorization")
            seen["body"] = json.loads(req.read())
            return httpx.Response(200, json=completion())

        provider = make_provider(handler)
        provider.chat(request(temperature=0.2, max_tokens=64))

        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE_URL}/chat/completions"
        assert seen["auth"] == "Bearer test-key"
        assert seen["body"]["model"] == "fake-model"
        assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
        assert seen["body"]["temperature"] == 0.2
        assert seen["body"]["max_tokens"] == 64

    def test_optional_params_omitted_when_unset(self) -> None:
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(req.read())
            return httpx.Response(200, json=completion())

        make_provider(handler).chat(request())
        assert "temperature" not in seen["body"]
        assert "max_tokens" not in seen["body"]


class TestSuccess:
    def test_full_response_parsed(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=completion(), headers={"x-request-id": "req-1"}
            )

        response = make_provider(handler).chat(request())
        assert response.content == "hi there"
        assert response.provider == "openai_compatible"
        assert response.model == "fake-model"
        assert response.finish_reason == "stop"
        assert response.request_id == "req-1"
        assert response.usage is not None
        assert response.usage.total_tokens == 15

    def test_missing_usage_is_none_not_zero(self) -> None:
        payload = completion()
        del payload["usage"]
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        assert provider.chat(request()).usage is None

    def test_partial_usage_fields_stay_none(self) -> None:
        payload = completion(usage={"prompt_tokens": 7})
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        usage = provider.chat(request()).usage
        assert usage is not None
        assert usage.prompt_tokens == 7
        assert usage.completion_tokens is None
        assert usage.total_tokens is None

    def test_missing_model_and_finish_reason_stay_none(self) -> None:
        payload = completion()
        del payload["model"]
        del payload["choices"][0]["finish_reason"]
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        response = provider.chat(request())
        assert response.model is None
        assert response.finish_reason is None


class TestProviderErrors:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors(self, status: int) -> None:
        provider = make_provider(
            lambda req: httpx.Response(status, json={"error": {"message": "denied"}})
        )
        with pytest.raises(LLMAuthenticationError, match="denied"):
            provider.chat(request())

    def test_rate_limit(self) -> None:
        provider = make_provider(lambda req: httpx.Response(429, text="slow down"))
        with pytest.raises(LLMRateLimitError, match="429"):
            provider.chat(request())

    @pytest.mark.parametrize("status", [400, 500, 502])
    def test_other_provider_errors(self, status: int) -> None:
        provider = make_provider(lambda req: httpx.Response(status, text="boom"))
        with pytest.raises(LLMProviderError, match=str(status)):
            provider.chat(request())


class TestTransportErrors:
    def test_timeout(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(LLMTimeoutError):
            make_provider(handler).chat(request())

    def test_connection_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(LLMConnectionError):
            make_provider(handler).chat(request())


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="list_runs",
        description="List runs.",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"limit": {"type": "integer"}},
        },
    )


def tool_call_response(arguments: str = '{"limit": 10}') -> dict[str, Any]:
    return {
        "model": "fake-model",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "list_runs", "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }


class TestToolCallingRequest:
    def test_tools_payload_sent(self) -> None:
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(req.read())
            return httpx.Response(200, json=completion())

        make_provider(handler).chat(request(tools=(tool_definition(),)))
        payload = seen["body"]["tools"][0]
        assert payload["type"] == "function"
        assert payload["function"]["name"] == "list_runs"
        assert payload["function"]["parameters"]["additionalProperties"] is False

    def test_no_tools_key_when_empty(self) -> None:
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(req.read())
            return httpx.Response(200, json=completion())

        make_provider(handler).chat(request())
        assert "tools" not in seen["body"]

    def test_assistant_tool_calls_and_tool_message_serialized(self) -> None:
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(req.read())
            return httpx.Response(200, json=completion())

        messages = (
            ChatMessage("user", "go"),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCall(id="c1", name="list_runs", arguments={"limit": 5}),),
            ),
            ChatMessage(role="tool", content='{"ok": true}', tool_call_id="c1"),
        )
        make_provider(handler).chat(request(messages=messages))

        sent = seen["body"]["messages"]
        assert sent[1]["tool_calls"][0]["function"]["name"] == "list_runs"
        assert json.loads(sent[1]["tool_calls"][0]["function"]["arguments"]) == {
            "limit": 5
        }
        assert sent[2] == {
            "role": "tool",
            "content": '{"ok": true}',
            "tool_call_id": "c1",
        }


class TestToolCallingResponse:
    def test_tool_call_response_parsed(self) -> None:
        provider = make_provider(
            lambda req: httpx.Response(200, json=tool_call_response())
        )
        response = provider.chat(request(tools=(tool_definition(),)))
        assert response.content == ""
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert call.id == "call_123"
        assert call.name == "list_runs"
        assert call.arguments == {"limit": 10}

    def test_multiple_tool_calls_parsed_in_order(self) -> None:
        payload = tool_call_response()
        payload["choices"][0]["message"]["tool_calls"].append(
            {
                "id": "call_124",
                "type": "function",
                "function": {"name": "get_run", "arguments": '{"run_id": "r"}'},
            }
        )
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        response = provider.chat(request())
        assert [call.id for call in response.tool_calls] == ["call_123", "call_124"]
        assert response.tool_calls[1].name == "get_run"

    def test_malformed_arguments_json(self) -> None:
        provider = make_provider(
            lambda req: httpx.Response(200, json=tool_call_response("{not json"))
        )
        with pytest.raises(LLMResponseError, match="not valid JSON"):
            provider.chat(request())

    def test_arguments_must_be_an_object(self) -> None:
        provider = make_provider(
            lambda req: httpx.Response(200, json=tool_call_response("[1, 2]"))
        )
        with pytest.raises(LLMResponseError, match="JSON object"):
            provider.chat(request())

    def test_tool_calls_must_be_a_list(self) -> None:
        payload = tool_call_response()
        payload["choices"][0]["message"]["tool_calls"] = {"id": "x"}
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        with pytest.raises(LLMResponseError, match="must be a list"):
            provider.chat(request())

    def test_missing_tool_call_id(self) -> None:
        payload = tool_call_response()
        del payload["choices"][0]["message"]["tool_calls"][0]["id"]
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        with pytest.raises(LLMResponseError, match="missing its id"):
            provider.chat(request())

    def test_missing_tool_call_name(self) -> None:
        payload = tool_call_response()
        del payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"]
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        with pytest.raises(LLMResponseError, match="missing its name"):
            provider.chat(request())


class TestResponseValidation:
    def test_non_json_body(self) -> None:
        provider = make_provider(lambda req: httpx.Response(200, content=b"nope"))
        with pytest.raises(LLMResponseError, match="non-JSON"):
            provider.chat(request())

    def test_missing_choices(self) -> None:
        provider = make_provider(lambda req: httpx.Response(200, json={"model": "m"}))
        with pytest.raises(LLMResponseError, match="no choices"):
            provider.chat(request())

    def test_empty_choices(self) -> None:
        provider = make_provider(
            lambda req: httpx.Response(200, json=completion(choices=[]))
        )
        with pytest.raises(LLMResponseError, match="no choices"):
            provider.chat(request())

    def test_choice_without_message(self) -> None:
        provider = make_provider(
            lambda req: httpx.Response(200, json=completion(choices=[{}]))
        )
        with pytest.raises(LLMResponseError, match="no message"):
            provider.chat(request())

    @pytest.mark.parametrize("content", ["", "   "])
    def test_empty_content_is_an_error(self, content: str) -> None:
        payload = completion()
        payload["choices"][0]["message"]["content"] = content
        provider = make_provider(lambda req: httpx.Response(200, json=payload))
        with pytest.raises(LLMResponseError, match="empty content"):
            provider.chat(request())
