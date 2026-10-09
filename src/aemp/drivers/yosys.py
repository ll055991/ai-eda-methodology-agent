"""YosysDriver: synthesis tool driver (M2.3, refactored onto the shared
container skeleton in M2.5 when OpenSTADriver arrived).

Runs Yosys inside the ``aemp-eda-base`` container through the Docker
execution backend. Responsibilities:

- Yosys tool identity: image, container workdir, official version probe;
- workspace boundary: ONE workspace directory per execution, mounted at
  ``container_workdir``; the Yosys script must live INSIDE the workspace
  (enforced - a script outside the mount is invisible to the container);
- container command construction (``yosys -s <script>``), delegated to
  :class:`aemp.drivers.docker.DockerToolExecutor`.

It returns M2.1 :class:`aemp.drivers.models.ToolExecutionResult` values -
there is deliberately no separate "YosysResult" type. For these results,
``executable_path`` is the HOST docker CLI (the governed process) and the
container command is visible in ``argv``; container-side Yosys identity
comes from :meth:`get_version`.
"""

from __future__ import annotations

from aemp.drivers.container import ContainerToolDriver

__all__ = ["YosysDriver"]


class YosysDriver(ContainerToolDriver):
    """Drives Yosys synthesis inside the pinned EDA container (M2.3)."""

    default_binary = "yosys"
    default_version_args = ("-V",)
    _script_flags = ("-s",)
