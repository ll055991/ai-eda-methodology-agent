"""get_run: read one run's statuses, metrics and provenance."""

from __future__ import annotations

from agent_backend.platform.client import PlatformClient
from agent_backend.platform.errors import PlatformClientError
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.platform.common import platform_error_result

__all__ = ["GetRunTool"]

_RUN_ID_PATTERN = "^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$"


class GetRunTool:
    """``get_run`` -> ``GET /api/v1/runs/{run_id}``."""

    def __init__(self, client: PlatformClient) -> None:
        self._client = client

    definition = ToolDefinition(
        name="get_run",
        description=(
            "Read one run: lifecycle/execution/engineering statuses, parsed "
            "metrics, artifacts and diagnostics."
        ),
        input_schema={
            "type": "object",
            "required": ["run_id"],
            "additionalProperties": False,
            "properties": {
                "run_id": {
                    "type": "string",
                    "pattern": _RUN_ID_PATTERN,
                    "description": "Run id returned by run_flow or seen in history.",
                }
            },
        },
    )

    def execute(self, call: ToolCall) -> ToolResult:
        try:
            document = self._client.get_run(str(call.arguments["run_id"]))
        except PlatformClientError as exc:
            return platform_error_result(call, exc)
        return ToolResult.success(call, document)
