"""run_flow: execute a named flow spec through the Platform API."""

from __future__ import annotations

from typing import Any

from agent_backend.platform.client import PlatformClient
from agent_backend.platform.errors import PlatformClientError
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.platform.common import platform_error_result

__all__ = ["RunFlowTool"]


class RunFlowTool:
    """``run_flow`` -> ``POST /api/v1/flows/run``."""

    def __init__(self, client: PlatformClient) -> None:
        self._client = client

    definition = ToolDefinition(
        name="run_flow",
        description=(
            "Execute a named flow spec through the Platform API as one tracked "
            "run. Returns the run id and its execution/engineering statuses."
        ),
        input_schema={
            "type": "object",
            "required": ["flow"],
            "additionalProperties": False,
            "properties": {
                "flow": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Flow spec path as known to the Platform.",
                },
                "inputs": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Reserved; the Platform rejects overrides today.",
                },
            },
        },
    )

    def execute(self, call: ToolCall) -> ToolResult:
        arguments: dict[str, Any] = call.arguments
        try:
            document = self._client.run_flow(
                str(arguments["flow"]), inputs=arguments.get("inputs")
            )
        except PlatformClientError as exc:
            return platform_error_result(call, exc)
        return ToolResult.success(call, document)
