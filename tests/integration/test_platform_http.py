"""HTTP contract tests for the Platform Backend (M6.1).

No Docker required: runs are created through the real RunManager in a
temporary project and the HTTP app is exercised in-process with
TestClient. Real EDA execution remains in the Docker-marked integration
tests (skipped without Docker).

Every response is validated against ``contracts/schemas/*.json`` - the
contract, not the implementation, is the source of truth.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from aemp.api import PlatformApi
from aemp.api.http import create_app
from aemp.api.schemas import validate_contract
from aemp.core.config import Settings
from aemp.runs.manager import Run, RunManager

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"
RULES = REPO_ROOT / "configs" / "rules" / "base_timing.yaml"
FLOW_SPEC = REPO_ROOT / "configs" / "flows" / "rtl_to_sta.yaml"


@pytest.fixture
def platform(tmp_path: Path):
    settings = Settings(project_root=tmp_path)
    api = PlatformApi(settings, contracts_dir=CONTRACTS)
    app = create_app(api=api, contracts_dir=CONTRACTS)
    return TestClient(app), api


def make_run(manager: RunManager, metrics: dict[str, float], *, artifact: str | None = None) -> Run:
    run = manager.create_run("opensta.timing", tool_name="opensta")
    run.record_step("sta")
    run.mark_running()
    run.record_metrics(metrics)
    if artifact is not None:
        path = run.run_dir / "artifacts" / artifact
        path.write_text("worst slack max 0.29\n", encoding="utf-8")
        run.add_artifact(path, role="sta.report")
    run.finalize("success", engineering_status="unknown")
    return run


class TestHealth:
    def test_healthz_ok_without_eda(self, platform) -> None:
        client, _api = platform
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["X-Request-ID"]  # server-generated

    def test_request_id_echoed(self, platform) -> None:
        client, _api = platform
        response = client.get("/healthz", headers={"X-Request-ID": "test-rid-1"})
        assert response.headers["X-Request-ID"] == "test-rid-1"


class TestFlowRun:
    def test_unknown_field_rejected(self, platform) -> None:
        client, _api = platform
        response = client.post("/api/v1/flows/run", json={"nope": True})
        assert response.status_code == 400
        document = response.json()
        validate_contract("error", document, CONTRACTS)
        assert document["error"]["code"] == "INVALID_REQUEST"

    def test_missing_flow_rejected(self, platform) -> None:
        client, _api = platform
        response = client.post("/api/v1/flows/run", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"

    def test_inputs_override_rejected_not_ignored(self, platform) -> None:
        client, _api = platform
        response = client.post(
            "/api/v1/flows/run",
            json={"flow": str(FLOW_SPEC), "inputs": {"rtl": "x.v"}},
        )
        assert response.status_code == 400
        document = response.json()
        assert document["error"]["code"] == "INVALID_REQUEST"
        assert "inputs" in document["error"]["message"]

    def test_flow_not_found(self, platform) -> None:
        client, _api = platform
        response = client.post("/api/v1/flows/run", json={"flow": "does/not/exist.yaml"})
        assert response.status_code == 404
        document = response.json()
        validate_contract("error", document, CONTRACTS)
        assert document["error"]["code"] == "FLOW_NOT_FOUND"


class TestRuns:
    def test_list_and_detail_contract(self, platform) -> None:
        client, api = platform
        run = make_run(api.manager, {"sta.wns_ns": 0.29})

        page = client.get("/api/v1/runs")
        assert page.status_code == 200
        validate_contract("run-list", page.json(), CONTRACTS)
        assert [row["run_id"] for row in page.json()["runs"]] == [run.run_id]

        detail = client.get(f"/api/v1/runs/{run.run_id}")
        assert detail.status_code == 200
        document = detail.json()
        validate_contract("run", document, CONTRACTS)
        # dual-state survives HTTP: tool ran, engineering not judged yet
        assert document["execution_status"] == "success"
        assert document["engineering_status"] == "unknown"

    def test_invalid_query_parameters(self, platform) -> None:
        client, _api = platform
        for query in ("?limit=abc", "?limit=0", "?offset=-1", "?status=bogus"):
            response = client.get(f"/api/v1/runs{query}")
            assert response.status_code == 400, query
            assert response.json()["error"]["code"] == "INVALID_REQUEST"

    def test_run_not_found(self, platform) -> None:
        client, _api = platform
        response = client.get("/api/v1/runs/20260101T000000Z-abcdef")
        assert response.status_code == 404
        document = response.json()
        validate_contract("error", document, CONTRACTS)
        assert document["error"]["code"] == "RUN_NOT_FOUND"

    def test_run_id_traversal_rejected(self, platform) -> None:
        client, _api = platform
        # backslashes survive URL routing as one path segment; the format
        # guard rejects before any filesystem access
        response = client.get("/api/v1/runs/..%5C..%5Cetc%5Cpasswd")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RUN_NOT_FOUND"

        # slash-encoded traversal never reaches a route (404, never touches disk)
        response = client.get("/api/v1/runs/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code == 404


class TestQA:
    def test_qa_fail_keeps_execution_success(self, platform) -> None:
        client, api = platform
        run = make_run(api.manager, {"sta.wns_ns": -0.01, "sta.tns_ns": -0.02})

        response = client.post(
            f"/api/v1/runs/{run.run_id}/qa", json={"rule_pack": str(RULES)}
        )
        assert response.status_code == 200
        document = response.json()
        validate_contract("qa", document, CONTRACTS)
        assert document["status"] == "FAIL"
        assert document["summary"]["failed"] == 2

        detail = client.get(f"/api/v1/runs/{run.run_id}").json()
        assert detail["execution_status"] == "success"
        assert detail["engineering_status"] == "fail"

    def test_qa_pass_and_unknown(self, platform) -> None:
        client, api = platform
        clean = make_run(api.manager, {"sta.wns_ns": 0.29, "sta.tns_ns": 0.0})
        blank = make_run(api.manager, {})

        passed = client.post(
            f"/api/v1/runs/{clean.run_id}/qa", json={"rule_pack": str(RULES)}
        )
        assert passed.status_code == 200 and passed.json()["status"] == "PASS"

        unknown = client.post(
            f"/api/v1/runs/{blank.run_id}/qa", json={"rule_pack": str(RULES)}
        )
        assert unknown.status_code == 200 and unknown.json()["status"] == "UNKNOWN"
        assert unknown.json()["summary"]["unknown"] == 2

    def test_qa_request_errors(self, platform) -> None:
        client, api = platform
        run = make_run(api.manager, {"sta.wns_ns": 0.29})
        bad_body = client.post(f"/api/v1/runs/{run.run_id}/qa", json={"nope": 1})
        assert bad_body.status_code == 400
        assert bad_body.json()["error"]["code"] == "INVALID_REQUEST"

        missing_pack = client.post(
            f"/api/v1/runs/{run.run_id}/qa", json={"rule_pack": "no/such/pack.yaml"}
        )
        assert missing_pack.status_code == 400
        assert missing_pack.json()["error"]["code"] == "INVALID_REQUEST"


class TestArtifacts:
    def test_registered_artifact_and_allowlist(self, platform) -> None:
        client, api = platform
        run = make_run(api.manager, {}, artifact="timing.rpt")

        listing = client.get(f"/api/v1/runs/{run.run_id}/artifacts")
        assert listing.status_code == 200
        validate_contract("artifact-list", listing.json(), CONTRACTS)

        detail = client.get(f"/api/v1/runs/{run.run_id}/artifacts/artifacts/timing.rpt")
        assert detail.status_code == 200
        document = detail.json()
        validate_contract("artifact", document, CONTRACTS)
        assert document["sha256"] and document["bytes"] > 0

        unknown = client.get(f"/api/v1/runs/{run.run_id}/artifacts/not-registered.txt")
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"


class TestInternalError:
    def test_unexpected_exception_is_generic_500(self) -> None:
        class ExplodingApi:
            def get_run(self, run_id: str) -> dict[str, Any]:
                raise RuntimeError("boom: secret detail")

        app = create_app(api=ExplodingApi(), contracts_dir=CONTRACTS)  # type: ignore[arg-type]
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs/20260101T000000Z-abcdef")

        assert response.status_code == 500
        document = response.json()
        validate_contract("error", document, CONTRACTS)
        assert document["error"]["code"] == "INTERNAL_ERROR"
        assert document["error"]["message"] == "Internal server error"
        assert "boom" not in response.text


class TestContractAlignment:
    def test_routes_match_contract_paths(self, platform) -> None:
        client, _api = platform
        contract = yaml.safe_load(
            (CONTRACTS / "platform-api.yaml").read_text(encoding="utf-8")
        )
        contract_ops = {
            (path, method.upper())
            for path, operations in contract["paths"].items()
            for method in operations
            if method != "parameters"
        }
        app_ops = {
            (_strip_convertors(route.path), method)
            for route in client.app.routes
            if getattr(route, "methods", None)
            and route.path.startswith("/api/")
            for method in route.methods
            if method != "HEAD"  # Starlette auto-adds HEAD for GET routes
        }
        assert app_ops == contract_ops

    def test_served_openapi_is_the_contract(self, platform) -> None:
        client, _api = platform
        response = client.get("/openapi.json")
        assert response.status_code == 200
        served = response.json()
        contract = yaml.safe_load(
            (CONTRACTS / "platform-api.yaml").read_text(encoding="utf-8")
        )
        assert served["info"]["title"] == contract["info"]["title"]
        assert set(served["paths"]) == set(contract["paths"])
        assert not _has_external_ref(served), "external $refs must be inlined"

    def test_docs_endpoint_available(self, platform) -> None:
        client, _api = platform
        assert client.get("/docs").status_code == 200


def _strip_convertors(path: str) -> str:
    """Normalize Starlette path convertors ({id:path}) to contract form ({id})."""
    return re.sub(r"\{(\w+):\w+\}", r"{\1}", path)


def _has_external_ref(node: Any) -> bool:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            return True
        return any(_has_external_ref(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_external_ref(item) for item in node)
    return False
