"""McpToolService unit tests (M7.2).

The tool layer is where a Platform answer becomes a structured MCP result:
success documents pass through untouched, and every failure keeps a stable,
switchable code. No judgment is added anywhere.
"""

import logging

import httpx
import pytest

from aemp.mcp import tools as tools_module
from aemp.mcp.client import PlatformHttpClient
from aemp.mcp.tools import (
    ERROR_PLATFORM_RESPONSE,
    ERROR_PLATFORM_UNAVAILABLE,
    TOOL_NAMES,
    McpToolService,
)


def _service(handler, **kwargs) -> McpToolService:
    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://platform.test"
    )
    return McpToolService(
        PlatformHttpClient("http://platform.test", http_client=http), **kwargs
    )


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def captured() -> _Capture:
    handler = _Capture()
    tools_module.logger.addHandler(handler)
    tools_module.logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        tools_module.logger.removeHandler(handler)


def test_exposed_tools_are_exactly_the_contract_tools() -> None:
    assert TOOL_NAMES == ("run_flow", "get_run", "run_qa", "get_artifacts")


def test_success_passes_the_platform_document_through() -> None:
    document = {
        "run_id": "20260101T000000Z-abcdef",
        "execution_status": "success",
        "engineering_status": "unknown",
    }
    service = _service(lambda _request: httpx.Response(200, json=document))

    outcome = service.get_run("20260101T000000Z-abcdef")

    assert outcome.ok is True
    assert outcome.name == "get_run"
    assert outcome.to_document() == document


def test_run_flow_success_reports_both_statuses() -> None:
    document = {
        "run_id": "20260101T000000Z-abcdef",
        "status": "success",
        "execution_status": "success",
        "engineering_status": "unknown",
    }
    service = _service(lambda _request: httpx.Response(200, json=document))

    outcome = service.run_flow("configs/flows/rtl_to_sta.yaml")

    assert outcome.ok is True
    assert outcome.data["execution_status"] == "success"
    assert outcome.data["engineering_status"] == "unknown"


def test_qa_verdict_is_the_platform_verdict() -> None:
    document = {"run_id": "r", "status": "FAIL", "summary": {"failed": 1}}
    service = _service(lambda _request: httpx.Response(200, json=document))

    outcome = service.run_qa("20260101T000000Z-abcdef", "configs/rules/base_timing.yaml")

    assert outcome.ok is True
    assert outcome.data["status"] == "FAIL"


def test_artifact_listing_is_metadata_only() -> None:
    document = {
        "run_id": "r",
        "artifacts": [
            {
                "artifact_id": "artifacts/timing.rpt",
                "role": "sta.report",
                "exists": True,
                "bytes": 12,
                "sha256": "0" * 64,
            }
        ],
    }
    service = _service(lambda _request: httpx.Response(200, json=document))

    outcome = service.get_artifacts("20260101T000000Z-abcdef")

    assert outcome.ok is True
    assert "content" not in str(outcome.data)  # never file bodies


def test_platform_error_keeps_the_platform_code() -> None:
    service = _service(
        lambda _request: httpx.Response(
            503,
            json={
                "error": {
                    "code": "TOOL_ENVIRONMENT_UNAVAILABLE",
                    "message": "Docker daemon is unreachable",
                    "details": {},
                }
            },
        )
    )

    outcome = service.run_flow("f.yaml")

    assert outcome.ok is False
    assert outcome.data["error"]["code"] == "TOOL_ENVIRONMENT_UNAVAILABLE"
    assert outcome.data["error"]["message"] == "Docker daemon is unreachable"


def test_run_not_found_is_not_a_crash() -> None:
    service = _service(
        lambda _request: httpx.Response(
            404,
            json={
                "error": {"code": "RUN_NOT_FOUND", "message": "Run not found: x", "details": {}},
            },
        )
    )

    outcome = service.get_run("20260101T000000Z-abcdef")

    assert outcome.ok is False
    assert outcome.data["error"]["code"] == "RUN_NOT_FOUND"


def test_transport_failure_uses_the_adapter_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    outcome = _service(handler).get_run("20260101T000000Z-abcdef")

    assert outcome.ok is False
    assert outcome.data["error"]["code"] == ERROR_PLATFORM_UNAVAILABLE


def test_unusable_response_uses_the_adapter_code() -> None:
    outcome = _service(lambda _request: httpx.Response(200, text="nope")).get_run(
        "20260101T000000Z-abcdef"
    )

    assert outcome.ok is False
    assert outcome.data["error"]["code"] == ERROR_PLATFORM_RESPONSE
