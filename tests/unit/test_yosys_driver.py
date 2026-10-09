"""Unit tests for YosysDriver: command construction, workspace boundary
enforcement, version parsing, and config loading - with a fake executor,
so no Docker is needed (AGENTS.md §12)."""

from pathlib import Path

import pytest

from aemp.core.errors import InvalidRequestError
from aemp.drivers import (
    EXECUTION_STATUS_FAILED,
    EXECUTION_STATUS_SUCCESS,
    DockerToolExecutor,
    ToolDriver,
    ToolExecutionRequest,
    ToolExecutionResult,
    YosysDriver,
)
from aemp.drivers.docker import ContainerMount

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_result(**overrides) -> ToolExecutionResult:
    fields: dict = dict(
        tool_name="docker",
        argv=("docker", "run", "--rm", "aemp-eda-base:0.1.0"),
        executable_path=Path("docker"),
        cwd=Path.cwd(),
        status=EXECUTION_STATUS_SUCCESS,
        exit_code=0,
        duration_ms=10,
        stdout="",
        stderr="",
        stdout_bytes=0,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        timed_out=False,
    )
    fields.update(overrides)
    return ToolExecutionResult(**fields)


class FakeExecutor(DockerToolExecutor):
    """Records run() kwargs, replays a queued result - no subprocess at all."""

    def __init__(self, results: list[ToolExecutionResult] | None = None) -> None:
        super().__init__(driver=ToolDriverStub())
        self.calls: list[dict] = []
        self._results = list(results or [])

    def run(self, **kwargs) -> ToolExecutionResult:  # type: ignore[override]
        self.calls.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return make_result()


class ToolDriverStub(ToolDriver):
    """Satisfies the base-class type without touching subprocess."""

    def __init__(self) -> None:
        super().__init__(tool_name="docker", executable="docker")

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:  # pragma: no cover
        raise AssertionError("FakeExecutor must never reach the real driver")


def make_driver(**overrides) -> tuple[YosysDriver, FakeExecutor]:
    executor = FakeExecutor(results=overrides.pop("results", None))
    driver = YosysDriver(image="aemp-eda-base:0.1.0", executor=executor, **overrides)
    return driver, executor


class TestRunScript:
    def test_mounts_workspace_and_runs_yosys_script(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        script = workspace / "synth.ys"
        script.write_text("stat\n", encoding="utf-8")

        driver, executor = make_driver()
        result = driver.run_script(workspace=workspace, script=script, timeout_s=42)

        call = executor.calls[-1]
        assert call["command"] == ("yosys", "-s", "synth.ys")  # workspace-relative
        assert call["workdir"] == "/workspace"
        assert call["timeout_s"] == 42
        mounts = call["mounts"]
        assert len(mounts) == 1
        assert isinstance(mounts[0], ContainerMount)
        assert mounts[0].host_path == workspace
        assert mounts[0].container_path == "/workspace"
        assert result.succeeded is True

    def test_script_outside_workspace_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside.ys"
        outside.write_text("stat\n", encoding="utf-8")

        driver, executor = make_driver()
        with pytest.raises(InvalidRequestError, match="workspace"):
            driver.run_script(workspace=workspace, script=outside)
        assert executor.calls == []  # nothing dispatched

    def test_missing_script_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        driver, executor = make_driver()
        with pytest.raises(InvalidRequestError, match="does not exist"):
            driver.run_script(workspace=workspace, script=workspace / "nope.ys")
        assert executor.calls == []

    def test_missing_workspace_rejected(self, tmp_path: Path) -> None:
        driver, executor = make_driver()
        with pytest.raises(InvalidRequestError, match="Workspace"):
            driver.run_script(
                workspace=tmp_path / "nope", script=tmp_path / "nope" / "synth.ys"
            )
        assert executor.calls == []

    def test_execute_generic_command(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        driver, executor = make_driver()
        driver.execute(workspace=workspace, command=("yosys", "-p", "stat"))
        assert executor.calls[-1]["command"] == ("yosys", "-p", "stat")

    def test_execute_rejects_empty_command(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        driver, executor = make_driver()
        with pytest.raises(InvalidRequestError, match="command"):
            driver.execute(workspace=workspace, command=())
        assert executor.calls == []


class TestGetVersion:
    def test_parses_first_real_output_line(self) -> None:
        driver, executor = make_driver(
            results=[
                make_result(
                    stdout="Yosys 0.23 (git sha1 7ce5011c24b)\n\nEnd of script.\n"
                )
            ]
        )
        assert driver.get_version() == "Yosys 0.23 (git sha1 7ce5011c24b)"
        call = executor.calls[-1]
        assert call["command"] == ("yosys", "-V")
        assert call["mounts"] == ()  # version probe needs no workspace

    def test_unparseable_output_is_unknown_never_guessed(self) -> None:
        driver, _ = make_driver(results=[make_result(stdout="   \n")])
        assert driver.get_version() == "UNKNOWN"

    def test_failed_probe_is_unknown_with_stderr_logged(self) -> None:
        driver, _ = make_driver(
            results=[make_result(status=EXECUTION_STATUS_FAILED, exit_code=2, stderr="boom")]
        )
        assert driver.get_version() == "UNKNOWN"


class TestConfig:
    def test_from_config_reads_committed_yaml(self) -> None:
        config = REPO_ROOT / "configs" / "tools" / "yosys.yaml"
        driver, _ = make_driver()
        loaded = YosysDriver.from_config(config)
        # guard the committed configuration against regressions
        assert loaded.image == "aemp-eda-base:0.1.0"
        assert loaded.container_workdir == "/workspace"
        assert loaded.version_args == ("-V",)
        assert driver.image == "aemp-eda-base:0.1.0"

    def test_from_config_missing_image_rejected(self, tmp_path: Path) -> None:
        config = tmp_path / "yosys.yaml"
        config.write_text("name: yosys\nexecutor: docker\n", encoding="utf-8")
        with pytest.raises(InvalidRequestError, match="image"):
            YosysDriver.from_config(config)

    def test_blank_image_rejected_in_constructor(self) -> None:
        with pytest.raises(InvalidRequestError, match="image"):
            YosysDriver(image="  ")
