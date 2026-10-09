"""Driver-layer errors: the execution BACKEND is not usable.

Distinct on purpose from the M2.1 semantics (AGENTS.md §11):

- :class:`aemp.core.errors.ToolNotFoundError` - the tool program could not
  be resolved on the host;
- :class:`ToolEnvironmentError` (this module) - the backend that would run
  the tool (e.g. the Docker daemon or a required image) is unavailable;
- nonzero tool exit codes and timeouts are NOT exceptions - they are
  recorded :class:`aemp.drivers.models.ToolExecutionResult` values.
"""

from __future__ import annotations

from aemp.core.errors import AEMPError

__all__ = [
    "DockerDaemonUnavailableError",
    "DockerImageMissingError",
    "ToolEnvironmentError",
]


class ToolEnvironmentError(AEMPError):
    """The execution backend required to run a tool is not usable right now."""


class DockerDaemonUnavailableError(ToolEnvironmentError):
    """The Docker daemon is not reachable (Docker Desktop not running, etc.)."""


class DockerImageMissingError(ToolEnvironmentError):
    """The required container image does not exist in the local image store."""
