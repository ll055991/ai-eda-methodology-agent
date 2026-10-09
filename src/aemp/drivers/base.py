"""ToolDriver: the single place where aemp starts EDA tool processes.

Every EDA tool invocation in this platform goes through
:meth:`ToolDriver.execute`, which enforces the subprocess governance rules
from AGENTS.md §1:

- parameterized argv, never a shell string (``shell=True`` is never used);
- explicit working directory and a hard wall-clock timeout - the child is
  killed when it expires (whole process group on POSIX);
- bounded output capture: head & tail windows per stream, exact byte
  totals and truncation flags, so a chatty tool cannot exhaust memory
  (storage stays far below the constitutional 16 MiB cap while every
  produced byte is still counted and drained);
- stdin detached, so children never block waiting for interactive input;
- exit code and duration always recorded;
- only launch problems raise (:class:`ToolNotFoundError` for unresolvable
  programs); everything after a successful launch is a result, never an
  exception - a nonzero exit code or a timeout is an execution fact.

A driver reports execution facts only. Judging whether the tool's OUTPUT
is engineering-good is the job of parsers (M4) and the QA engine (M5).

Executable discovery and version probing reuse the doctor primitives
(:func:`aemp.doctor.find_executable`, :func:`aemp.doctor.probe_version`)
so PATH resolution and the UNKNOWN-never-guessed discipline have exactly
one implementation.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from aemp.core.errors import InvalidRequestError, ToolNotFoundError
from aemp.core.logging import get_logger
from aemp.doctor import find_executable, probe_version
from aemp.drivers.models import (
    EXECUTION_STATUS_FAILED,
    EXECUTION_STATUS_SUCCESS,
    ToolExecutionRequest,
    ToolExecutionResult,
)

logger = get_logger(__name__)

_DRAIN_CHUNK_BYTES = 64 * 1024
_VERSION_PROBE_TIMEOUT_S = 30.0
_DEFAULT_TIMEOUT_S = 300.0

# Bounded capture windows (AGENTS.md §1: keep head & tail, record totals
# and truncation flags). Storage per stream is bounded at
# _HEAD_BYTES + _TAIL_BYTES - far below the constitutional 16 MiB cap -
# while every produced byte is still drained and counted.
_HEAD_BYTES = 8 * 1024
_TAIL_BYTES = 32 * 1024


class _StreamCapture:
    """Drains one output stream, storing only bounded head/tail windows.

    The stream is ALWAYS drained to EOF (a full pipe would deadlock the
    child), but storage is bounded: the first ``_HEAD_BYTES`` and the last
    ``_TAIL_BYTES`` of the stream. ``total_bytes`` counts everything seen
    and ``truncated`` reports whether a middle segment was dropped.
    """

    def __init__(self) -> None:
        self._head = bytearray()
        self._tail = bytearray()
        self.total_bytes = 0

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        head_room = _HEAD_BYTES - len(self._head)
        if head_room > 0:
            self._head += chunk[:head_room]
            chunk = chunk[head_room:]
        if chunk:  # head window full - the tail ring keeps the last bytes
            self._tail += chunk
            overflow = len(self._tail) - _TAIL_BYTES
            if overflow > 0:
                del self._tail[:overflow]

    def decoded(self) -> str:
        raw = bytes(self._head) + bytes(self._tail)
        return raw.decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self.total_bytes > len(self._head) + len(self._tail)


def _drain(stream, capture: _StreamCapture) -> None:
    """Read one pipe to EOF, feeding the bounded capture. Thread target."""
    try:
        while chunk := stream.read(_DRAIN_CHUNK_BYTES):
            capture.feed(chunk)
    finally:
        stream.close()


def _resolve_program(program: str) -> Path:
    """Resolve argv[0]: absolute paths must exist; bare names go through PATH.

    Raises:
        ToolNotFoundError: If the program cannot be located.
    """
    candidate = Path(program)
    if candidate.is_absolute():
        if not candidate.is_file():
            msg = f"Executable does not exist: {program}"
            raise ToolNotFoundError(program, message=msg)
        return candidate
    resolved = find_executable(program)
    if resolved is None:
        msg = f"Executable not found on PATH: {program}"
        raise ToolNotFoundError(program, message=msg)
    return resolved


def _validate_cwd(cwd: Path) -> None:
    if not cwd.is_dir():
        msg = f"Working directory does not exist: {cwd}"
        raise InvalidRequestError(msg)


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill the child and (on POSIX) its whole process group."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass  # already gone - fall through to kill()
    process.kill()


class ToolDriver:
    """Base class for all EDA tool drivers (M2.1: infrastructure only).

    Concrete drivers (Yosys / OpenSTA / OpenROAD / KLayout, from M2.2 on)
    subclass this and add domain methods that build
    :class:`ToolExecutionRequest` values and call :meth:`execute`. The
    base deliberately implements the full subprocess governance itself so
    individual drivers cannot bypass it.

    Args:
        tool_name: Label recorded on every result (e.g. ``yosys``).
        executable: Program name to resolve on PATH (or an absolute path).
        version_args: Arguments of the tool's official version command.
        default_timeout_s: Default wall-clock bound for requests built by
            subclass helpers; every execution is always bounded.
    """

    def __init__(
        self,
        *,
        tool_name: str,
        executable: str,
        version_args: tuple[str, ...] = (),
        default_timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not tool_name or not tool_name.strip():
            msg = "tool_name must be a non-empty string"
            raise InvalidRequestError(msg)
        if not executable or not executable.strip():
            msg = "executable must be a non-empty string"
            raise InvalidRequestError(msg)
        if default_timeout_s <= 0:
            msg = f"default_timeout_s must be positive, got {default_timeout_s}"
            raise InvalidRequestError(msg)
        self.tool_name = tool_name
        self.executable = executable
        self.version_args = tuple(version_args)
        self.default_timeout_s = default_timeout_s

    def resolve_executable(self) -> Path:
        """Locate this driver's tool executable.

        Raises:
            ToolNotFoundError: If it cannot be found on PATH (or an
                explicitly configured absolute path does not exist).
        """
        return _resolve_program(self.executable)

    def get_version(self, timeout_s: float = _VERSION_PROBE_TIMEOUT_S) -> str:
        """Probe the tool's official version command.

        Returns:
            The self-reported version string, or ``UNKNOWN`` when the
            tool exists but the probe fails or its output cannot be
            parsed. Versions are never guessed (M1 discipline: UNKNOWN
            is a valid answer, not a failure).

        Raises:
            ToolNotFoundError: If the executable cannot be located.
        """
        return probe_version(self.resolve_executable(), self.version_args, timeout=timeout_s)

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """Run one tool command under full subprocess governance.

        The program (``argv[0]``) is resolved first - a bare name goes
        through PATH, an absolute path must exist - and the executed
        ``argv`` recorded in the result contains the resolved path.

        Raises:
            ToolNotFoundError: If the program cannot be resolved.
            InvalidRequestError: If the working directory does not exist.
            OSError: Propagates unchanged if the OS itself refuses to
                launch an otherwise-resolved program (never swallowed).
        """
        program = _resolve_program(request.argv[0])
        argv = (str(program), *request.argv[1:])
        cwd = request.cwd if request.cwd is not None else Path.cwd()
        _validate_cwd(cwd)

        stdout_capture = _StreamCapture()
        stderr_capture = _StreamCapture()
        started = time.monotonic()
        logger.info(
            "launch %s: %s (cwd=%s, timeout_s=%s)",
            request.tool_name,
            request.command,
            cwd,
            request.timeout_s,
        )
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=None if request.env is None else dict(request.env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",  # own process group: killpg reaches grandchildren
        )
        threads = [
            threading.Thread(target=_drain, args=(process.stdout, stdout_capture), daemon=True),
            threading.Thread(target=_drain, args=(process.stderr, stderr_capture), daemon=True),
        ]
        for thread in threads:
            thread.start()

        timed_out = False
        try:
            return_code = process.wait(timeout=request.timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_tree(process)
            return_code = process.wait()  # after the kill, exit is prompt
        duration_ms = int((time.monotonic() - started) * 1000)
        for thread in threads:
            thread.join(timeout=5)

        status = (
            EXECUTION_STATUS_SUCCESS
            if return_code == 0 and not timed_out
            else EXECUTION_STATUS_FAILED
        )
        result = ToolExecutionResult(
            tool_name=request.tool_name,
            argv=argv,
            executable_path=program,
            cwd=cwd,
            status=status,
            exit_code=None if timed_out else return_code,
            duration_ms=duration_ms,
            stdout=stdout_capture.decoded(),
            stderr=stderr_capture.decoded(),
            stdout_bytes=stdout_capture.total_bytes,
            stderr_bytes=stderr_capture.total_bytes,
            stdout_truncated=stdout_capture.truncated,
            stderr_truncated=stderr_capture.truncated,
            timed_out=timed_out,
        )
        logger.info(
            "tool %s finished: status=%s exit_code=%s duration_ms=%d timed_out=%s",
            request.tool_name,
            status,
            result.exit_code,
            duration_ms,
            timed_out,
        )
        return result
