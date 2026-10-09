"""Integration tests: Python -> YosysDriver -> Docker -> Yosys -> result.

Real execution, no mocks (AGENTS.md §12): they need a working Docker daemon
AND the aemp-eda-base image; otherwise the module skips. Unit tests never
touch Docker.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from aemp.drivers import DockerImageMissingError, ToolExecutionResult, YosysDriver

IMAGE = "aemp-eda-base:0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration

_SYNTH_SCRIPT = (
    "read_verilog inputs/simple_counter.v\n"
    "hierarchy -check -top simple_counter\n"
    "synth -top simple_counter\n"
    "stat\n"
    "write_verilog -noattr artifacts/synthesized.v\n"
)


def _docker_ready() -> bool:
    try:
        probe = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


@pytest.fixture(scope="module")
def driver() -> YosysDriver:
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
    return YosysDriver.from_config(REPO_ROOT / "configs" / "tools" / "yosys.yaml")


def make_workspace(tmp_path: Path, *, script: str = _SYNTH_SCRIPT) -> Path:
    """One workspace per execution: inputs + script inside the boundary."""
    workspace = tmp_path / "ws"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    shutil.copy(REPO_ROOT / "examples" / "rtl" / "simple_counter.v",
                workspace / "inputs" / "simple_counter.v")
    (workspace / "synth.ys").write_text(script, encoding="utf-8")
    return workspace


def test_get_version_from_real_container(driver: YosysDriver) -> None:
    version = driver.get_version()
    assert version.startswith("Yosys ")
    assert "git sha1" in version  # real build identity, never a guess


def test_synthesis_via_driver_returns_m21_result(
    tmp_path: Path, driver: YosysDriver
) -> None:
    workspace = make_workspace(tmp_path)
    result = driver.run_script(workspace=workspace, script=workspace / "synth.ys")

    assert isinstance(result, ToolExecutionResult)  # M2.1 result type, no second hierarchy
    assert result.exit_code == 0
    assert result.status == "success"
    assert result.timed_out is False
    assert result.duration_ms > 0
    assert "Number of cells" in result.stdout  # real stat from the real tool
    # the governed host process is the docker CLI (resolved to an absolute
    # path by M2.1); the container command is visible in argv
    assert Path(result.argv[0]).name.lower().startswith("docker")
    assert result.executable_path == Path(result.argv[0])
    assert "yosys" in result.argv
    # artifacts written by the container land in the host workspace
    netlist = workspace / "artifacts" / "synthesized.v"
    assert netlist.is_file()
    assert "module simple_counter" in netlist.read_text(encoding="utf-8")


def test_two_runs_produce_identical_netlists(
    tmp_path: Path, driver: YosysDriver
) -> None:
    workspace_1 = make_workspace(tmp_path)
    workspace_2 = tmp_path / "ws2"
    shutil.copytree(workspace_1, workspace_2)

    driver.run_script(workspace=workspace_1, script=workspace_1 / "synth.ys")
    driver.run_script(workspace=workspace_2, script=workspace_2 / "synth.ys")

    netlist_1 = (workspace_1 / "artifacts" / "synthesized.v").read_text(encoding="utf-8")
    netlist_2 = (workspace_2 / "artifacts" / "synthesized.v").read_text(encoding="utf-8")
    assert netlist_1 == netlist_2


def test_yosys_nonzero_exit_is_a_result_not_an_exception(
    tmp_path: Path, driver: YosysDriver
) -> None:
    workspace = make_workspace(
        tmp_path, script="read_verilog inputs/does_not_exist.v\n"
    )
    result = driver.run_script(workspace=workspace, script=workspace / "synth.ys")
    # tool-level failure is a recorded fact - distinct from environment errors
    assert result.status == "failed"
    assert result.exit_code != 0
    assert result.stderr.strip() != ""


def test_missing_image_raises_environment_error(tmp_path: Path, driver: YosysDriver) -> None:
    broken = YosysDriver(image="aemp-definitely-missing-image:v9", executor=driver._executor)
    with pytest.raises(DockerImageMissingError, match="aemp-definitely-missing-image"):
        broken.get_version()


def test_timeout_kills_client_and_removes_container(
    tmp_path: Path, driver: YosysDriver
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = driver.execute(
        workspace=workspace, command=("sleep", "30"), timeout_s=1.0
    )
    assert result.timed_out is True
    assert result.exit_code is None  # killed by us - no meaningful exit code
    assert result.status == "failed"

    # the unique container was removed by the executor's cleanup
    name = result.argv[result.argv.index("--name") + 1]
    listing = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert listing.stdout.strip() == ""  # nothing left behind
