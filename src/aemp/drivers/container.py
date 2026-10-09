"""ContainerToolDriver: shared skeleton for container-backed tool drivers.

M2.5 extracted this from YosysDriver when OpenSTADriver arrived - the
workspace boundary, version probing and executor delegation were about to
be duplicated per tool. Public APIs stay stable: constructors keep their
per-tool defaults (``default_binary``/``default_version_args``),
``from_config`` reads the same YAML keys, and YosysDriver's behavior is
guarded by its M2.3 tests.

Version-probe honesty rule: the probe parses the first non-empty STDOUT
line as the tool's self-reported version - stderr is never parsed as a
version (error text is not a version), and an empty stdout yields
``UNKNOWN``. The rule is applied regardless of exit code, so tools whose
version output accompanies a nonzero exit still report honestly.
Environment problems (daemon/image) raise before any parsing (M2.3
pre-flight), so ``UNKNOWN`` only ever means "the tool ran but printed
nothing parseable on stdout".
"""

from __future__ import annotations

from pathlib import Path

from aemp.core.config import load_yaml
from aemp.core.errors import InvalidRequestError
from aemp.core.logging import get_logger
from aemp.doctor import VERSION_UNKNOWN
from aemp.drivers.docker import ContainerMount, DockerToolExecutor
from aemp.drivers.models import ToolExecutionResult

logger = get_logger(__name__)

_DEFAULT_WORKDIR = "/workspace"


class ContainerToolDriver:
    """Base for drivers that run one specific tool inside a container.

    Subclasses declare:
        ``default_binary``  the tool's program name inside the container
        ``_script_flags``   flags preceding the script path on invocation
                            (yosys: ``-s``; opensta: ``-no_splash -exit -f``)

    Args:
        image: Version-pinned container image reference (never latest).
        binary: Optional override of the tool's program name.
        container_workdir: Working directory inside the container; the
            workspace is mounted exactly here.
        version_args: Arguments of the tool's official version command;
            defaults to the subclass's ``default_version_args``.
        executor: Execution backend; defaults to a real
            :class:`DockerToolExecutor` (inject a fake in unit tests).
    """

    default_binary: str = ""
    default_version_args: tuple[str, ...] = ()
    _script_flags: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        image: str,
        binary: str | None = None,
        container_workdir: str = _DEFAULT_WORKDIR,
        version_args: tuple[str, ...] | None = None,
        executor: DockerToolExecutor | None = None,
    ) -> None:
        binary = binary if binary is not None else self.default_binary
        version_args = (
            self.default_version_args if version_args is None else tuple(version_args)
        )
        if not image or not image.strip():
            msg = "image must be a non-empty string"
            raise InvalidRequestError(msg)
        if not binary or not binary.strip():
            msg = "binary must be a non-empty string (declare default_binary or pass binary)"
            raise InvalidRequestError(msg)
        if not container_workdir or not container_workdir.strip():
            msg = "container_workdir must be a non-empty string"
            raise InvalidRequestError(msg)
        self.image = image
        self.binary = binary
        self.container_workdir = container_workdir
        self.version_args = tuple(version_args)
        self._executor = executor if executor is not None else DockerToolExecutor()

    @classmethod
    def from_config(
        cls, path: Path, *, executor: DockerToolExecutor | None = None
    ) -> ContainerToolDriver:
        """Build a driver from a config file (``configs/tools/<tool>.yaml``).

        Expected shape::

            name: <label>
            executor: docker
            image: <pinned image reference>
            binary: <program name inside the container>
            container_workdir: /workspace
            version_args: ["-version"]
        """
        data = load_yaml(Path(path))
        image = data.get("image")
        if not isinstance(image, str) or not image:
            msg = f"{path}: 'image' must be a non-empty string"
            raise InvalidRequestError(msg)
        container_workdir = data.get("container_workdir", _DEFAULT_WORKDIR)
        version_args = data.get("version_args", [])
        if not isinstance(container_workdir, str) or not container_workdir:
            msg = f"{path}: 'container_workdir' must be a non-empty string"
            raise InvalidRequestError(msg)
        if not isinstance(version_args, list) or not all(
            isinstance(arg, str) for arg in version_args
        ):
            msg = f"{path}: 'version_args' must be a list of strings"
            raise InvalidRequestError(msg)
        return cls(
            image=image,
            binary=data.get("binary"),
            container_workdir=container_workdir,
            version_args=tuple(version_args) if version_args else None,
            executor=executor,
        )

    def get_version(self, timeout_s: float = 120.0) -> str:
        """Return the first line of the tool's real in-container version output.

        Parsed from stdout regardless of exit code (see the honesty rule in
        the module docstring); ``UNKNOWN`` when stdout has no parseable line.

        Raises:
            DockerDaemonUnavailableError: Docker daemon not reachable.
            DockerImageMissingError: Image not in the local store.
        """
        result = self._executor.run(
            image=self.image,
            command=(self.binary, *self.version_args),
            mounts=(),
            workdir=None,
            timeout_s=timeout_s,
        )
        if result.stdout.strip():
            for line in result.stdout.splitlines():
                if line.strip():
                    return line.strip()
        logger.warning(
            "%s version probe produced no parseable output (exit_code=%s): %s",
            self.binary,
            result.exit_code,
            result.stderr.strip()[:200],
        )
        return VERSION_UNKNOWN

    def execute(
        self, *, workspace: Path, command: tuple[str, ...], timeout_s: float = 300.0
    ) -> ToolExecutionResult:
        """Run an arbitrary container command with *workspace* mounted.

        Command paths must be container-relative to the workdir.
        """
        workspace_path = Path(workspace)
        if not workspace_path.is_dir():
            msg = f"Workspace does not exist: {workspace_path}"
            raise InvalidRequestError(msg)
        if not command or not all(isinstance(part, str) and part for part in command):
            msg = "command must be a non-empty sequence of strings"
            raise InvalidRequestError(msg)
        return self._executor.run(
            image=self.image,
            command=command,
            mounts=(
                ContainerMount(
                    host_path=workspace_path, container_path=self.container_workdir
                ),
            ),
            workdir=self.container_workdir,
            timeout_s=timeout_s,
        )

    def run_script(
        self, *, workspace: Path, script: Path, timeout_s: float = 300.0
    ) -> ToolExecutionResult:
        """Run the tool's script mode with *workspace* mounted as the workdir.

        The workspace is the execution boundary: the script and every file
        it references must live inside it (the container cannot see
        anything else). Artifacts the script writes land in the workspace,
        i.e. on the host - the container itself is ephemeral (``--rm``).

        Raises:
            InvalidRequestError: Workspace/script missing, or script
                outside the workspace boundary.
        """
        workspace_path = Path(workspace)
        script_path = Path(script)
        if not workspace_path.is_dir():
            msg = f"Workspace does not exist: {workspace_path}"
            raise InvalidRequestError(msg)
        if not script_path.is_file():
            msg = f"Script does not exist: {script_path}"
            raise InvalidRequestError(msg)
        container_script = _container_relative(script_path, workspace_path)
        return self.execute(
            workspace=workspace_path,
            command=(self.binary, *self._script_flags, container_script),
            timeout_s=timeout_s,
        )


def _container_relative(script: Path, workspace: Path) -> str:
    """Container-side path of *script* relative to the workspace mount.

    Raises:
        InvalidRequestError: If the script lies outside the workspace
            boundary (it would be invisible inside the container).
    """
    try:
        return script.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        msg = (
            f"Script must live inside the workspace "
            f"(execution boundary): {script} is not under {workspace}"
        )
        raise InvalidRequestError(msg) from None
