"""run_qa: evaluate a rule pack against a run (Platform verdict)."""

from __future__ import annotations

from agent_backend.platform.client import PlatformClient
from agent_backend.platform.errors import PlatformClientError
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.platform.common import platform_error_result

__all__ = ["RunQaTool"]

_RUN_ID_PATTERN = "^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$"


class RunQaTool:
    """``run_qa`` -> ``POST /api/v1/runs/{run_id}/qa``."""

    def __init__(self, client: PlatformClient) -> None:
        self._client = client

    definition = ToolDefinition(
        name="run_qa",
        description=(
            "Evaluate a QA rule pack against a run's recorded metrics. The "
            "verdict (PASS/FAIL/UNKNOWN) comes from the Platform QA engine."
        ),
        input_schema={
            "type": "object",
            "required": ["run_id", "rule_pack"],
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string", "pattern": _RUN_ID_PATTERN},
                "rule_pack": {
                    "type": "string",
                    "minLength": 1,
                    "description": "QA rule pack path as known to the Platform.",
                },
            },
        },
    )

    def execute(self, call: ToolCall) -> ToolResult:
        try:
            document = self._client.run_qa(
                str(call.arguments["run_id"]), str(call.arguments["rule_pack"])
            )
        except PlatformClientError as exc:
            return platform_error_result(call, exc)
        return ToolResult.success(call, document)
