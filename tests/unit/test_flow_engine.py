"""Unit tests for the FlowEngine with fake drivers: registry resolution,
fail-fast, honest artifact validation, freezing and history - no Docker."""

import json
from pathlib import Path

import pytest

from aemp.core.config import Settings
from aemp.core.errors import InvalidRequestError
from aemp.drivers.models import ToolExecutionResult
from aemp.flow.engine import FlowEngine, ToolRegistry
from aemp.flow.operations import OpenSTATimingOperation, YosysSynthesizeOperation
from aemp.runs.manager import RunManager
from aemp.schema.result import validate_result

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_V = REPO_ROOT / "examples" / "sta" / "design.v"


def make_result(**overrides) -> ToolExecutionResult:
    fields: dict = dict(
        tool_name="docker",
        argv=("docker", "run", "--rm", "img:1"),
        executable_path=Path("docker"),
        cwd=Path.cwd(),
        status="success",
        exit_code=0,
        duration_ms=100,
        stdout="report_checks output\nslack (MET)\n",
        stderr="",
        stdout_bytes=30,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        timed_out=False,
    )
    fields.update(overrides)
    return ToolExecutionResult(**fields)


class FakeFlowDriver:
    """Fake container driver: simulates tool side effects on the workspace."""

    def __init__(
        self,
        *,
        version: str,
        result: ToolExecutionResult | None = None,
        writes: dict[str, bytes] | None = None,
    ) -> None:
        self.version = version
        self._result = result if result is not None else make_result()
        self._writes = writes or {}
        self.calls: list[Path] = []

    def get_version(self, timeout_s: float = 120.0) -> str:
        return self.version

    def run_script(self, *, workspace: Path, script: Path, timeout_s: float = 300.0):
        self.calls.append(workspace)
        for relative, content in self._writes.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return self._result


class TestRegistry:
    def test_unknown_tool_rejected(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(InvalidRequestError, match="Unknown tool"):
            registry.resolve("yosys")

    def test_duplicate_registration_rejected(self) -> None:
        registry = ToolRegistry()
        driver = FakeFlowDriver(version="v")
        registry.register("yosys", driver, YosysSynthesizeOperation())
        with pytest.raises(InvalidRequestError, match="already registered"):
            registry.register("yosys", driver, YosysSynthesizeOperation())


def build_engine(
    tmp_path: Path, *, yosys: FakeFlowDriver, opensta: FakeFlowDriver
) -> tuple[FlowEngine, RunManager]:
    registry = ToolRegistry()
    registry.register("yosys", yosys, YosysSynthesizeOperation())
    registry.register("opensta", opensta, OpenSTATimingOperation())
    manager = RunManager(Settings(project_root=tmp_path))
    return FlowEngine(registry=registry, manager=manager), manager


def write_spec(tmp_path: Path, *, rtl: Path, liberty: Path, sdc: Path) -> Path:
    text = f"""\
version: "aemp.flow/v1"
flow:
  id: demo_flow
  name: Demo
inputs:
  files:
    rtl: {rtl.as_posix()}
    liberty: {liberty.as_posix()}
    sdc: {sdc.as_posix()}
  params:
    top: simple_counter
steps:
  - id: synth
    tool: yosys
    operation: yosys.synthesize
    inputs:
      rtl: ${{inputs.rtl}}
      liberty: ${{inputs.liberty}}
      top: ${{inputs.top}}
    outputs:
      netlist: mapped.v
  - id: sta
    tool: opensta
    operation: opensta.timing_analysis
    inputs:
      netlist: ${{steps.synth.outputs.netlist}}
      liberty: ${{inputs.liberty}}
      sdc: ${{inputs.sdc}}
      top: ${{inputs.top}}
    outputs:
      report: timing.rpt
"""
    spec = tmp_path / "demo_flow.yaml"
    spec.write_text(text, encoding="utf-8")
    return spec


def make_demo_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    rtl = tmp_path / "design.v"
    rtl.write_text("module simple_counter(clk); endmodule\n", encoding="utf-8")
    liberty = tmp_path / "demo.lib"
    liberty.write_text("library (demo) { }\n", encoding="utf-8")
    sdc = tmp_path / "timing.sdc"
    sdc.write_text("create_clock -name clk -period 0.5 [get_ports clk]\n", encoding="utf-8")
    return rtl, liberty, sdc


class TestFlowEngineHappyPath:
    def test_two_step_flow_records_everything(self, tmp_path: Path) -> None:
        rtl, liberty, sdc = make_demo_files(tmp_path)
        yosys = FakeFlowDriver(
            version="Yosys 0.23 (git sha1 fake)",
            writes={"artifacts/mapped.v": b"module simple_counter(DFFR_X1);\n"},
        )
        report = "report_checks\nslack (MET)\nworst slack max 0.29\n"
        opensta = FakeFlowDriver(
            version="3.1.0", result=make_result(stdout=report)
        )
        engine, manager = build_engine(tmp_path, yosys=yosys, opensta=opensta)

        run = engine.run(write_spec(tmp_path, rtl=rtl, liberty=liberty, sdc=sdc))

        assert run.status == "success"
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        validate_result(envelope)
        assert envelope["operation"] == "flow.demo_flow"
        # flow spec freeze (the archive answers "what flow produced this?")
        frozen = run.run_dir / "flow.spec.frozen.yaml"
        assert frozen.is_file() and "aemp.flow/v1" in frozen.read_text(encoding="utf-8")
        # input freeze with hashes
        roles = {entry["role"]: entry for entry in envelope["inputs"]}
        assert roles["rtl"]["path"] == "inputs/design.v"
        assert roles["liberty"]["path"] == "inputs/demo.lib"
        assert roles["sdc"]["sha256"] == sha256(sdc.read_bytes())
        # artifacts registered with per-step roles
        artifact_roles = {entry["role"] for entry in envelope["artifacts"]}
        assert artifact_roles == {
            "synth.netlist",
            "sta.report",
            "synth.stdout",
            "sta.stdout",
            "synth.stderr",
            "sta.stderr",
        }
        # per-step summary recorded in the open provenance area
        steps = envelope["provenance"]["steps"]
        assert [step["id"] for step in steps] == ["synth", "sta"]
        assert all(step["status"] == "success" for step in steps)
        assert envelope["engineering"]["status"] == "unknown"
        assert manager.index.list_runs()[0]["status"] == "success"

    def test_driver_sees_running_state_and_workspace(self, tmp_path: Path) -> None:
        rtl, liberty, sdc = make_demo_files(tmp_path)
        yosys = FakeFlowDriver(
            version="v", writes={"artifacts/mapped.v": b"x\n"}
        )
        engine, _ = build_engine(tmp_path, yosys=yosys, opensta=FakeFlowDriver(version="v"))
        # capture state during the driver call
        seen: list[str] = []
        original = yosys.run_script

        def spy(*, workspace: Path, script: Path, timeout_s: float = 300.0):
            envelope = json.loads((workspace / "run.json").read_text(encoding="utf-8"))
            seen.append(envelope["provenance"]["run_status"])
            return original(workspace=workspace, script=script, timeout_s=timeout_s)

        yosys.run_script = spy  # type: ignore[method-assign]
        run = engine.run(
            write_spec(tmp_path, rtl=rtl, liberty=liberty, sdc=sdc)
        )
        assert seen == ["running"]
        assert run.run_dir.name.startswith("20")


def sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


class TestFailFast:
    def test_tool_failure_skips_remaining_steps(self, tmp_path: Path) -> None:
        rtl, liberty, sdc = make_demo_files(tmp_path)
        yosys = FakeFlowDriver(version="v", result=make_result(status="failed", exit_code=1))
        opensta = FakeFlowDriver(version="v")
        engine, manager = build_engine(tmp_path, yosys=yosys, opensta=opensta)

        run = engine.run(write_spec(tmp_path, rtl=rtl, liberty=liberty, sdc=sdc))

        assert run.status == "failed"
        assert opensta.calls == []  # OpenSTA never executed
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        assert [step["id"] for step in envelope["provenance"]["steps"]] == ["synth"]
        assert manager.index.list_runs()[0]["status"] == "failed"

    def test_missing_declared_output_fails_the_flow(self, tmp_path: Path) -> None:
        rtl, liberty, sdc = make_demo_files(tmp_path)
        # yosys "succeeds" but writes NOTHING -> declared output missing
        yosys = FakeFlowDriver(version="v", writes={})
        opensta = FakeFlowDriver(version="v")
        engine, _ = build_engine(tmp_path, yosys=yosys, opensta=opensta)

        run = engine.run(write_spec(tmp_path, rtl=rtl, liberty=liberty, sdc=sdc))

        assert run.status == "failed"
        assert opensta.calls == []  # no valid interface for the next step
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        codes = {d["code"] for d in envelope["diagnostics"]}
        assert "missing_artifact" in codes

    def test_unknown_tool_fails_before_any_run(self, tmp_path: Path) -> None:
        rtl, liberty, sdc = make_demo_files(tmp_path)
        spec_text = write_spec(tmp_path, rtl=rtl, liberty=liberty, sdc=sdc).read_text(
            encoding="utf-8"
        )
        spec_text = spec_text.replace("tool: yosys", "tool: unknown_tool")
        spec = tmp_path / "bad.yaml"
        spec.write_text(spec_text, encoding="utf-8")

        registry = ToolRegistry()  # empty: nothing registered at all
        manager = RunManager(Settings(project_root=tmp_path))
        engine = FlowEngine(registry=registry, manager=manager)

        with pytest.raises(InvalidRequestError, match="Unknown tool"):
            engine.run(spec)
        assert manager.index.list_runs() == []  # validation happened pre-run
