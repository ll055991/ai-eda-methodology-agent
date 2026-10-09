"""Integration tests (M4): real flow -> real timing.rpt -> parser -> metrics.

The correctness anchor (§26): the WNS the parser extracts MUST equal the
value a human reads from the same report file.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aemp.core.config import Settings
from aemp.flow.engine import FlowEngine, default_registry
from aemp.parsers import OpenSTAParser
from aemp.runs.manager import RunManager

IMAGE_STA = "aemp-eda-opensta:3.1.0"
IMAGE_YOSYS = "aemp-eda-base:0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_IN_CONTAINER = "/OpenSTA/test/nangate45/Nangate45_typ.lib"

pytestmark = pytest.mark.integration

_MAPPING_SCRIPT = """\
read_verilog design.v
hierarchy -check -top simple_counter
proc
opt
fsm
opt
memory
opt
techmap
opt
dfflibmap -liberty demo_nangate45_typ.lib
abc -liberty demo_nangate45_typ.lib
setundef -zero
splitnets
opt_clean -purge
write_verilog -noattr mapped.v
"""


def _run(args: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="module")
def environment(tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        daemon = _run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Docker not installed or not runnable on this host")
    if daemon.returncode != 0:
        pytest.skip(f"Docker daemon not available: {daemon.stderr.strip()[:120]}")
    for image in (IMAGE_YOSYS, IMAGE_STA):
        if _run(["docker", "image", "inspect", image], timeout=30).returncode != 0:
            pytest.skip(f"EDA image missing: {image}")

    # an isolated project: runs land under tmp, the flow spec uses absolute
    # input paths (spec paths resolve against the invocation cwd)
    project = tmp_path_factory.mktemp("m4-project")
    (project / "configs" / "tools").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'it'\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "configs" / "tools" / "yosys.yaml",
                project / "configs" / "tools" / "yosys.yaml")
    shutil.copy(REPO_ROOT / "configs" / "tools" / "opensta.yaml",
                project / "configs" / "tools" / "opensta.yaml")
    return project


def _extract_lib(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        ["docker", "run", "--rm", "-v", f"{dest.parent.as_posix()}:/out",
         "--entrypoint", "sh", IMAGE_STA, "-c",
         f"cp {LIB_IN_CONTAINER} /out/{dest.name}"]
    )
    assert result.returncode == 0, result.stderr
    return dest


def _manual_wns(report_text: str) -> float:
    """The value a human reads from the report (independent of the parser)."""
    for line in report_text.splitlines():
        if line.strip().startswith("worst slack max"):
            return float(line.split()[-1])
    raise AssertionError("report has no worst slack line")


def test_flow_report_metrics_match_manual_reading(
    environment: Path, tmp_path: Path
) -> None:
    project = environment
    ws = tmp_path / "inputs"
    ws.mkdir()
    shutil.copy(REPO_ROOT / "examples" / "sta" / "design.v", ws / "design.v")
    lib = _extract_lib(ws / "demo_nangate45_typ.lib")
    sdc = ws / "timing.sdc"
    sdc.write_text(
        "create_clock -name clk -period 0.5 [get_ports clk]\n"
        "set_input_delay 0.05 -clock clk [get_ports rst_n]\n"
        "set_output_delay 0.05 -clock clk [all_outputs]\n",
        encoding="utf-8",
    )

    spec_text = f"""\
version: "aemp.flow/v1"
flow:
  id: rtl_to_sta_it
inputs:
  files:
    rtl: {(ws / "design.v").as_posix()}
    liberty: {lib.as_posix()}
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
    spec = tmp_path / "flow.yaml"
    spec.write_text(spec_text, encoding="utf-8")

    engine = FlowEngine(
        registry=default_registry(Settings(project_root=project)),
        manager=RunManager(Settings(project_root=project)),
    )
    run = engine.run(spec)

    # the flow recorded parser metrics into the envelope
    envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
    assert envelope["metrics"]["sta.wns_ns"] == pytest.approx(0.29)
    assert envelope["metrics"]["sta.tns_ns"] == pytest.approx(0.0)
    assert envelope["metrics"]["sta.path_count"] >= 1

    # §26 correctness: parser WNS == the value a human reads in the file
    report_path = run.run_dir / "artifacts" / "timing.rpt"
    assert report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8")
    parsed = OpenSTAParser().parse(report_path)
    assert parsed.metric_value("sta.wns_ns") == pytest.approx(_manual_wns(report_text))
    assert parsed.metric_value("sta.tns_ns") == pytest.approx(-0.0) or parsed.metric_value(
        "sta.tns_ns"
    ) == pytest.approx(0.0)
