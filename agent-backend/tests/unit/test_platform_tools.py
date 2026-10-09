"""Platform tool tests with a FakePlatformClient.

Every tool must go Tool -> PlatformClient (never filesystem, never SQL).
"""

from typing import Any

from agent_backend.platform.errors import (
    PlatformApiError,
    PlatformConnectionError,
    PlatformContractError,
)
from agent_backend.tools.models import ToolCall
from agent_backend.tools.platform import (
    GetArtifactsTool,
    GetRunTool,
    RunFlowTool,
    RunQaTool,
    build_platform_registry,
)

RUN_ID = "20260101T000000Z-abcdef"


class FakePlatformClient:
    """Records calls; returns canned documents or raises a canned error."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._error = error

    def _record(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((name, args, kwargs))
        if self._error is not None:
            raise self._error
        return {"ok": True}

    def run_flow(self, flow: str, *, inputs: dict | None = None) -> dict[str, Any]:
        return self._record("run_flow", flow, inputs=inputs)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._record("get_run", run_id)

    def run_qa(self, run_id: str, rule_pack: str) -> dict[str, Any]:
        return self._record("run_qa", run_id, rule_pack)

    def list_artifacts(self, run_id: str) -> dict[str, Any]:
        return self._record("list_artifacts", run_id)


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


class TestToolsDelegateToPlatformClient:
    def test_run_flow(self) -> None:
        client = FakePlatformClient()
        result = RunFlowTool(client).execute(
            call("run_flow", flow="configs/flows/rtl_to_sta.yaml")
        )
        assert result.status == "success"
        assert client.calls == [
            ("run_flow", ("configs/flows/rtl_to_sta.yaml",), {"inputs": None})
        ]

    def test_get_run(self) -> None:
        client = FakePlatformClient()
        result = GetRunTool(client).execute(call("get_run", run_id=RUN_ID))
        assert result.status == "success"
        assert client.calls == [("get_run", (RUN_ID,), {})]

    def test_run_qa(self) -> None:
        client = FakePlatformClient()
        result = RunQaTool(client).execute(
            call("run_qa", run_id=RUN_ID, rule_pack="configs/rules/base_timing.yaml")
        )
        assert result.status == "success"
        assert client.calls == [
            ("run_qa", (RUN_ID, "configs/rules/base_timing.yaml"), {})
        ]

    def test_get_artifacts(self) -> None:
        client = FakePlatformClient()
        result = GetArtifactsTool(client).execute(call("get_artifacts", run_id=RUN_ID))
        assert result.status == "success"
        assert client.calls == [("list_artifacts", (RUN_ID,), {})]


class TestPlatformErrorMapping:
    def test_api_error(self) -> None:
        client = FakePlatformClient(
            error=PlatformApiError(
                "RUN_NOT_FOUND", "Run not found", details={}, http_status=404
            )
        )
        result = GetRunTool(client).execute(call("get_run", run_id=RUN_ID))
        assert result.status == "error"
        assert result.error_code == "PLATFORM_API_ERROR"
        assert "RUN_NOT_FOUND" in (result.error_message or "")

    def test_connection_error(self) -> None:
        client = FakePlatformClient(error=PlatformConnectionError("refused"))
        result = GetRunTool(client).execute(call("get_run", run_id=RUN_ID))
        assert result.error_code == "PLATFORM_CONNECTION_ERROR"
        assert result.error_message == "Platform service is unavailable."

    def test_contract_error(self) -> None:
        client = FakePlatformClient(error=PlatformContractError("bad shape"))
        result = GetRunTool(client).execute(call("get_run", run_id=RUN_ID))
        assert result.error_code == "PLATFORM_CONTRACT_ERROR"


class TestToolContractAlignment:
    def test_registry_has_exactly_the_contract_tools(self) -> None:
        registry = build_platform_registry(FakePlatformClient())
        assert {definition.name for definition in registry.definitions()} == {
            "run_flow",
            "get_run",
            "run_qa",
            "get_artifacts",
        }

    def test_schemas_are_closed_and_typed(self) -> None:
        for tool in (
            RunFlowTool(FakePlatformClient()),
            GetRunTool(FakePlatformClient()),
            RunQaTool(FakePlatformClient()),
            GetArtifactsTool(FakePlatformClient()),
        ):
            schema = tool.definition.input_schema
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False
            assert schema["required"]

    def test_definitions_convert_to_openai_schema(self) -> None:
        definition = GetRunTool(FakePlatformClient()).definition
        schema = definition.to_openai_schema()
        assert schema["function"]["name"] == "get_run"
        assert "run_id" in schema["function"]["parameters"]["properties"]
