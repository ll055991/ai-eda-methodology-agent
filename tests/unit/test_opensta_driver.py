"""Unit tests for OpenSTADriver (M2.5): config, command shape, and the
OpenSTA version-probe quirk - with a fake executor, no Docker needed."""

from pathlib import Path

import pytest

from aemp.core.errors import InvalidRequestError
from aemp.drivers import (
    EXECUTION_STATUS_FAILED,
    EXECUTION_STATUS_SUCCESS,
    DockerToolExecutor,
    OpenSTADriver,
    ToolDriver,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from aemp.drivers.docker import ContainerMount

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_result(**overrides) -> ToolExecutionResult:
    fields: dict = dict(
        tool_name="docker",
        argv=("docker", "run", "--rm", "aemp-eda-opensta:3.1.0"),
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


def make_driver(**overrides) -> tuple[OpenSTADriver, FakeExecutor]:
    executor = FakeExecutor(results=overrides.pop("results", None))
    driver = OpenSTADriver(image="aemp-eda-opensta:3.1.0", executor=executor, **overrides)
    return driver, executor


class TestConfig:
    def test_from_config_reads_committed_yaml(self) -> None:
        driver = OpenSTADriver.from_config(
            REPO_ROOT / "configs" / "tools" / "opensta.yaml"
        )
        # guard the committed configuration against regressions
        assert driver.image == "aemp-eda-opensta:3.1.0"
        assert driver.binary == "sta"
        assert driver.container_workdir == "/workspace"
        assert driver.version_args == ("-version",)

    def test_from_config_missing_image_rejected(self, tmp_path: Path) -> None:
        config = tmp_path / "opensta.yaml"
        config.write_text("name: opensta\nexecutor: docker\n", encoding="utf-8")
        with pytest.raises(InvalidRequestError, match="image"):
            OpenSTADriver.from_config(config)

    def test_blank_image_rejected_in_constructor(self) -> None:
        with pytest.raises(InvalidRequestError, match="image"):
            OpenSTADriver(image="  ")


class TestGetVersion:
    def test_clean_version_from_real_flag_shape(self) -> None:
        driver, executor = make_driver(
            results=[make_result(stdout="3.1.0\n")]
        )
        assert driver.get_version() == "3.1.0"
        assert executor.calls[-1]["command"] == ("sta", "-version")
        assert executor.calls[-1]["mounts"] == ()

    def test_stdout_parsed_even_when_exit_is_nonzero(self) -> None:
        # Robustness rule: stdout is the tool's self-report and is parsed
        # regardless of exit code (stderr is never treated as a version).
        banner = "OpenSTA 3.1.0 d1e43c6f9f Copyright (c) 2026, Parallax Software, Inc."
        driver, _ = make_driver(
            results=[
                make_result(
                    status=EXECUTION_STATUS_FAILED,
                    exit_code=1,
                    stdout=banner + "\nUsage: ...\n",
                )
            ]
        )
        assert driver.get_version() == banner

    def test_unparseable_output_is_unknown_never_guessed(self) -> None:
        driver, _ = make_driver(
            results=[make_result(status=EXECUTION_STATUS_FAILED, exit_code=1, stdout="")]
        )
        # empty stdout: nothing self-reported -> UNKNOWN (stderr is never
        # parsed as a version)
        assert driver.get_version() == "UNKNOWN"

    def test_successful_probe_parses_first_line(self) -> None:
        driver, _ = make_driver(results=[make_result(stdout="OpenSTA 3.1.0\nmore\n")])
        assert driver.get_version() == "OpenSTA 3.1.0"


class TestRunScript:
    def test_opensta_invocation_shape(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        script = workspace / "sta.tcl"
        script.write_text("report_checks\n", encoding="utf-8")

        driver, executor = make_driver()
        result = driver.run_script(workspace=workspace, script=script, timeout_s=42)

        call = executor.calls[-1]
        assert call["command"] == ("sta", "-no_splash", "-exit", "sta.tcl")
        assert call["workdir"] == "/workspace"
        assert call["timeout_s"] == 42
        mounts = call["mounts"]
        assert len(mounts) == 1
        assert isinstance(mounts[0], ContainerMount)
        assert mounts[0].host_path == workspace
        assert result.succeeded is True

    def test_script_outside_workspace_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside.tcl"
        outside.write_text("report_checks\n", encoding="utf-8")

        driver, executor = make_driver()
        with pytest.raises(InvalidRequestError, match="workspace"):
            driver.run_script(workspace=workspace, script=outside)
        assert executor.calls == []

    def test_missing_script_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        driver, executor = make_driver()
        with pytest.raises(InvalidRequestError, match="does not exist"):
            driver.run_script(workspace=workspace, script=workspace / "nope.tcl")
        assert executor.calls == []

    def test_missing_workspace_rejected(self, tmp_path: Path) -> None:
        driver, executor = make_driver()
        with pytest.raises(InvalidRequestError, match="Workspace"):
            driver.run_script(workspace=tmp_path / "nope", script=tmp_path / "s.tcl")
        assert executor.calls == []
