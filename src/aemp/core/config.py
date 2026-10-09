"""Configuration discovery and loading.

M0 scope: locate the project root, resolve the ``configs/`` tree, and load
YAML config files. Flow-spec parsing, run directories and storage are later
milestones and intentionally absent here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aemp.core.errors import ConfigurationError

ROOT_ENV_VAR = "AEMP_PROJECT_ROOT"
ROOT_MARKER = "pyproject.toml"


@dataclass(frozen=True)
class Settings:
    """Resolved, read-only view of the project layout.

    Attributes:
        project_root: Directory containing ``pyproject.toml`` (or the path
            given via the ``AEMP_PROJECT_ROOT`` environment variable).
    """

    project_root: Path

    @property
    def config_dir(self) -> Path:
        """Directory holding all platform configuration."""
        return self.project_root / "configs"

    @property
    def flow_dir(self) -> Path:
        """Directory of flow specs (consumed from M3 onwards)."""
        return self.config_dir / "flows"

    @property
    def rule_dir(self) -> Path:
        """Directory of QA rule packs (consumed from M5 onwards)."""
        return self.config_dir / "rules"

    @property
    def tool_config(self) -> Path:
        """Path of the tool lookup table used by ``aemp doctor``."""
        return self.config_dir / "tools" / "tools.yaml"

    @property
    def runs_dir(self) -> Path:
        """Directory holding all run data (source of truth; index only in SQLite)."""
        return self.project_root / "runs"


def find_project_root(start: Path, env: Mapping[str, str] | None = None) -> Path:
    """Locate the project root, searching upward from *start*.

    Resolution order:

    1. The ``AEMP_PROJECT_ROOT`` environment variable, if set to an
       existing directory.
    2. The nearest ancestor directory of *start* (including *start*
       itself) that contains a ``pyproject.toml`` marker file.

    Args:
        start: Directory or file path to search upward from.
        env: Environment mapping to consult; defaults to ``os.environ``
            (injectable for tests).

    Returns:
        The resolved project root directory.

    Raises:
        ConfigurationError: If no root can be resolved.
    """
    env_map = os.environ if env is None else env
    override = env_map.get(ROOT_ENV_VAR, "").strip()
    if override:
        root = Path(override).expanduser().resolve()
        if not root.is_dir():
            msg = f"{ROOT_ENV_VAR} does not point to an existing directory: {root}"
            raise ConfigurationError(msg)
        return root

    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ROOT_MARKER).is_file():
            return candidate
    msg = (
        f"Could not locate project root: no {ROOT_MARKER} found in or above {current}. "
        f"Set the {ROOT_ENV_VAR} environment variable to override."
    )
    raise ConfigurationError(msg)


def discover_settings(
    start: Path | None = None, env: Mapping[str, str] | None = None
) -> Settings:
    """Build :class:`Settings` for the current invocation.

    Args:
        start: Where to begin project-root discovery; defaults to the
            current working directory.
        env: Environment mapping; defaults to ``os.environ``.
    """
    root = find_project_root(start if start is not None else Path.cwd(), env)
    return Settings(project_root=root)


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its mapping content.

    An empty file loads as an empty mapping.

    Args:
        path: YAML file to read.

    Returns:
        The top-level mapping.

    Raises:
        ConfigurationError: If the file is missing, not valid YAML, or its
            top level is not a mapping.
    """
    if not path.is_file():
        msg = f"Config file not found: {path}"
        raise ConfigurationError(msg)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"Invalid YAML in {path}: {exc}"
        raise ConfigurationError(msg) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        type_name = type(data).__name__
        msg = f"Top level of {path} must be a mapping, got {type_name}"
        raise ConfigurationError(msg)
    return data
