"""MCP server configuration (M7.2): environment-driven, nothing hardcoded.

Deployment convention used by the docs and the local demo (spec §38)::

    Platform Backend   :8765
    Agent Backend      :8769
    Methodology MCP    :8770

The MCP server is an adapter, so every value it needs is a *boundary*
value: where it is reachable (host/port), which Platform API it adapts
(base URL), how long it may wait, and which read-only knowledge roots it
may expose as resources. Nothing about EDA tools is configured here - the
MCP server can only ever reach the Platform HTTP API.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_MCP_HOST",
    "DEFAULT_MCP_PLATFORM_URL",
    "DEFAULT_MCP_PORT",
    "DEFAULT_MCP_REQUEST_TIMEOUT_S",
    "DEFAULT_SERVER_NAME",
    "DEFAULT_SERVER_VERSION",
    "MAX_RESOURCE_BYTES",
    "McpServerConfig",
]

DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8770
DEFAULT_MCP_PLATFORM_URL = "http://127.0.0.1:8765"
DEFAULT_MCP_REQUEST_TIMEOUT_S = 30.0

#: The server identity the MCP handshake reports (and the agent registry
#: matches on). ``DEFAULT_SERVER_VERSION`` is the version of the MCP
#: interface (tools/resources), deliberately independent of the platform
#: package version: the MCP surface is a contract of its own.
DEFAULT_SERVER_NAME = "aemp-methodology"
DEFAULT_SERVER_VERSION = "1.0.0"

#: A resource is read-only knowledge for a model context: bounded, always.
MAX_RESOURCE_BYTES = 64 * 1024

_SKILLS_RELATIVE = Path("agent-backend") / "skills"
_DOCS_RELATIVE = Path("docs")


def _optional_dir(env: Mapping[str, str], key: str, default: Path | None) -> Path | None:
    value = env.get(key, "").strip()
    if value:
        return Path(value).expanduser()
    return default


@dataclass(frozen=True)
class McpServerConfig:
    """Resolved MCP server settings.

    Attributes:
        server_name: Identity reported in the MCP handshake (the agent
            registry matches ``AEMP_MCP_SERVER_NAME`` against it).
        server_version: Version of the MCP interface this server publishes.
        host: Bind address of the MCP HTTP endpoint (never 0.0.0.0 unless
            the operator says so explicitly through the environment).
        port: Bind port of the MCP HTTP endpoint.
        platform_base_url: Base URL of the Platform HTTP API - the one and
            only capability source of this server.
        request_timeout_s: Timeout for one Platform HTTP request.
        skills_dir: Root of the read-only M7.1 skill content served as
            resources (``None`` disables skill resources).
        docs_dir: Root of the read-only methodology documents served as
            resources (``None`` disables document resources).
        max_resource_bytes: Hard size limit for any single resource.
    """

    server_name: str = DEFAULT_SERVER_NAME
    server_version: str = DEFAULT_SERVER_VERSION
    host: str = DEFAULT_MCP_HOST
    port: int = DEFAULT_MCP_PORT
    platform_base_url: str = DEFAULT_MCP_PLATFORM_URL
    request_timeout_s: float = DEFAULT_MCP_REQUEST_TIMEOUT_S
    skills_dir: Path | None = None
    docs_dir: Path | None = None
    max_resource_bytes: int = MAX_RESOURCE_BYTES

    def __post_init__(self) -> None:
        if not self.server_name.strip():
            raise ValueError("mcp server_name must be a non-empty string")
        if not self.server_version.strip():
            raise ValueError("mcp server_version must be a non-empty string")
        if not self.host.strip():
            raise ValueError("mcp host must be a non-empty string")
        if not (0 < self.port < 65536):
            raise ValueError(f"mcp port must be in 1..65535, got {self.port}")
        if not self.platform_base_url.strip():
            raise ValueError("mcp platform_base_url must be a non-empty URL")
        if self.request_timeout_s <= 0:
            raise ValueError(
                f"mcp request_timeout_s must be positive, got {self.request_timeout_s}"
            )
        if self.max_resource_bytes <= 0:
            raise ValueError(
                f"mcp max_resource_bytes must be positive, got {self.max_resource_bytes}"
            )

    @property
    def platform_api_url(self) -> str:
        """Platform base URL without a trailing slash."""
        return self.platform_base_url.rstrip("/")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        project_root: Path | None = None,
    ) -> McpServerConfig:
        """Build the config from the environment (with documented defaults)."""
        env_map = os.environ if env is None else env
        root = Path(project_root) if project_root is not None else None
        default_skills = (root / _SKILLS_RELATIVE) if root is not None else None
        default_docs = (root / _DOCS_RELATIVE) if root is not None else None
        return cls(
            server_name=env_map.get("AEMP_MCP_SERVER_NAME", DEFAULT_SERVER_NAME),
            server_version=env_map.get(
                "AEMP_MCP_SERVER_VERSION", DEFAULT_SERVER_VERSION
            ),
            host=env_map.get("AEMP_MCP_HOST", DEFAULT_MCP_HOST),
            port=int(env_map.get("AEMP_MCP_PORT", str(DEFAULT_MCP_PORT))),
            platform_base_url=env_map.get(
                "AEMP_MCP_PLATFORM_URL", DEFAULT_MCP_PLATFORM_URL
            ),
            request_timeout_s=float(
                env_map.get(
                    "AEMP_MCP_REQUEST_TIMEOUT_S", str(DEFAULT_MCP_REQUEST_TIMEOUT_S)
                )
            ),
            skills_dir=_optional_dir(env_map, "AEMP_MCP_SKILLS_DIR", default_skills),
            docs_dir=_optional_dir(env_map, "AEMP_MCP_DOCS_DIR", default_docs),
            max_resource_bytes=int(
                env_map.get("AEMP_MCP_MAX_RESOURCE_BYTES", str(MAX_RESOURCE_BYTES))
            ),
        )
