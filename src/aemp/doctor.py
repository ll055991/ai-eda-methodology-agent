"""``aemp doctor`` - environment diagnosis.

M0 scope: locate EDA tool executables on PATH and probe their versions via
each tool's official version command. No drivers, no flows, no EDA
workloads.

Version discipline (the basis of the later tool contract): a version is
either what the tool itself reports, or ``UNKNOWN``. It is never guessed
from file names, package metadata, or partial output.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from aemp.core.config import Settings, load_yaml
from aemp.core.errors import ConfigurationError

PROBE_TIMEOUT_S = 30.0
_MAX_VERSION_CHARS = 200

STATUS_OK = "OK"
STATUS_MISSING = "MISSING"
VERSION_UNKNOWN = "UNKNOWN"
OVERALL_READY = "READY"
OVERALL_NOT_READY = "NOT READY"


@dataclass(frozen=True)
class ToolSpec:
    """One EDA tool as declared in ``configs/tools/tools.yaml``.

    Attributes:
        name: Config key, e.g. ``yosys``.
        executable: Executable name to resolve on PATH.
        version_args: Arguments of the tool's official version command.
    """

    name: str
    executable: str
    version_args: tuple[str, ...]


@dataclass(frozen=True)
class ToolReport:
    """Diagnosis result for a single tool.

    Attributes:
        name: Config key of the tool.
        path: Resolved executable path, or ``None`` when not found.
        status: ``OK`` (executable located) or ``MISSING``.
        version: Self-reported version string, or ``UNKNOWN`` when the
            probe failed; empty for missing tools.
    """

    name: str
    path: Path | None
    status: str
    version: str


@dataclass(frozen=True)
class DoctorReport:
    """Full environment diagnosis produced by :func:`run_doctor`."""

    python_version: str
    os_name: str
    architecture: str
    tools: tuple[ToolReport, ...]

    @property
    def overall_status(self) -> str:
        """``READY`` only when every declared tool was located."""
        if all(tool.status == STATUS_OK for tool in self.tools):
            return OVERALL_READY
        return OVERALL_NOT_READY


def load_tool_specs(path: Path) -> tuple[ToolSpec, ...]:
    """Load tool declarations from *path* (``configs/tools/tools.yaml``).

    Expected shape::

        tools:
          <name>:
            executable: <string>
            version_args: [<string>, ...]

    Each tool is configured independently on purpose: version command
    conventions differ per tool, and the table avoids if/else chains in
    code.

    Raises:
        ConfigurationError: On a missing file or malformed content.
    """
    data = load_yaml(path)
    tools = data.get("tools")
    if not isinstance(tools, dict) or not tools:
        msg = f"{path} must contain a non-empty 'tools' mapping"
        raise ConfigurationError(msg)

    specs: list[ToolSpec] = []
    for name, entry in tools.items():
        if not isinstance(entry, dict):
            msg = f"{path}: tool '{name}' must be a mapping"
            raise ConfigurationError(msg)
        executable = entry.get("executable")
        version_args = entry.get("version_args", [])
        if not isinstance(executable, str) or not executable:
            msg = f"{path}: tool '{name}' needs a non-empty 'executable' string"
            raise ConfigurationError(msg)
        if not isinstance(version_args, list) or not all(
            isinstance(arg, str) for arg in version_args
        ):
            msg = f"{path}: tool '{name}' 'version_args' must be a list of strings"
            raise ConfigurationError(msg)
        specs.append(
            ToolSpec(name=str(name), executable=executable, version_args=tuple(version_args))
        )
    return tuple(specs)


def find_executable(executable: str) -> Path | None:
    """Return the resolved executable path, or ``None`` if not on PATH."""
    found = shutil.which(executable)
    return Path(found) if found else None


def probe_version(
    executable: Path,
    version_args: tuple[str, ...],
    timeout: float = PROBE_TIMEOUT_S,
) -> str:
    """Run the tool's official version command and return its first output line.

    Returns:
        The first non-empty line of stdout (falling back to stderr),
        truncated to a sane length, or ``UNKNOWN`` when the probe fails,
        times out, or produces no parseable output.
    """
    try:
        completed = subprocess.run(
            [str(executable), *version_args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return VERSION_UNKNOWN

    for stream in (completed.stdout, completed.stderr):
        for line in (stream or "").splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:_MAX_VERSION_CHARS]
    return VERSION_UNKNOWN


def check_tool(spec: ToolSpec) -> ToolReport:
    """Locate one tool and best-effort probe its version."""
    path = find_executable(spec.executable)
    if path is None:
        return ToolReport(name=spec.name, path=None, status=STATUS_MISSING, version="")
    version = probe_version(path, spec.version_args)
    return ToolReport(name=spec.name, path=path, status=STATUS_OK, version=version)


def collect_python_version() -> str:
    """Return the running interpreter version as ``major.minor.micro``."""
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def collect_platform() -> tuple[str, str]:
    """Return ``(os_name, architecture)`` for the running interpreter."""
    return platform.system(), platform.machine()


def run_doctor(settings: Settings) -> DoctorReport:
    """Execute all M0 environment checks.

    Raises:
        ConfigurationError: If the tool configuration cannot be loaded.
    """
    specs = load_tool_specs(settings.tool_config)
    tools = tuple(check_tool(spec) for spec in specs)
    os_name, architecture = collect_platform()
    return DoctorReport(
        python_version=collect_python_version(),
        os_name=os_name,
        architecture=architecture,
        tools=tools,
    )


def _field(label: str, value: str, indent: int, width: int) -> str:
    """Format one ``<indent><label>: <value>`` line with aligned colons."""
    return f"{' ' * indent}{label.ljust(width)}: {value}"


def render_report(report: DoctorReport) -> str:
    """Render the doctor report in the documented plain-text layout."""
    lines = [
        "AEMP Environment Doctor",
        "=======================",
        "",
        "Python",
        _field("Version", report.python_version, indent=2, width=13),
        _field("Status", STATUS_OK, indent=2, width=13),
        "",
        "Platform",
        _field("OS", report.os_name, indent=2, width=13),
        _field("Architecture", report.architecture, indent=2, width=13),
        "",
        "EDA Tools",
        "",
    ]
    for tool in report.tools:
        lines.append(f"  {tool.name}")
        if tool.path is None:
            lines.append(_field("Path", "NOT FOUND", indent=4, width=11))
            lines.append(_field("Status", tool.status, indent=4, width=11))
        else:
            lines.append(_field("Path", str(tool.path), indent=4, width=11))
            lines.append(_field("Version", tool.version, indent=4, width=11))
            lines.append(_field("Status", tool.status, indent=4, width=11))
        lines.append("")
    lines.append("Overall")
    lines.append(_field("Status", report.overall_status, indent=2, width=13))
    return "\n".join(lines)
