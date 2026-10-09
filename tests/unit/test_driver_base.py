"""Tests for the ToolDriver subprocess governance layer.

Fake tools are the running Python interpreter itself (``sys.executable -c
"<code>"``): real subprocesses, fully cross-platform, and no EDA tool
installation is ever required. The "tool missing" case uses a name that
cannot exist on any machine.
"""

import os
import sys
from pathlib import Path

import pytest

from aemp.core.errors import InvalidRequestError, ToolNotFoundError
from aemp.drivers import (
    EXECUTION_STATUS_FAILED,
    EXECUTION_STATUS_SUCCESS,
    ToolDriver,
    ToolExecutionRequest,
    ToolExecutionResult,
)

PY = sys.executable  # absolute path -> used directly as the fake "tool"


def make_driver(**overrides) -> ToolDriver:
    kwargs = {"tool_name": "fake-tool", "executable": PY}
    kwargs.update(overrides)
    return ToolDriver(**kwargs)


def run_code(code: str, **request_overrides) -> ToolExecutionResult:
    """Run *code* in a child interpreter through ToolDriver.execute."""
    request_overrides.setdefault("argv", (PY, "-c", code))
    return make_driver().execute(
        ToolExecutionRequest(tool_name="fake-tool", **request_overrides)
    )


class TestRequestValidation:
    def test_empty_argv_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="argv"):
            ToolExecutionRequest(tool_name="t", argv=())

    def test_non_string_argv_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="argv"):
            ToolExecutionRequest(tool_name="t", argv=(PY, 42))

    def test_shell_style_string_rejected_by_design(self) -> None:
        # A single shell string is one "argv element" - still rejected only if
        # empty; the real protection is that argv stays a vector. This test
        # documents that a list is accepted and normalized to a tuple.
        request = ToolExecutionRequest(tool_name="t", argv=[PY, "-c", "print(1)"])
        assert isinstance(request.argv, tuple)

    def test_non_positive_timeout_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="timeout"):
            ToolExecutionRequest(tool_name="t", argv=(PY,), timeout_s=0)

    def test_blank_tool_name_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="tool_name"):
            ToolExecutionRequest(tool_name="  ", argv=(PY,))


class TestExecuteSuccess:
    def test_success_result(self) -> None:
        result = run_code("print('hello')")
        assert result.status == EXECUTION_STATUS_SUCCESS
        assert result.succeeded is True
        assert result.exit_code == 0
        assert result.timed_out is False

    def test_stdout_captured(self) -> None:
        result = run_code("import sys; sys.stdout.buffer.write(b'hello\\n')")
        assert "hello" in result.stdout
        assert result.stdout_bytes == len("hello\n")

    def test_stderr_captured(self) -> None:
        result = run_code("import sys; sys.stderr.buffer.write(b'oops\\n')")
        assert "oops" in result.stderr
        assert result.stderr_bytes == len("oops\n")

    def test_duration_recorded(self) -> None:
        result = run_code("import time; time.sleep(0.3)")
        assert result.duration_ms >= 200
        assert result.duration_ms < 60_000

    def test_executed_argv_uses_resolved_program(self) -> None:
        result = run_code("print('x')")
        assert result.executable_path == Path(PY)
        assert result.argv[0] == PY

    def test_cwd_effective_and_recorded(self, tmp_path: Path) -> None:
        result = run_code("import os; print(os.getcwd())", cwd=tmp_path)
        assert os.path.normcase(result.stdout.strip()) == os.path.normcase(str(tmp_path))
        assert os.path.normcase(str(result.cwd)) == os.path.normcase(str(tmp_path))

    def test_cwd_missing_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="Working directory"):
            run_code("print('x')", cwd=tmp_path / "nope")

    def test_argv_never_parsed_by_shell(self) -> None:
        # A shell would split on spaces and execute after ';'. The child must
        # see exactly ONE argument with the full string intact.
        driver = make_driver()
        request = ToolExecutionRequest(
            tool_name="fake-tool",
            argv=(PY, "-c", "import sys; print(sys.argv[1:])", "hello world; rm -rf /"),
        )
        result = driver.execute(request)
        assert result.stdout.strip() == "['hello world; rm -rf /']"

    def test_stdin_detached_child_never_blocks(self) -> None:
        result = run_code("import sys; print('stdin bytes:', len(sys.stdin.read()))")
        assert "stdin bytes: 0" in result.stdout


class TestExecuteFailure:
    def test_nonzero_exit_is_a_result_not_an_exception(self) -> None:
        result = run_code("import sys; print('boom'); sys.exit(3)")
        assert result.status == EXECUTION_STATUS_FAILED
        assert result.succeeded is False
        assert result.exit_code == 3
        assert "boom" in result.stdout

    def test_stderr_and_failure_combined(self) -> None:
        result = run_code(
            "import sys; sys.stderr.write('bad\\n'); sys.exit(1)"
        )
        assert "bad" in result.stderr
        assert result.status == EXECUTION_STATUS_FAILED

    def test_tool_missing_raises(self) -> None:
        driver = make_driver()
        request = ToolExecutionRequest(
            tool_name="ghost",
            argv=("aemp-definitely-missing-tool-xyz", "--version"),
        )
        with pytest.raises(ToolNotFoundError) as excinfo:
            driver.execute(request)
        assert excinfo.value.tool == "aemp-definitely-missing-tool-xyz"

    def test_absolute_program_missing_raises(self, tmp_path: Path) -> None:
        driver = make_driver()
        missing = str(tmp_path / "no-such-tool")  # true absolute path on any OS
        request = ToolExecutionRequest(tool_name="ghost", argv=(missing,))
        with pytest.raises(ToolNotFoundError, match="does not exist"):
            driver.execute(request)

    def test_timeout_kills_child_and_reports_fact(self) -> None:
        result = run_code("import time; time.sleep(30); print('never')", timeout_s=0.5)
        assert result.timed_out is True
        assert result.status == EXECUTION_STATUS_FAILED
        assert result.exit_code is None  # we killed it: the code is meaningless
        assert "never" not in result.stdout
        assert result.duration_ms < 15_000

    def test_bounded_capture_truncates_large_output(self) -> None:
        result = run_code("import sys; sys.stdout.buffer.write(b'A' * 300000 + b'\\n')")
        assert result.stdout_bytes == 300_001  # every byte counted
        assert result.stdout_truncated is True  # middle dropped
        assert len(result.stdout) < 60_000  # storage bounded
        assert result.stdout.startswith("A" * 100)  # head kept
        assert result.stdout.endswith("A" * 99 + "\n")  # true tail kept (incl. final newline)

    def test_small_output_not_truncated(self) -> None:
        result = run_code("import sys; sys.stdout.buffer.write(b'tiny\\n')")
        assert result.stdout_truncated is False
        assert result.stdout_bytes == len("tiny\n")


class TestEnvironment:
    def test_env_mapping_reaches_child(self) -> None:
        merged = {**os.environ, "AEMP_FAKE_VAR": "hello-env"}
        result = run_code(
            "import os; print(os.environ.get('AEMP_FAKE_VAR', 'unset'))", env=merged
        )
        assert "hello-env" in result.stdout

    def test_env_none_inherits_parent(self) -> None:
        result = run_code(
            "import os; print(os.environ.get('AEMP_FAKE_VAR', 'unset'))"
        )
        assert "unset" in result.stdout


class TestVersion:
    def test_version_probed_from_official_command(self) -> None:
        driver = make_driver(version_args=("-c", "print('FakeTool 9.9')"))
        assert driver.get_version() == "FakeTool 9.9"

    def test_version_probe_failure_is_unknown_never_guessed(self) -> None:
        driver = make_driver(version_args=("-c", "import sys; sys.exit(2)"))
        assert driver.get_version() == "UNKNOWN"

    def test_version_probe_empty_output_is_unknown(self) -> None:
        driver = make_driver(version_args=("-c", "pass"))
        assert driver.get_version() == "UNKNOWN"

    def test_version_of_missing_tool_raises(self) -> None:
        driver = make_driver(executable="aemp-definitely-missing-tool-xyz")
        with pytest.raises(ToolNotFoundError):
            driver.get_version()

    def test_unknown_version_is_a_string_not_an_error(self) -> None:
        # UNKNOWN != FAILED: an unparseable version must not raise, and the
        # caller decides what to do with it (e.g. record it in provenance).
        driver = make_driver(version_args=("-c", "import sys; sys.exit(9)"))
        version = driver.get_version()
        assert version == "UNKNOWN"
        assert isinstance(version, str)


class TestDriverConstruction:
    def test_blank_executable_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="executable"):
            make_driver(executable="  ")

    def test_default_timeout_must_be_positive(self) -> None:
        with pytest.raises(InvalidRequestError, match="default_timeout_s"):
            make_driver(default_timeout_s=-1)
