"""Integration tests (M2.4): the full chain with real Docker/Yosys.

Python -> RunService -> RunManager -> YosysDriver -> DockerToolExecutor
-> docker -> Yosys -> ToolExecutionResult -> run.json -> SQLite -> history.

Real execution, no mocks; skips when Docker or the EDA image is missing.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aemp.cli import main
from aemp.core.config import Settings
from aemp.core.hashing import sha256_bytes
from aemp.drivers.yosys import YosysDriver
from aemp.runs.manager import RunManager
from aemp.schema.result import validate_result
from aemp.service import RunService

IMAGE = "aemp-eda-base:0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[2]
RTL = REPO_ROOT / "examples" / "rtl" / "simple_counter.v"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def service(tmp_path_factory: pytest.TempPathFactory) -> tuple[RunService, Path]:
    try:
        daemon = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Docker not installed or not runnable on this host")
    if daemon.returncode != 0:
        pytest.skip(f"Docker daemon not available: {daemon.stderr.strip()[:120]}")
    inspect = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, text=True, timeout=30
    )
    if inspect.returncode != 0:
        pytest.skip(f"EDA image missing - build it: docker build -t {IMAGE} docker/")

    # an isolated fake project so run dirs land under tmp, not the repo
    project = tmp_path_factory.mktemp("m24-project")
    (project / "configs" / "tools").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'it'\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "configs" / "tools" / "yosys.yaml",
                project / "configs" / "tools" / "yosys.yaml")
    manager = RunManager(Settings(project_root=project))
    yosys = YosysDriver.from_config(project / "configs" / "tools" / "yosys.yaml")
    return RunService(manager=manager, yosys=yosys), project


def test_full_success_chain(
    service: tuple[RunService, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, project = service
    run = svc.run_synth(rtl=RTL, top="simple_counter")

    # run directory created with the execution outputs
    assert (run.run_dir / "inputs" / "simple_counter.v").is_file()
    assert (run.run_dir / "synth.ys").is_file()
    assert (run.run_dir / "logs" / "yosys.stdout.log").is_file()
    netlist = run.run_dir / "artifacts" / "synthesized.v"
    assert netlist.is_file()

    # run.json: valid envelope with mapped execution facts
    envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
    validate_result(envelope)
    assert envelope["step_id"] == "synth"
    assert envelope["provenance"]["run_status"] == "success"
    assert envelope["execution"]["status"] == "success"
    assert envelope["execution"]["exit_code"] == 0
    assert "Yosys" in envelope["tool"]["version"]
    assert envelope["engineering"]["status"] == "unknown"

    # SHA256 of the netlist artifact matches the file actually on disk
    roles = {entry["role"]: entry for entry in envelope["artifacts"]}
    assert roles["netlist"]["sha256"] == sha256_bytes(netlist.read_bytes())
    assert roles["netlist"]["path"] == "artifacts/synthesized.v"

    # stdout log really contains the tool's stat output
    assert "Number of cells" in (run.run_dir / "logs" / "yosys.stdout.log").read_text(
        encoding="utf-8"
    )

    # aemp history (SQLite index) can see the run
    monkeypatch.setenv("AEMP_PROJECT_ROOT", str(project))
    exit_code = main(["history"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert run.run_id in output


def test_failure_chain_tool_error_is_recorded(
    service: tuple[RunService, Path], tmp_path: Path
) -> None:
    svc, project = service
    # RTL that Yosys cannot parse -> the TOOL fails (exit != 0)
    bad_rtl = tmp_path / "broken.v"
    bad_rtl.write_text("this is not verilog at all <<<>>>\n", encoding="utf-8")

    run = svc.run_synth(rtl=bad_rtl, top="simple_counter")

    assert run.status == "failed"
    envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
    assert envelope["execution"]["status"] == "failed"
    assert envelope["execution"]["exit_code"] != 0
    assert envelope["provenance"]["run_status"] == "failed"
    assert envelope["engineering"]["status"] == "unknown"
    stderr_log = (run.run_dir / "logs" / "yosys.stderr.log").read_text(encoding="utf-8")
    assert stderr_log.strip() != ""  # tool error preserved for diagnosis
    validate_result(envelope)


def test_two_runs_produce_identical_netlists(
    service: tuple[RunService, Path], tmp_path: Path
) -> None:
    svc, _ = service
    run_1 = svc.run_synth(rtl=RTL, top="simple_counter")
    run_2 = svc.run_synth(rtl=RTL, top="simple_counter")
    netlist_1 = (run_1.run_dir / "artifacts" / "synthesized.v").read_bytes()
    netlist_2 = (run_2.run_dir / "artifacts" / "synthesized.v").read_bytes()
    assert netlist_1 == netlist_2
    assert run_1.run_id != run_2.run_id
