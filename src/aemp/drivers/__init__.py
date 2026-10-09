"""EDA tool driver layer.

M2.1/M2.2/M2.3 scope:

- :class:`aemp.drivers.base.ToolDriver` - the shared subprocess governance
  every tool execution goes through (bounded capture, timeouts, argv-only);
- :class:`aemp.drivers.docker.DockerToolExecutor` - the container execution
  BACKEND (Docker is the environment, not a tool);
- :class:`aemp.drivers.yosys.YosysDriver` - the first concrete tool driver.

Drivers report execution facts only (argv, exit code, duration, captured
output). They NEVER judge engineering quality - that is the job of parsers
(M4) and the QA engine (M5).
"""

from aemp.drivers.base import ToolDriver
from aemp.drivers.container import ContainerToolDriver
from aemp.drivers.docker import ContainerMount, DockerToolExecutor
from aemp.drivers.errors import (
    DockerDaemonUnavailableError,
    DockerImageMissingError,
    ToolEnvironmentError,
)
from aemp.drivers.models import (
    EXECUTION_STATUS_FAILED,
    EXECUTION_STATUS_SUCCESS,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from aemp.drivers.opensta import OpenSTADriver
from aemp.drivers.yosys import YosysDriver

__all__ = [
    "EXECUTION_STATUS_FAILED",
    "EXECUTION_STATUS_SUCCESS",
    "ContainerMount",
    "ContainerToolDriver",
    "DockerDaemonUnavailableError",
    "DockerImageMissingError",
    "DockerToolExecutor",
    "OpenSTADriver",
    "ToolDriver",
    "ToolEnvironmentError",
    "ToolExecutionRequest",
    "ToolExecutionResult",
    "YosysDriver",
]
