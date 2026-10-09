"""PlatformHttpClient unit tests (M7.2).

The MCP server's only capability source is the Platform HTTP contract, so
these tests pin the mapping (endpoint, body, error document) that the MCP
tools rely on - with ``httpx.MockTransport``, no sockets involved.
"""

import json
from typing import Any

import httpx
import pytest

from aemp.mcp.client import PlatformHttpClient
from aemp.mcp.errors import (
    PlatformApiError,
    PlatformResponseError,
    PlatformUnavailableError,
)


def _client(handler: Any) -> PlatformHttpClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="http://platform.test")
    return PlatformHttpClient("http://platform.test", http_client=http)


def test_run_flow_posts_the_contract_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"run_id": "20260101T000000Z-abcdef"})

    document = _client(handler).run_flow("configs/flows/rtl_to_sta.yaml")

    assert document["run_id"] == "20260101T000000Z-abcdef"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/flows/run"
    assert seen["body"] == {"flow": "configs/flows/rtl_to_sta.yaml"}


def test_run_flow_forwards_inputs_only_when_present() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": "r"})

    client = _client(handler)
    client.run_flow("f.yaml")
    client.run_flow("f.yaml", inputs={"rtl": "x.v"})

    assert bodies[0] == {"flow": "f.yaml"}
    assert bodies[1] == {"flow": "f.yaml", "inputs": {"rtl": "x.v"}}


@pytest.mark.parametrize(
    ("call", "method", "path", "body"),
    [
        (
            lambda client: client.get_run("20260101T000000Z-abcdef"),
            "GET",
            "/api/v1/runs/20260101T000000Z-abcdef",
            None,
        ),
        (
            lambda client: client.run_qa("20260101T000000Z-abcdef", "rules.yaml"),
            "POST",
            "/api/v1/runs/20260101T000000Z-abcdef/qa",
            {"rule_pack": "rules.yaml"},
        ),
        (
            lambda client: client.list_artifacts("20260101T000000Z-abcdef"),
            "GET",
            "/api/v1/runs/20260101T000000Z-abcdef/artifacts",
            None,
        ),
    ],
)
def test_endpoint_mapping(call: Any, method: str, path: str, body: Any) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(200, json={"ok": True})

    assert call(_client(handler)) == {"ok": True}
    assert seen == {"method": method, "path": path, "body": body}


def test_platform_error_document_is_preserved() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error": {
                    "code": "TOOL_ENVIRONMENT_UNAVAILABLE",
                    "message": "Docker daemon is unreachable",
                    "details": {"tool": "docker"},
                }
            },
        )

    with pytest.raises(PlatformApiError) as excinfo:
        _client(handler).run_flow("f.yaml")

    error = excinfo.value
    assert error.code == "TOOL_ENVIRONMENT_UNAVAILABLE"
    assert error.http_status == 503
    assert error.details == {"tool": "docker"}
    assert error.to_document()["error"]["code"] == "TOOL_ENVIRONMENT_UNAVAILABLE"


def test_invalid_error_document_is_a_response_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>boom</html>")

    with pytest.raises(PlatformResponseError):
        _client(handler).get_run("20260101T000000Z-abcdef")


def test_non_json_success_is_a_response_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(PlatformResponseError):
        _client(handler).get_run("20260101T000000Z-abcdef")


def test_non_object_success_is_a_response_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(PlatformResponseError):
        _client(handler).get_run("20260101T000000Z-abcdef")


def test_transport_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PlatformUnavailableError):
        _client(handler).run_flow("f.yaml")


def test_injected_client_is_not_closed_by_us() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://platform.test"
    )
    client = PlatformHttpClient("http://platform.test", http_client=http)
    client.close()
    assert not http.is_closed  # the caller owns an injected client
    http.close()
