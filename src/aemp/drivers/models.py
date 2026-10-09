"""Value types for tool execution: execution facts only, no engineering verdicts.

These types map 1:1 onto the ``aemp.result/v0`` envelope blocks (``tool``
and ``execution``) so the flow engine (M3) can record driver results
without translation surprises - but the drivers package itself stays
dependency-free of runs/schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from aemp.core.errors import InvalidRequestError

# Execution facts are deliberately a two-value vocabulary: a driver that
# launched a process always knows which one applies. 'unknown' exists only
# at the envelope level, for runs that never executed anything.
EXECUTION_STATUS_SUCCESS = "success"
EXECUTION_STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ToolExecutionRequest:
    """Everything needed to run one EDA tool command.

    Attributes:
        tool_name: Label of the tool this command belongs to (e.g.
            ``yosys``). Metadata only - the program that runs is argv[0].
        argv: The full command as an argv vector INCLUDING the program.
            Never a shell string: it is handed to the OS verbatim, so a
            tool name or argument containing spaces/semicolons stays one
            element. Example::

                ("yosys", "-p", "read_verilog design.v; stat")

        cwd: Working directory for the child process; ``None`` inherits
            the caller's current directory (the one actually used is
            recorded in the result either way).
        env: Child environment. ``None`` inherits the parent environment;
            a mapping REPLACES it entirely. Closed per-tool environments
            (whitelists) arrive with the concrete drivers in M2.2.
        timeout_s: Hard wall-clock bound. The child is killed when it
            expires and the result is marked ``timed_out`` - a timeout is
            a recorded execution fact, not an exception.

    Raises:
        InvalidRequestError: On an empty/blank ``tool_name``, an empty or
            non-string ``argv``, or a non-positive ``timeout_s``.
    """

    tool_name: str
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    timeout_s: float = 300.0

    def __post_init__(self) -> None:
        if not self.tool_name or not self.tool_name.strip():
            msg = "tool_name must be a non-empty string"
            raise InvalidRequestError(msg)
        argv = tuple(self.argv)
        if not argv or not all(isinstance(part, str) and part for part in argv):
            msg = "argv must be a non-empty sequence of non-empty strings (never a shell string)"
            raise InvalidRequestError(msg)
        if self.timeout_s <= 0:
            msg = f"timeout_s must be positive, got {self.timeout_s}"
            raise InvalidRequestError(msg)
        object.__setattr__(self, "argv", argv)

    @property
    def command(self) -> str:
        """Human-readable command line. Display only - never re-parsed."""
        return " ".join(self.argv)


@dataclass(frozen=True)
class ToolExecutionResult:
    """Facts about one tool execution. NEVER an engineering verdict.

    ``exit_code == 0`` means "the program ran and returned 0"; it says
    nothing about design quality. Parsers (M4) read tool output and the
    QA engine (M5) judges engineering quality - never this class.

    ``stdout``/``stderr`` are bounded captures: the first bytes (head)
    and last bytes (tail) of each stream, decoded with
    ``errors="replace"`` (a non-UTF-8 tool log can never crash the
    platform), plus exact byte totals and truncation flags.

    Mapping to ``aemp.result/v0``: ``status``/``exit_code``/
    ``duration_ms``/``cwd`` fill the ``execution`` block, ``argv``/
    ``executable_path`` fill ``tool``, and ``timed_out`` becomes a
    diagnostic.

    ``executable_path`` semantics: it is always the HOST program that was
    actually spawned and governed. For host tools that is the tool binary;
    for container-backed execution (DockerToolExecutor, M2.3+) it is the
    HOST launcher (the docker CLI), while the container-side program and
    image are visible in ``argv`` and the container tool identity comes
    from the driver's version probe. The two cases are never conflated.
    """

    tool_name: str
    argv: tuple[str, ...]
    executable_path: Path
    cwd: Path
    status: str
    exit_code: int | None
    duration_ms: int
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        """True when the process ran to completion with exit code 0."""
        return self.status == EXECUTION_STATUS_SUCCESS

    @property
    def command(self) -> str:
        """Human-readable command line. Display only - never re-parsed."""
        return " ".join(self.argv)
