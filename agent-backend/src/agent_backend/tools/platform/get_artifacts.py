"""get_artifacts: list the artifacts recorded by a run."""

from __future__ import annotations

from agent_backend.platform.client import PlatformClient
from agent_backend.platform.errors import PlatformClientError
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.platform.common import platform_error_result

__all__ = ["GetArtifactsTool"]

_RUN_ID_PATTERN = "^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$"


class GetArtifactsTool:
    """``get_artifacts`` -> ``GET /api/v1/runs/{run_id}/artifacts``."""

    def __init__(self, client: PlatformClient) -> None:
        self._client = client

    definition = ToolDefinition(
        name="get_artifacts",
        description=(
            "List the artifacts recorded by a run (roles, paths, sizes and "
            "SHA-256 digests) - metadata only."
        ),
        input_schema={
            "type": "object",
            "required": ["run_id"],
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string", "pattern": _RUN_ID_PATTERN}
            },
        },
    )

    def execute(self, call: ToolCall) -> ToolResult:
        try:
            document = self._client.list_artifacts(str(call.arguments["run_id"]))
        except PlatformClientError as exc:
            return platform_error_result(call, exc)
        return ToolResult.success(call, document)
