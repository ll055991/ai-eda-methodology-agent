"""Contract-level tests for the M6.0 three-tier boundaries.

These tests validate the language-neutral contracts
(``contracts/schemas/*.json``) and the DTOs produced by the application
service against them. No HTTP server, no Docker, no EDA tools.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from aemp.api import PlatformApi, error_document
from aemp.api.errors import RUN_NOT_FOUND, ApiError
from aemp.api.schemas import validate_contract
from aemp.core.config import Settings
from aemp.core.errors import SchemaValidationError
from aemp.runs.manager import Run, RunManager

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"
RULES = REPO_ROOT / "configs" / "rules" / "base_timing.yaml"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(project_root=tmp_path)


@pytest.fixture
def api(settings: Settings) -> PlatformApi:
    return PlatformApi(settings, contracts_dir=CONTRACTS)


def make_run(
    manager: RunManager,
    metrics: dict[str, float],
    *,
    artifact: str | None = None,
) -> Run:
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


class TestRunContract:
    def test_run_response_schema(self, api: PlatformApi) -> None:
        run = make_run(api.manager, {"sta.wns_ns": 0.29})

        document = api.get_run(run.run_id)
        validate_contract("run", document, CONTRACTS)

        # M1 semantics survive the boundary: the two statuses never merge
        assert document["execution_status"] == "success"
        assert document["engineering_status"] == "unknown"
        assert document["metrics"] == {"sta.wns_ns": 0.29}

        page = api.list_runs()
        validate_contract("run-list", page, CONTRACTS)
        assert [row["run_id"] for row in page["runs"]] == [run.run_id]
        assert page["runs"][0]["lifecycle_status"] == "success"

        validate_contract(
            "run-summary",
            {
                "run_id": run.run_id,
                "status": "success",
                "execution_status": "success",
                "engineering_status": "unknown",
            },
            CONTRACTS,
        )


class TestFlowContract:
    def test_flow_request_schema(self) -> None:
        validate_contract("flow", {"flow": "configs/flows/rtl_to_sta.yaml"}, CONTRACTS)
        validate_contract(
            "flow",
            {"flow": "flow.yaml", "inputs": {"rtl": "examples/rtl/simple_counter.v"}},
            CONTRACTS,
        )


class TestQAContract:
    def test_qa_response_schema(self, api: PlatformApi) -> None:
        run = make_run(api.manager, {"sta.wns_ns": -0.01, "sta.tns_ns": -0.02})

        document = api.run_qa(run.run_id, {"rule_pack": str(RULES)})
        validate_contract("qa", document, CONTRACTS)

        assert document["status"] == "FAIL"
        assert document["summary"]["failed"] == 2
        wns = next(f for f in document["findings"] if f["metric"] == "sta.wns_ns")
        assert wns["actual"] == pytest.approx(-0.01)
        assert wns["status"] == "fail" and wns["severity"] == "error"

        # the verdict is engineering-only; execution stays untouched
        detail = api.get_run(run.run_id)
        assert detail["execution_status"] == "success"
        assert detail["engineering_status"] == "fail"


class TestArtifactContract:
    def test_artifact_response_schema(self, api: PlatformApi) -> None:
        run = make_run(api.manager, {}, artifact="timing.rpt")

        listing = api.list_artifacts(run.run_id)
        validate_contract("artifact-list", listing, CONTRACTS)
        assert listing["artifacts"][0]["artifact_id"] == "artifacts/timing.rpt"

        record = api.get_artifact(run.run_id, "artifacts/timing.rpt")
        validate_contract("artifact", record, CONTRACTS)
        assert record["sha256"] and record["bytes"] > 0

        # allowlist discipline: not an arbitrary file reader
        with pytest.raises(ApiError) as excinfo:
            api.get_artifact(run.run_id, "C:/Windows/win.ini")
        assert excinfo.value.code == "ARTIFACT_NOT_FOUND"


class TestErrorContract:
    def test_error_response_schema(self, api: PlatformApi) -> None:
        document = error_document(ApiError(RUN_NOT_FOUND, "Run not found: x"))
        validate_contract("error", document, CONTRACTS)
        assert document["error"]["code"] == RUN_NOT_FOUND
        assert document["error"]["details"] == {}

        with pytest.raises(ApiError) as excinfo:
            api.get_run("20260101T000000Z-abcdef")
        assert excinfo.value.code == RUN_NOT_FOUND
        assert excinfo.value.http_status == 404
        validate_contract("error", error_document(excinfo.value), CONTRACTS)

        # run ids are validated before any filesystem access
        with pytest.raises(ApiError) as excinfo:
            api.get_run("../../etc/passwd")
        assert excinfo.value.code == RUN_NOT_FOUND


class TestAgentChatContract:
    def test_agent_chat_request_and_response_schema(self) -> None:
        validate_contract(
            "agent-chat-request",
            {"message": "run RTL to STA and report timing", "session_id": None},
            CONTRACTS,
        )
        validate_contract(
            "agent-chat-response",
            {"message": "done", "status": "completed", "run_id": None},
            CONTRACTS,
        )
        # approval fields are reserved but already valid in the contract
        validate_contract(
            "agent-chat-response",
            {
                "message": "approval required",
                "status": "pending_approval",
                "approval_required": True,
                "approval_id": "a-1",
                "resume_token": "t-1",
            },
            CONTRACTS,
        )


class TestUnknownFields:
    def test_unknown_fields_are_rejected(self, api: PlatformApi) -> None:
        run = make_run(api.manager, {"sta.wns_ns": -0.01}, artifact="timing.rpt")
        documents: list[tuple[str, dict[str, Any]]] = [
            ("run", api.get_run(run.run_id)),
            ("run-list", api.list_runs()),
            (
                "run-summary",
                {
                    "run_id": run.run_id,
                    "status": "success",
                    "execution_status": "success",
                    "engineering_status": "unknown",
                },
            ),
            ("flow", {"flow": "configs/flows/rtl_to_sta.yaml"}),
            ("qa", api.run_qa(run.run_id, {"rule_pack": str(RULES)})),
            ("artifact-list", api.list_artifacts(run.run_id)),
            ("error", error_document(ApiError(RUN_NOT_FOUND, "x"))),
            ("agent-chat-request", {"message": "hi"}),
            ("agent-chat-response", {"message": "hi", "status": "completed"}),
        ]
        for name, document in documents:
            with pytest.raises(SchemaValidationError):
                validate_contract(name, {**document, "unexpected_field": True}, CONTRACTS)

        # nested unknown fields are rejected too
        detail = api.get_run(run.run_id)
        detail["tool"]["unexpected"] = True
        with pytest.raises(SchemaValidationError):
            validate_contract("run", detail, CONTRACTS)


class TestAgentToolBoundary:
    def test_no_arbitrary_capability_tools_in_agent_contract(self) -> None:
        contract = yaml.safe_load(
            (CONTRACTS / "agent-api.yaml").read_text(encoding="utf-8")
        )
        tools = contract["x-agent-tools"]["tools"]

        names = {tool["name"] for tool in tools}
        assert names == {"run_flow", "get_run", "run_qa", "get_artifacts"}

        allowed_endpoints = {
            "/api/v1/flows/run",
            "/api/v1/runs/{run_id}",
            "/api/v1/runs/{run_id}/qa",
            "/api/v1/runs/{run_id}/artifacts",
        }
        for tool in tools:
            assert tool["platform_endpoint"] in allowed_endpoints

        # no capability token of arbitrary execution appears anywhere in the tool table
        blob = json.dumps(contract["x-agent-tools"]).lower()
        for token in ("shell", "bash", "subprocess", "arbitrary_command", "arbitrary_file_read"):
            assert token not in blob

    def test_contract_refs_resolve_to_files(self) -> None:
        for api_file in ("platform-api.yaml", "agent-api.yaml"):
            text = (CONTRACTS / api_file).read_text(encoding="utf-8")
            for ref in re.findall(r'\$ref:\s*"?([^"\s]+)"?', text):
                if ref.startswith("#"):
                    continue
                assert (CONTRACTS / ref).is_file(), f"{api_file}: dangling $ref {ref}"
