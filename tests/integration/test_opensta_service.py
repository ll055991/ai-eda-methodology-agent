"""Integration tests (M2.5): RunService/OpenSTADriver with real OpenSTA.

Chain: Yosys mapped synthesis (prep) -> RunService.run_sta -> Docker ->
OpenSTA -> ToolExecutionResult -> run archive. Real execution, no mocks;
skips when Docker or the required images are missing.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aemp.core.config import Settings
from aemp.core.hashing import sha256_bytes
from aemp.drivers.opensta import OpenSTADriver
from aemp.drivers.yosys import YosysDriver
from aemp.runs.manager import RunManager
from aemp.schema.result import validate_result
from aemp.service import RunService

IMAGE_STA = "aemp-eda-opensta:3.1.0"
IMAGE_YOSYS = "aemp-eda-base:0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_V = REPO_ROOT / "examples" / "sta" / "design.v"
SDC = REPO_ROOT / "examples" / "sta" / "timing.sdc"
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
def drivers() -> tuple[YosysDriver, OpenSTADriver]:
    try:
        daemon = _run(
            ["docker", "version", "--format", "{{.Server.Version}}"], timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Docker not installed or not runnable on this host")
    if daemon.returncode != 0:
        pytest.skip(f"Docker daemon not available: {daemon.stderr.strip()[:120]}")
    for image in (IMAGE_YOSYS, IMAGE_STA):
        if _run(["docker", "image", "inspect", image], timeout=30).returncode != 0:
            pytest.skip(f"EDA image missing: {image}")

    yosys = YosysDriver.from_config(REPO_ROOT / "configs" / "tools" / "yosys.yaml")
    opensta = OpenSTADriver.from_config(REPO_ROOT / "configs" / "tools" / "opensta.yaml")
    return yosys, opensta


def _extract_lib(dest: Path) -> Path:
    """Copy the demo Liberty out of the pinned OpenSTA image (its source)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            "docker", "run", "--rm",
            "-v", f"{dest.parent.as_posix()}:/out",
            "--entrypoint", "sh",
            IMAGE_STA, "-c", f"cp {LIB_IN_CONTAINER} /out/{dest.name}",
        ]
    )
    assert result.returncode == 0, result.stderr
    return dest


def _map_design(tmp_path: Path, yosys: YosysDriver) -> tuple[Path, Path]:
    """Prep step (M2.3 API, unrecorded): map the counter onto Nangate45."""
    workspace = tmp_path / "mapping"
    workspace.mkdir(parents=True)
    shutil.copy(DESIGN_V, workspace / "design.v")
    lib = _extract_lib(workspace / "demo_nangate45_typ.lib")
    (workspace / "synth_mapped.ys").write_text(_MAPPING_SCRIPT, encoding="utf-8")
    result = yosys.run_script(workspace=workspace, script=workspace / "synth_mapped.ys")
    assert result.exit_code == 0, result.stderr
    mapped = workspace / "mapped.v"
    assert mapped.is_file()
    assert "DFFR_X1" in mapped.read_text(encoding="utf-8")  # real library cells
    return lib, mapped


class TestOpenSTAEnvironment:
    def test_version_from_real_container(self, drivers: tuple) -> None:
        _, opensta = drivers
        version = opensta.get_version()
        assert version == "3.1.0"  # clean self-report from the pinned image


class TestRunSta:
    def test_full_sta_chain_as_formal_run(
        self, tmp_path: Path, drivers: tuple
    ) -> None:
        yosys, opensta = drivers
        project = tmp_path / "project"
        project.mkdir()
        manager = RunManager(Settings(project_root=project))
        service = RunService(manager=manager, opensta=opensta)

        lib, mapped = _map_design(tmp_path, yosys)
        run = service.run_sta(netlist=mapped, liberty=lib, sdc=SDC, top="simple_counter")

        assert run.status == "success"
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        validate_result(envelope)
        assert envelope["step_id"] == "sta"
        assert envelope["tool"]["name"] == "opensta"
        assert envelope["tool"]["version"] == "3.1.0"
        assert envelope["execution"]["exit_code"] == 0
        assert envelope["engineering"]["status"] == "unknown"

        timing_rpt = run.run_dir / "artifacts" / "timing.rpt"
        assert timing_rpt.is_file()
        assert "slack" in timing_rpt.read_text(encoding="utf-8")
        roles = {entry["role"]: entry for entry in envelope["artifacts"]}
        assert roles["timing_report"]["sha256"] == sha256_bytes(timing_rpt.read_bytes())
        input_roles = {entry["role"] for entry in envelope["inputs"]}
        assert input_roles == {"netlist", "liberty", "sdc", "sta_script"}

        assert manager.index.list_runs()[0]["operation"] == "opensta.timing"

    def test_failure_chain_unparseable_netlist(
        self, tmp_path: Path, drivers: tuple
    ) -> None:
        _, opensta = drivers
        manager = RunManager(Settings(project_root=tmp_path))
        service = RunService(manager=manager, opensta=opensta)

        lib = _extract_lib(tmp_path / "demo_nangate45_typ.lib")
        garbage = tmp_path / "garbage_mapped.v"
        garbage.write_text("this is not a netlist <<<>>>\n", encoding="utf-8")

        run = service.run_sta(netlist=garbage, liberty=lib, sdc=SDC, top="simple_counter")

        assert run.status == "failed"
        envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
        assert envelope["execution"]["status"] == "failed"
        assert envelope["execution"]["exit_code"] != 0
        roles = {entry["role"] for entry in envelope["artifacts"]}
        assert "timing_report" not in roles  # failed stdout is not a report
        # OpenSTA prints Tcl errors on STDOUT (unlike yosys): the captured
        # logs must preserve the failure evidence either way
        stdout_log = (run.run_dir / "logs" / "opensta.stdout.log").read_text(encoding="utf-8")
        assert "Error" in stdout_log
        assert manager.index.list_runs()[0]["status"] == "failed"

    def test_timeout_kills_client_and_removes_container(
        self, tmp_path: Path, drivers: tuple
    ) -> None:
        _, opensta = drivers
        workspace = tmp_path / "ws"
        workspace.mkdir()
        result = opensta.execute(
            workspace=workspace, command=("sleep", "30"), timeout_s=1.0
        )
        assert result.timed_out is True
        assert result.exit_code is None
        name = result.argv[result.argv.index("--name") + 1]
        listing = _run(
            ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
            timeout=30,
        )
        assert listing.stdout.strip() == ""
