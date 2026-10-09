"""Docker execution backend: runs containerized tools via the docker CLI.

Docker is NOT modelled as an EDA tool - it is the execution backend
(AGENTS.md §13): the same role a native Linux shell or an Apptainer
executor could play later. The backend reuses M2.1's
:class:`aemp.drivers.base.ToolDriver` for ALL subprocess governance, so
``docker run`` itself is executed under exactly the same rules as any EDA
tool binary (argv-only, bounded capture, hard timeout, process-group kill).

Classification discipline (never "everything is Execution failed"):

- docker CLI missing                 -> ToolNotFoundError (M2.1 argv resolution)
- daemon unreachable                 -> DockerDaemonUnavailableError (pre-flight)
- image missing                      -> DockerImageMissingError (pre-flight)
- container command exits nonzero    -> ToolExecutionResult, status=failed
- timeout                            -> ToolExecutionResult, timed_out=True,
                                        plus a best-effort ``docker rm -f``
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex

from aemp.core.errors import AEMPError, InvalidRequestError
from aemp.core.logging import get_logger
from aemp.drivers.base import ToolDriver
from aemp.drivers.errors import DockerDaemonUnavailableError, DockerImageMissingError
from aemp.drivers.models import ToolExecutionRequest, ToolExecutionResult

logger = get_logger(__name__)

_CONTAINER_NAME_PREFIX = "aemp-"
_CLEANUP_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class ContainerMount:
    """One host<->container path mapping for ``docker run -v``.

    The host path is normalized to POSIX form (``E:/repo`` style), which
    Docker Desktop on Windows accepts and which avoids shell-specific path
    conversion; container paths are always POSIX.
    """

    host_path: Path
    container_path: str
    read_only: bool = False

    def to_arg(self) -> str:
        mode = ":ro" if self.read_only else ""
        return f"{self.host_path.as_posix()}:{self.container_path}{mode}"


class DockerToolExecutor:
    """Runs container commands through the M2.1 governance layer.

    The executor owns everything Docker-specific: the CLI executable,
    ``docker run`` argv construction, volume mounts, the container working
    directory, ``--rm`` lifecycle, and post-timeout container cleanup.
    It does NOT know about any specific EDA tool.
    """

    def __init__(
        self,
        *,
        docker_executable: str = "docker",
        driver: ToolDriver | None = None,
    ) -> None:
        self._driver = driver if driver is not None else ToolDriver(
            tool_name="docker", executable=docker_executable
        )

    def check_daemon(self, timeout_s: float = 30.0) -> None:
        """Verify the Docker daemon is reachable.

        Raises:
            DockerDaemonUnavailableError: If ``docker version`` fails (the
                CLI itself was already resolved by M2.1, so any failure
                here means the daemon/engine is not usable).
        """
        result = self._driver.execute(
            ToolExecutionRequest(
                tool_name="docker",
                argv=("docker", "version", "--format", "{{.Server.Version}}"),
                timeout_s=timeout_s,
            )
        )
        if not result.succeeded:
            detail = (result.stderr.strip() or result.stdout.strip())[:200]
            msg = f"Docker daemon not reachable: {detail}"
            raise DockerDaemonUnavailableError(msg)

    def image_exists(self, image: str, timeout_s: float = 30.0) -> bool:
        """Return True when *image* exists in the local image store."""
        result = self._driver.execute(
            ToolExecutionRequest(
                tool_name="docker",
                argv=("docker", "image", "inspect", image),
                timeout_s=timeout_s,
            )
        )
        return result.succeeded

    def run(
        self,
        *,
        image: str,
        command: tuple[str, ...],
        mounts: Sequence[ContainerMount] = (),
        workdir: str | None = None,
        timeout_s: float = 300.0,
    ) -> ToolExecutionResult:
        """Run one container command (``--rm``, unique ``--name``, pre-flight).

        Args:
            image: Container image reference (version-pinned, never latest).
            command: Container-side argv, e.g. ``("yosys", "-s", "synth.ys")``.
            mounts: Host<->container path mappings.
            workdir: Container working directory (``docker run -w``).
            timeout_s: Hard wall-clock bound; on expiry the docker client is
                killed by M2.1 governance and the container is removed with
                a best-effort ``docker rm -f`` (logged, never silent).

        Returns:
            The M2.1 result for the governed docker process. For container
            runs, ``executable_path`` is the HOST docker CLI and the
            container command is visible in ``argv`` after the image
            reference; the container exit code is propagated as
            ``exit_code``.

        Raises:
            DockerDaemonUnavailableError: Pre-flight daemon check failed.
            DockerImageMissingError: Pre-flight image check failed.
            InvalidRequestError: Empty image or malformed command.
        """
        if not image or not image.strip():
            msg = "image must be a non-empty string"
            raise InvalidRequestError(msg)
        if not command or not all(isinstance(part, str) and part for part in command):
            msg = "command must be a non-empty sequence of strings"
            raise InvalidRequestError(msg)

        self.check_daemon()
        if not self.image_exists(image):
            msg = f"Image not found locally: {image} (build it or fix the tag)"
            raise DockerImageMissingError(msg)

        container_name = f"{_CONTAINER_NAME_PREFIX}{token_hex(4)}"
        argv: list[str] = ["docker", "run", "--rm", "--name", container_name]
        for mount in mounts:
            argv += ["-v", mount.to_arg()]
        if workdir is not None:
            argv += ["-w", workdir]
        argv += [image, *command]

        result = self._driver.execute(
            ToolExecutionRequest(
                tool_name="docker", argv=tuple(argv), timeout_s=timeout_s
            )
        )
        if result.timed_out:
            self._cleanup_container(container_name)
        return result

    def _cleanup_container(self, container_name: str) -> None:
        """Best-effort ``docker rm -f`` after a client-side timeout kill.

        Killing the docker CLI does not stop the container (the daemon owns
        it), so the executor removes it by the unique name it assigned.
        Failures are logged, never raised: the timed-out result is the
        primary fact and must not be masked by cleanup problems.
        """
        try:
            result = self._driver.execute(
                ToolExecutionRequest(
                    tool_name="docker",
                    argv=("docker", "rm", "-f", container_name),
                    timeout_s=_CLEANUP_TIMEOUT_S,
                )
            )
            if not result.succeeded:
                logger.warning(
                    "container cleanup %s: %s",
                    container_name,
                    (result.stderr.strip() or "non-zero exit")[:200],
                )
            else:
                logger.info("cleaned up container %s after timeout", container_name)
        except AEMPError as exc:
            logger.warning("container cleanup %s failed: %s", container_name, exc)
