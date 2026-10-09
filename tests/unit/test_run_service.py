"""Unit tests for RunService (M2.4): RunManager <-> Driver orchestration.

The driver is a fake (records calls, simulates Yosys side effects), so the
orchestration lifecycle is fully testable without Docker (AGENTS.md §12).
"""

import json
from pathlib import Path

import pytest

from aemp.core.config import Settings
from aemp.core.errors import InvalidRequestError
from aemp.core.hashing import sha256_bytes
from aemp.drivers import DockerDaemonUnavailableError
from aemp.drivers.models import ToolExecutionResult
from aemp.runs.manager import RunManager
from aemp.schema.result import validate_result
from aemp.service import RunService

REPO_ROOT = Path(__file__).resolve().parents[2]
RTL = REPO_ROOT / "examples" / "rtl" / "simple_counter.v"


def make_result(**overrides) -> ToolExecutionResult:
    fields: dict = dict(
        tool_name="docker",
        argv=("docker", "run", "--rm", "img:1", "yosys", "-s", "synth.ys"),
        executable_path=Path("docker"),
        cwd=Path.cwd(),
        status="success",
        exit_code=0,
        duration_ms=123,
        stdout="Yosys 0.23\nNumber of cells: 10\n",
        stderr="",
        stdout_bytes=33,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        timed_out=False,
    )
    fields.update(overrides)
    return ToolExecutionResult(**fields)


class FakeToolDriver:
    """Fake driver for either slot: records calls, simulates tool effects."""

    def __init__(
        self,
        *,
        version: str = "Yosys 0.23 (git sha1 fake)",
        result: ToolExecutionResult | None = None,
        error: Exception | None = None,
        version_error: Exception | None = None,
    ) -> None:
        self.version = version
        self._result = result if result is not None else make_result()
        self._error = error
        self._version_error = version_error
        self.calls: list[dict] = []
        self.run_status_during_call: str | None = None

    def get_version(self, timeout_s: float = 120.0) -> str:
        if self._version_error is not None:
            raise self._version_error
        return self.version

    def run_script(
        self, *, workspace: Path, script: Path, timeout_s: float = 300.0
    ) -> ToolExecutionResult:
        self.calls.append(
            {"workspace": workspace, "script": script, "timeout_s": timeout_s}
        )
        self.run_status_during_call = json.loads(
            (workspace / "run.json").read_text(encoding="utf-8")
        )["provenance"]["run_status"]
        if self._error is not None:
            raise self._error
        if self._result.succeeded:
            (workspace / "artifacts" / "synthesized.v").write_bytes(b"netlist fake\n")
        return self._result


def make_service(
    tmp_path: Path, *, slot: str = "yosys", **driver_kwargs
) -> tuple[RunService, FakeToolDriver, RunManager]:
    manager = RunManager(Settings(project_root=tmp_path))
    driver = FakeToolDriver(**driver_kwargs)
    service = RunService(manager=manager, **{slot: driver})
    return service, driver, manager


def read_envelope(run) -> dict:
    return json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))


class TestHappyPath:
    def test_full_lifecycle(self, tmp_path: Path) -> None:
        service, fake, manager = make_service(tmp_path)
        run = service.run_synth(rtl=RTL, top="simple_counter")

        assert run.status == "success"
        envelope = read_envelope(run)
        validate_result(envelope)  # still a valid aemp.result/v0 envelope
        assert envelope["step_id"] == "synth"
        assert envelope["operation"] == "yosys.synthesis"
        assert envelope["tool"]["name"] == "yosys"
        assert envelope["tool"]["version"] == "Yosys 0.23 (git sha1 fake)"
        assert envelope["tool"]["argv"] == ["yosys", "-s", "synth.ys"]
        assert envelope["execution"]["status"] == "success"
        assert envelope["execution"]["exit_code"] == 0
        assert envelope["execution"]["duration_ms"] == 123
        assert envelope["execution"]["log_path"] == "logs/yosys.stdout.log"
        assert envelope["engineering"]["status"] == "unknown"  # execution != engineering

        # the driver saw the workspace in the running state (forensic trail)
        assert fake.run_status_during_call == "running"
        # one workspace per execution: the RUN DIRECTORY is the workspace
        assert fake.calls[0]["workspace"] == run.run_dir

    def test_inputs_frozen_and_hashed(self, tmp_path: Path) -> None:
        service, _, _ = make_service(tmp_path)
        run = service.run_synth(rtl=RTL, top="simple_counter")

        envelope = read_envelope(run)
        roles = {entry["role"]: entry for entry in envelope["inputs"]}
        assert set(roles) == {"rtl", "synth_script"}
        assert roles["rtl"]["path"] == "inputs/simple_counter.v"
        assert roles["rtl"]["sha256"] == sha256_bytes(RTL.read_bytes())
        assert roles["synth_script"]["path"] == "synth.ys"
        # the frozen script references the workspace-relative RTL
        assert "inputs/simple_counter.v" in (run.run_dir / "synth.ys").read_text(encoding="utf-8")

    def test_artifacts_registered_with_sha256(self, tmp_path: Path) -> None:
        service, _, _ = make_service(tmp_path)
        run = service.run_synth(rtl=RTL, top="simple_counter")

        envelope = read_envelope(run)
        roles = {entry["role"]: entry for entry in envelope["artifacts"]}
        assert set(roles) == {"netlist", "log_stdout", "log_stderr"}
        assert roles["netlist"]["path"] == "artifacts/synthesized.v"
        assert roles["netlist"]["sha256"] == sha256_bytes(b"netlist fake\n")
        assert (run.run_dir / "logs" / "yosys.stdout.log").is_file()
        assert (run.run_dir / "logs" / "yosys.stderr.log").is_file()

    def test_history_sees_the_run(self, tmp_path: Path) -> None:
        service, _, manager = make_service(tmp_path)
        run = service.run_synth(rtl=RTL, top="simple_counter")
        rows = manager.index.list_runs()
        assert len(rows) == 1
        assert rows[0]["run_id"] == run.run_id
        assert rows[0]["status"] == "success"
        assert rows[0]["operation"] == "yosys.synthesis"

    def test_explicit_run_id_passthrough(self, tmp_path: Path) -> None:
        service, _, _ = make_service(tmp_path)
        run = service.run_synth(
            rtl=RTL, top="simple_counter", run_id="20261007T000000Z-aaaaaa"
        )
        assert run.run_id == "20261007T000000Z-aaaaaa"


class TestFailurePaths:
    def test_missing_rtl_rejected_before_run_creation(self, tmp_path: Path) -> None:
        service, fake, manager = make_service(tmp_path)
        with pytest.raises(InvalidRequestError, match="RTL file does not exist"):
            service.run_synth(rtl=tmp_path / "nope.v", top="simple_counter")
        assert fake.calls == []  # driver never invoked
        assert manager.index.list_runs() == []  # no run recorded

    def test_blank_top_rejected(self, tmp_path: Path) -> None:
        service, _, manager = make_service(tmp_path)
        with pytest.raises(InvalidRequestError, match="top"):
            service.run_synth(rtl=RTL, top="  ")
        assert manager.index.list_runs() == []

    def test_tool_nonzero_exit_fails_the_run(self, tmp_path: Path) -> None:
        failed = make_result(
            status="failed", exit_code=1, stderr="ERROR: parse error in RTL\n"
        )
        service, _, manager = make_service(tmp_path, result=failed)
        run = service.run_synth(rtl=RTL, top="simple_counter")

        assert run.status == "failed"
        envelope = read_envelope(run)
        assert envelope["execution"]["status"] == "failed"
        assert envelope["execution"]["exit_code"] == 1
        assert envelope["engineering"]["status"] == "unknown"
        # no netlist was produced: no netlist artifact is claimed
        roles = {entry["role"] for entry in envelope["artifacts"]}
        assert "netlist" not in roles
        # stderr preserved on disk for diagnosis
        stderr_text = (run.run_dir / "logs" / "yosys.stderr.log").read_text(encoding="utf-8")
        assert "parse error" in stderr_text
        assert manager.index.list_runs()[0]["status"] == "failed"

    def test_timeout_fails_run_with_diagnostic(self, tmp_path: Path) -> None:
        timed_out = make_result(
            timed_out=True, exit_code=None, status="failed", stderr=""
        )
        service, _, _ = make_service(tmp_path, result=timed_out)
        run = service.run_synth(rtl=RTL, top="simple_counter")

        assert run.status == "failed"
        envelope = read_envelope(run)
        assert envelope["execution"]["exit_code"] is None  # killed: no real code
        codes = {d["code"] for d in envelope["diagnostics"]}
        assert "timeout" in codes

    def test_environment_failure_fails_run_execution_stays_unknown(
        self, tmp_path: Path
    ) -> None:
        service, _, manager = make_service(
            tmp_path,
            version_error=DockerDaemonUnavailableError("Docker daemon not reachable"),
        )
        with pytest.raises(DockerDaemonUnavailableError):
            service.run_synth(rtl=RTL, top="simple_counter")

        rows = manager.index.list_runs()
        assert rows[0]["status"] == "failed"  # run failed...
        run_dir = tmp_path / "runs" / rows[0]["run_id"]
        envelope = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        # ...but the tool never ran, so execution stays unknown (not failed)
        assert envelope["execution"]["status"] == "unknown"
        assert envelope["provenance"]["run_status"] == "failed"
        codes = {d["code"] for d in envelope["diagnostics"]}
        assert "environment" in codes


def make_sta_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    netlist = tmp_path / "mapped.v"
    netlist.write_text("module simple_counter(clk); endmodule\n", encoding="utf-8")
    liberty = tmp_path / "demo_nangate45_typ.lib"
    liberty.write_text("library (demo) { cell (AND2_X1) { } }\n", encoding="utf-8")
    sdc = tmp_path / "timing.sdc"
    sdc.write_text("create_clock -name clk -period 0.5 [get_ports clk]\n", encoding="utf-8")
    return netlist, liberty, sdc


class TestRunSta:
    def test_full_lifecycle(self, tmp_path: Path) -> None:
        report_text = "Startpoint: _15_ ...\nslack (MET)\ntns max 0.00\n"
        service, fake, manager = make_service(
            tmp_path, slot="opensta", result=make_result(stdout=report_text)
        )
        netlist, liberty, sdc = make_sta_files(tmp_path)

        run = service.run_sta(
            netlist=netlist, liberty=liberty, sdc=sdc, top="simple_counter"
        )

        assert run.status == "success"
        envelope = read_envelope(run)
        validate_result(envelope)
        assert envelope["step_id"] == "sta"
        assert envelope["operation"] == "opensta.timing"
        assert envelope["tool"]["name"] == "opensta"
        assert envelope["tool"]["argv"] == ["sta", "-no_splash", "-exit", "sta.tcl"]
        assert envelope["execution"]["log_path"] == "logs/opensta.stdout.log"
        assert envelope["engineering"]["status"] == "unknown"

        input_roles = {entry["role"]: entry["path"] for entry in envelope["inputs"]}
        assert input_roles == {
            "netlist": "inputs/mapped.v",
            "liberty": "inputs/demo_nangate45_typ.lib",
            "sdc": "inputs/timing.sdc",
            "sta_script": "sta.tcl",
        }
        artifact_roles = {entry["role"]: entry for entry in envelope["artifacts"]}
        assert "timing_report" in artifact_roles
        assert artifact_roles["timing_report"]["path"] == "artifacts/timing.rpt"
        assert "timing_report" in artifact_roles
        timing_rpt = run.run_dir / "artifacts" / "timing.rpt"
        assert timing_rpt.read_text(encoding="utf-8") == report_text
        assert fake.calls[0]["workspace"] == run.run_dir
        assert manager.index.list_runs()[0]["operation"] == "opensta.timing"

    def test_missing_input_rejected_before_run_creation(self, tmp_path: Path) -> None:
        service, fake, manager = make_service(tmp_path, slot="opensta")
        netlist, liberty, sdc = make_sta_files(tmp_path)
        with pytest.raises(InvalidRequestError, match="Liberty file does not exist"):
            service.run_sta(
                netlist=netlist, liberty=tmp_path / "nope.lib", sdc=sdc, top="simple_counter"
            )
        assert fake.calls == []
        assert manager.index.list_runs() == []

    def test_failed_execution_claims_no_timing_report(self, tmp_path: Path) -> None:
        failed = make_result(
            status="failed", exit_code=1, stderr="Error: design not linked\n"
        )
        service, _, manager = make_service(
            tmp_path, slot="opensta", result=failed
        )
        netlist, liberty, sdc = make_sta_files(tmp_path)
        run = service.run_sta(
            netlist=netlist, liberty=liberty, sdc=sdc, top="simple_counter"
        )

        assert run.status == "failed"
        envelope = read_envelope(run)
        roles = {entry["role"] for entry in envelope["artifacts"]}
        assert "timing_report" not in roles  # a failed stdout is not a report
        assert "log_stderr" in roles
        assert "Error: design not linked" in (
            run.run_dir / "logs" / "opensta.stderr.log"
        ).read_text(encoding="utf-8")

    def test_environment_failure_fails_run_execution_stays_unknown(
        self, tmp_path: Path
    ) -> None:
        service, _, manager = make_service(
            tmp_path,
            slot="opensta",
            version_error=DockerDaemonUnavailableError("Docker daemon not reachable"),
        )
        netlist, liberty, sdc = make_sta_files(tmp_path)
        with pytest.raises(DockerDaemonUnavailableError):
            service.run_sta(
                netlist=netlist, liberty=liberty, sdc=sdc, top="simple_counter"
            )
        envelope = json.loads(
            (tmp_path / "runs" / manager.index.list_runs()[0]["run_id"] / "run.json").read_text(
                encoding="utf-8"
            )
        )
        assert envelope["execution"]["status"] == "unknown"
        assert envelope["provenance"]["run_status"] == "failed"


class TestDriverSlotGuards:
    def test_run_synth_without_yosys_driver_rejected(self, tmp_path: Path) -> None:
        service = RunService(manager=RunManager(Settings(project_root=tmp_path)))
        with pytest.raises(InvalidRequestError, match="yosys driver"):
            service.run_synth(rtl=RTL, top="simple_counter")

    def test_run_sta_without_opensta_driver_rejected(self, tmp_path: Path) -> None:
        service = RunService(manager=RunManager(Settings(project_root=tmp_path)))
        netlist, liberty, sdc = make_sta_files(tmp_path)
        with pytest.raises(InvalidRequestError, match="opensta driver"):
            service.run_sta(netlist=netlist, liberty=liberty, sdc=sdc, top="simple_counter")
