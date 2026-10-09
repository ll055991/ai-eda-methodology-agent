"""OpenAI-compatible chat-completions provider (httpx, no vendor SDK).

Talks ``POST {base_url}/chat/completions`` and validates the response
shape honestly: missing usage stays None, empty choices/content is an
error - never a silent empty answer.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from agent_backend.llm.errors import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from agent_backend.llm.models import ChatMessage, ModelRequest, ModelResponse, ModelUsage
from agent_backend.logging import get_logger
from agent_backend.tools.models import ToolCall

__all__ = ["OpenAICompatibleProvider"]

logger = get_logger(__name__)

_PROVIDER_NAME = "openai_compatible"
_ERROR_BODY_CHARS = 500


class OpenAICompatibleProvider:
    """Provider for any OpenAI-compatible ``/chat/completions`` endpoint.

    Args:
        base_url: API base URL, e.g. ``https://api.example.com/v1``.
        api_key: Bearer token (environment only; never logged).
        timeout_s: Hard per-request timeout (never infinite).
        http_client: Optional injected ``httpx.Client`` (tests use
            ``httpx.MockTransport``); the caller then owns its lifecycle.
    """

    name = _PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.Client(timeout=timeout_s)
            self._owns_http = True

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> OpenAICompatibleProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def chat(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [_serialize_message(message) for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [tool.to_openai_schema() for tool in request.tools]

        started = time.monotonic()
        try:
            response = self._http.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"provider timed out after {self._timeout_s}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError(
                f"provider unreachable: {exc.__class__.__name__}"
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        logger.info(
            "llm call: provider=%s model=%s status=%d latency_ms=%d request_id=%s",
            self.name,
            request.model,
            response.status_code,
            latency_ms,
            response.headers.get("x-request-id"),
        )

        if response.status_code >= 400:
            self._raise_provider_error(response)
        return self._parse_response(response)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _raise_provider_error(response: httpx.Response) -> None:
        detail = _bounded_error_detail(response)
        message = f"provider HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        if response.status_code in (401, 403):
            raise LLMAuthenticationError(message)
        if response.status_code == 429:
            raise LLMRateLimitError(message)
        raise LLMProviderError(message)

    @staticmethod
    def _parse_response(response: httpx.Response) -> ModelResponse:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMResponseError("provider returned a non-JSON body") from exc
        if not isinstance(data, dict):
            raise LLMResponseError("provider returned a non-object body")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMResponseError("provider returned no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMResponseError("provider choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMResponseError("provider choice has no message")

        content_raw = message.get("content")
        if content_raw is None:
            content = ""
        elif isinstance(content_raw, str):
            content = content_raw
        else:
            raise LLMResponseError("provider content must be a string or null")

        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        if not content.strip() and not tool_calls:
            raise LLMResponseError("provider returned empty content")

        return ModelResponse(
            content=content,
            provider=_PROVIDER_NAME,
            model=_optional_str(data.get("model")),
            finish_reason=_optional_str(first.get("finish_reason")),
            usage=_parse_usage(data.get("usage")),
            request_id=response.headers.get("x-request-id"),
            tool_calls=tool_calls,
        )


def _serialize_message(message: ChatMessage) -> dict[str, Any]:
    """OpenAI-compatible message, including tool roles/calls when present."""
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.role == "tool":
        payload["tool_call_id"] = message.tool_call_id
        return payload
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _parse_tool_calls(raw: object) -> tuple[ToolCall, ...]:
    """Parse provider tool_calls; malformed structures are response errors."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise LLMResponseError("provider tool_calls must be a list")
    calls: list[ToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise LLMResponseError("provider tool call is not an object")
        call_id = entry.get("id")
        function = entry.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise LLMResponseError("provider tool call is missing its id")
        if not isinstance(function, dict):
            raise LLMResponseError("provider tool call is missing its function")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise LLMResponseError("provider tool call is missing its name")
        arguments_raw = function.get("arguments", "{}")
        if not isinstance(arguments_raw, str):
            raise LLMResponseError(
                "provider tool call arguments must be a JSON string"
            )
        try:
            arguments = json.loads(arguments_raw) if arguments_raw.strip() else {}
        except ValueError as exc:
            raise LLMResponseError(
                "provider tool call arguments are not valid JSON"
            ) from exc
        if not isinstance(arguments, dict):
            raise LLMResponseError(
                "provider tool call arguments must be a JSON object"
            )
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
    return tuple(calls)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_usage(raw: object) -> ModelUsage | None:
    """Usage is optional: absent stays None, partial fields stay None."""
    if not isinstance(raw, dict):
        return None
    return ModelUsage(
        prompt_tokens=_optional_int(raw.get("prompt_tokens")),
        completion_tokens=_optional_int(raw.get("completion_tokens")),
        total_tokens=_optional_int(raw.get("total_tokens")),
    )


def _bounded_error_detail(response: httpx.Response) -> str:
    """Short provider message for humans; never the whole body, never headers."""
    try:
        data = response.json()
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"][:_ERROR_BODY_CHARS]
    except ValueError:
        pass
    text = response.text or ""
    return text[:_ERROR_BODY_CHARS].strip()
