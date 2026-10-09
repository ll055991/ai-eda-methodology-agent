"""PlatformClient contract tests (mock HTTP transport, no network).

Every response is validated against the SHARED schemas in ../contracts;
errors are asserted to stay in their family (API / connection / contract).
"""

from pathlib import Path

import httpx
import pytest

from agent_backend.contracts import validate_document
from agent_backend.platform import (
    PlatformApiError,
    PlatformClient,
    PlatformConnectionError,
    PlatformContractError,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
RUN_ID = "20260101T000000Z-abcdef"

RUN_LIST = {"runs": [], "limit": 20, "offset": 0}
RUN_SUMMARY = {
    "run_id": RUN_ID,
    "status": "success",
    "execution_status": "success",
    "engineering_status": "unknown",
}
RUN_DETAIL = {
    "run_id": RUN_ID,
    "operation": "opensta.timing",
    "step_id": "sta",
    "lifecycle_status": "success",
    "execution_status": "success",
    "engineering_status": "unknown",
    "tool": {"name": "opensta", "version": "unknown"},
    "metrics": {"sta.wns_ns": -0.01},
    "inputs": [],
    "artifacts": [],
    "diagnostics": [],
    "provenance": {"created_at": "2026-01-01T00:00:00Z"},
}
QA_DOC = {
    "run_id": RUN_ID,
    "status": "FAIL",
    "rule_pack": {"name": "base_timing", "version": 1},
    "summary": {"total_rules": 1, "passed": 0, "failed": 1, "unknown": 0, "warnings": 0},
    "findings": [
        {
            "rule_id": "timing.wns.non_negative",
            "metric": "sta.wns_ns",
            "check": "ge",
            "threshold": 0.0,
            "severity": "error",
            "status": "fail",
            "expected": ">= 0.0",
            "actual": -0.01,
            "message": "violated",
        }
    ],
}
ARTIFACT = {
    "artifact_id": "artifacts/timing.rpt",
    "role": "sta.report",
    "path": "artifacts/timing.rpt",
    "exists": True,
    "bytes": 10,
    "sha256": "a" * 64,
}
ARTIFACT_LIST = {"run_id": RUN_ID, "artifacts": [ARTIFACT]}


def error_doc(code: str) -> dict:
    return {"error": {"code": code, "message": f"{code} happened", "details": {}}}


def make_client(handler) -> PlatformClient:
    http = httpx.Client(
        base_url="http://platform.test", transport=httpx.MockTransport(handler)
    )
    return PlatformClient("http://platform.test", contracts_dir=CONTRACTS, http_client=http)


class TestSuccessPaths:
    def test_list_runs_returns_validated_document(self) -> None:
        client = make_client(lambda request: httpx.Response(200, json=RUN_LIST))
        assert client.list_runs() == RUN_LIST

    def test_get_run_returns_validated_document(self) -> None:
        client = make_client(lambda request: httpx.Response(200, json=RUN_DETAIL))
        document = client.get_run(RUN_ID)
        assert document == RUN_DETAIL
        assert document["execution_status"] == "success"
        assert document["engineering_status"] == "unknown"

    def test_run_flow_sends_contract_body(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = request.read().decode()
            return httpx.Response(200, json=RUN_SUMMARY)

        client = make_client(handler)
        document = client.run_flow("configs/flows/rtl_to_sta.yaml", inputs={"rtl": "a.v"})
        assert document == RUN_SUMMARY
        assert seen["path"] == "/api/v1/flows/run"
        assert '"flow"' in seen["body"] and '"inputs"' in seen["body"]

    def test_run_qa_returns_platform_verdict_unchanged(self) -> None:
        # the client must NOT reinterpret FAIL - verdicts are Platform facts
        client = make_client(lambda request: httpx.Response(200, json=QA_DOC))
        document = client.run_qa(RUN_ID, "configs/rules/base_timing.yaml")
        assert document == QA_DOC
        assert document["status"] == "FAIL"

    def test_artifact_endpoints(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/artifacts"):
                return httpx.Response(200, json=ARTIFACT_LIST)
            return httpx.Response(200, json=ARTIFACT)

        client = make_client(handler)
        assert client.list_artifacts(RUN_ID) == ARTIFACT_LIST
        assert client.get_artifact(RUN_ID, "artifacts/timing.rpt") == ARTIFACT


class TestBusinessErrors:
    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (400, "INVALID_REQUEST"),
            (404, "RUN_NOT_FOUND"),
            (422, "FLOW_VALIDATION_FAILED"),
            (503, "TOOL_ENVIRONMENT_UNAVAILABLE"),
            (500, "INTERNAL_ERROR"),
        ],
    )
    def test_error_document_becomes_platform_api_error(
        self, status: int, code: str
    ) -> None:
        client = make_client(lambda request: httpx.Response(status, json=error_doc(code)))
        with pytest.raises(PlatformApiError) as excinfo:
            client.get_run(RUN_ID)
        error = excinfo.value
        assert error.code == code
        assert error.http_status == status
        assert error.details == {}
        assert code in str(error)

    def test_invalid_error_code_is_a_contract_error(self) -> None:
        # an error document outside the contract enum must not be trusted
        client = make_client(
            lambda request: httpx.Response(404, json=error_doc("NOT_A_REAL_CODE"))
        )
        with pytest.raises(PlatformContractError):
            client.get_run(RUN_ID)


class TestTransportErrors:
    def test_connection_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = make_client(handler)
        with pytest.raises(PlatformConnectionError):
            client.list_runs()

    def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        client = make_client(handler)
        with pytest.raises(PlatformConnectionError):
            client.list_runs()

    def test_connection_error_is_not_an_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = make_client(handler)
        with pytest.raises(PlatformConnectionError) as excinfo:
            client.list_runs()
        assert not isinstance(excinfo.value, PlatformApiError)


class TestContractErrors:
    def test_non_json_body(self) -> None:
        client = make_client(
            lambda request: httpx.Response(200, content=b"<html>not json</html>")
        )
        with pytest.raises(PlatformContractError):
            client.list_runs()

    def test_missing_required_field(self) -> None:
        client = make_client(lambda request: httpx.Response(200, json={"runs": []}))
        with pytest.raises(PlatformContractError):
            client.list_runs()

    def test_unexpected_field_rejected(self) -> None:
        document = dict(RUN_LIST, unexpected_field=True)
        client = make_client(lambda request: httpx.Response(200, json=document))
        with pytest.raises(PlatformContractError):
            client.list_runs()

    def test_error_without_error_object(self) -> None:
        client = make_client(lambda request: httpx.Response(404, json={"detail": "no"}))
        with pytest.raises(PlatformContractError):
            client.get_run(RUN_ID)


class TestSharedContract:
    def test_schemas_are_shared_not_copied(self) -> None:
        # the client validates against the repository contracts directory
        validate_document("run-list", RUN_LIST, CONTRACTS)
        assert not (Path(__file__).resolve().parents[2] / "contracts" / "schemas").exists()
