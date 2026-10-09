"""Tool value types: definition, call, result.

Pure data (no platform/LLM imports) so the LLM layer can reuse
``ToolDefinition``/``ToolCall`` without pulling in the platform client.

``ToolResult.to_message_content`` is the ONLY serialization handed back
to the model: bounded (32 KiB) and honest about truncation - a huge run
archive never reaches the LLM whole.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "MAX_TOOL_RESULT_BYTES",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
]

MAX_TOOL_RESULT_BYTES = 32 * 1024
_MAX_ERROR_CHARS = 1000

_NAME_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


@dataclass(frozen=True)
class ToolDefinition:
    """One registered tool, convertible to an OpenAI-compatible tool schema."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_PATTERN.fullmatch(self.name):
            raise ValueError(f"invalid tool name {self.name!r}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("tool description must be a non-empty string")
        if not isinstance(self.input_schema, dict):
            raise ValueError("input_schema must be a JSON Schema object")

    def to_openai_schema(self) -> dict[str, Any]:
        """OpenAI-compatible ``{"type": "function", ...}`` form."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    """One structured tool call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("tool call id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("tool call name must be a non-empty string")
        if not isinstance(self.arguments, dict):
            raise ValueError("tool call arguments must be an object")


@dataclass(frozen=True)
class ToolResult:
    """Structured tool outcome - errors never crash the agent loop."""

    tool_call_id: str
    tool_name: str
    status: Literal["success", "error"]
    data: Any | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("success", "error"):
            raise ValueError(f"invalid tool status {self.status!r}")
        if self.status == "error" and not self.error_code:
            raise ValueError("error results require an error_code")
        if self.status == "success" and self.error_code is not None:
            raise ValueError("success results must not carry an error_code")

    @classmethod
    def success(cls, call: ToolCall, data: Any | None = None) -> ToolResult:
        return cls(
            tool_call_id=call.id, tool_name=call.name, status="success", data=data
        )

    @classmethod
    def error(cls, call: ToolCall, code: str, message: str) -> ToolResult:
        return cls(
            tool_call_id=call.id,
            tool_name=call.name,
            status="error",
            error_code=code,
            error_message=message[:_MAX_ERROR_CHARS],
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (None fields omitted)."""
        document: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "status": self.status,
        }
        if self.data is not None:
            document["data"] = self.data
        if self.error_code is not None:
            document["error_code"] = self.error_code
        if self.error_message is not None:
            document["error_message"] = self.error_message
        return document

    def to_message_content(self, max_bytes: int = MAX_TOOL_RESULT_BYTES) -> str:
        """Bounded JSON text for the ``tool`` chat message.

        Oversized results are truncated to a preview and flagged with
        ``truncated: true`` so the model knows evidence was cut.
        """
        document = self.to_dict()
        text = json.dumps(document, ensure_ascii=False)
        if len(text.encode("utf-8")) <= max_bytes:
            return text

        data_text = json.dumps(document.get("data"), ensure_ascii=False)
        document["data"] = None
        document["truncated"] = True
        base = json.dumps(document, ensure_ascii=False)
        budget = max_bytes - len(base.encode("utf-8")) - 64
        document["data_preview"] = data_text[: max(0, budget)] if budget > 0 else ""
        text = json.dumps(document, ensure_ascii=False)
        if len(text.encode("utf-8")) > max_bytes:  # multibyte safety net
            document["data_preview"] = ""
            text = json.dumps(document, ensure_ascii=False)
        return text
