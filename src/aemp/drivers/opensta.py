"""OpenSTADriver: static timing analysis tool driver (M2.5).

Runs OpenSTA inside the ``aemp-eda-opensta`` container (pinned OpenSTA
3.1.0 image) through the Docker execution backend. Built on the same
shared container skeleton as :class:`aemp.drivers.yosys.YosysDriver` -
one Request/Result vocabulary, one workspace boundary, one version-probe
discipline.

OpenSTA specifics (all verified against the pinned image):
- binary ``sta``; invoked as ``sta -no_splash -exit <script>`` - the
  script is a POSITIONAL cmd_file argument (there is no ``-f`` flag in
  3.1.0; the upstream image's relative-path ENTRYPOINT is cleared by the
  wrapper Dockerfile so the plain command form works);
- ``sta -version`` prints ``3.1.0`` on stdout and exits 0;
- OpenSTA writes its timing reports to STDOUT (there is no ``redirect``
  command in 3.1.0), so callers archive the captured stdout as the
  timing report artifact.
"""

from __future__ import annotations

from aemp.drivers.container import ContainerToolDriver

__all__ = ["OpenSTADriver"]


class OpenSTADriver(ContainerToolDriver):
    """Drives OpenSTA timing analysis inside the pinned container (M2.5)."""

    default_binary = "sta"
    default_version_args = ("-version",)
    _script_flags = ("-no_splash", "-exit")
