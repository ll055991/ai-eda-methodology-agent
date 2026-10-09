"""Unit tests for DockerToolExecutor: argv construction, mounts, pre-flight
classification, and post-timeout cleanup - all with a recording fake driver
beneath the executor, so no Docker is needed (AGENTS.md §12)."""

from pathlib import Path

import pytest

from aemp.core.errors import InvalidRequestError
from aemp.drivers import (
    EXECUTION_STATUS_SUCCESS,
    DockerDaemonUnavailableError,
    DockerImageMissingError,
    DockerToolExecutor,
    ToolDriver,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from aemp.drivers.docker import ContainerMount


def make_result(**overrides) -> ToolExecutionResult:
    fields: dict = dict(
        tool_name="docker",
        argv=("docker", "run", "--rm", "img:1"),
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


class RecordingDriver(ToolDriver):
    """Fake M2.1 driver: records requests, replays queued results."""

    def __init__(self, results: list[ToolExecutionResult] | None = None) -> None:
        super().__init__(tool_name="docker", executable="docker")
        self.requests: list[ToolExecutionRequest] = []
        self._results = list(results or [])

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        self.requests.append(request)
        if self._results:
            return self._results.pop(0)
        return make_result()


def make_executor(
    results: list[ToolExecutionResult],
) -> tuple[DockerToolExecutor, RecordingDriver]:
    driver = RecordingDriver(results=results)
    return DockerToolExecutor(driver=driver), driver


class TestArgvConstruction:
    def test_run_builds_full_docker_argv(self, tmp_path: Path) -> None:
        executor, recorder = make_executor([])
        executor.run(
            image="img:1",
            command=("yosys", "-s", "synth.ys"),
            mounts=(ContainerMount(host_path=tmp_path, container_path="/workspace"),),
            workdir="/workspace",
        )
        argv = recorder.requests[-1].argv
        assert argv[0:2] == ("docker", "run")
        assert "--rm" in argv
        name_index = argv.index("--name")
        assert argv[name_index + 1].startswith("aemp-")  # unique container name
        assert argv[argv.index("-v") + 1] == f"{tmp_path.as_posix()}:/workspace"
        assert argv[argv.index("-w") + 1] == "/workspace"
        # image directly precedes the container command, which stays verbatim
        image_index = argv.index("img:1")
        assert argv[image_index + 1:] == ("yosys", "-s", "synth.ys")

    def test_mount_arg_uses_posix_host_path(self, tmp_path: Path) -> None:
        # Windows paths arrive as 'E:/...' style POSIX strings - no backslashes.
        executor, recorder = make_executor([])
        executor.run(
            image="img:1",
            command=("true",),
            mounts=(
                ContainerMount(host_path=tmp_path, container_path="/rtl", read_only=True),
            ),
        )
        argv = recorder.requests[-1].argv
        assert argv[argv.index("-v") + 1] == f"{tmp_path.as_posix()}:/rtl:ro"

    def test_command_is_argv_never_a_shell_string(self) -> None:
        executor, recorder = make_executor([])
        executor.run(image="img:1", command=("yosys", "-p", "stat; write_verilog out.v"))
        argv = recorder.requests[-1].argv
        # the container command is separate argv elements; the semicolon is
        # data inside one argument, never a shell separator
        assert argv[-3:] == ("yosys", "-p", "stat; write_verilog out.v")
        assert all(isinstance(part, str) for part in argv)

    def test_multiple_mounts_in_order(self, tmp_path: Path) -> None:
        executor, recorder = make_executor([])
        executor.run(
            image="img:1",
            command=("true",),
            mounts=(
                ContainerMount(host_path=tmp_path, container_path="/workspace"),
                ContainerMount(
                    host_path=tmp_path / "rtl", container_path="/rtl", read_only=True
                ),
            ),
        )
        argv = recorder.requests[-1].argv
        mounts = [argv[i + 1] for i, part in enumerate(argv) if part == "-v"]
        assert mounts == [
            f"{tmp_path.as_posix()}:/workspace",
            f"{(tmp_path / 'rtl').as_posix()}:/rtl:ro",
        ]


class TestPreFlightClassification:
    def test_daemon_unreachable_raises_dedicated_error(self) -> None:
        executor, _ = make_executor(
            [make_result(exit_code=1, status="failed", stderr="error during connect")]
        )
        with pytest.raises(DockerDaemonUnavailableError, match="daemon"):
            executor.run(image="img:1", command=("true",))

    def test_image_missing_raises_dedicated_error(self) -> None:
        executor, recorder = make_executor(
            [make_result(), make_result(exit_code=1, status="failed")]
        )
        with pytest.raises(DockerImageMissingError, match="img:missing"):
            executor.run(image="img:missing", command=("true",))
        # classification came from the two pre-flight calls, before any run
        assert recorder.requests[0].argv[1] == "version"
        assert recorder.requests[1].argv[1:3] == ("image", "inspect")

    def test_pre_flight_runs_before_run(self) -> None:
        executor, recorder = make_executor([])
        executor.run(image="img:1", command=("true",))
        subcommands = [request.argv[1] for request in recorder.requests]
        assert subcommands == ["version", "image", "run"]

    def test_empty_image_rejected_before_any_subprocess(self) -> None:
        executor, recorder = make_executor([])
        with pytest.raises(InvalidRequestError, match="image"):
            executor.run(image="  ", command=("true",))
        assert recorder.requests == []

    def test_empty_command_rejected_before_any_subprocess(self) -> None:
        executor, recorder = make_executor([])
        with pytest.raises(InvalidRequestError, match="command"):
            executor.run(image="img:1", command=())
        assert recorder.requests == []


class TestTimeoutCleanup:
    def test_timeout_triggers_best_effort_rm_by_unique_name(self) -> None:
        timed_out = make_result(timed_out=True, exit_code=None, status="failed")
        executor, recorder = make_executor(
            [make_result(), make_result(), timed_out, make_result()]
        )
        result = executor.run(image="img:1", command=("sleep", "30"))
        assert result.timed_out is True

        run_request = recorder.requests[2]
        name = run_request.argv[run_request.argv.index("--name") + 1]
        cleanup = recorder.requests[3]
        assert cleanup.argv == ("docker", "rm", "-f", name)

    def test_non_timeout_does_not_cleanup(self) -> None:
        executor, recorder = make_executor([make_result(), make_result(), make_result()])
        executor.run(image="img:1", command=("true",))
        assert len(recorder.requests) == 3  # pre-flight x2 + run, no rm
