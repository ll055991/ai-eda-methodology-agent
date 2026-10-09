"""Agent Backend configuration: environment-driven, nothing hardcoded."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PLATFORM_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_LOG_LEVEL = "INFO"

DEFAULT_AGENT_RUNTIME = "stub"
AGENT_RUNTIMES = ("stub", "llm")

DEFAULT_LLM_PROVIDER = "openai_compatible"
DEFAULT_LLM_TIMEOUT_S = 60.0

DEFAULT_MAX_TOOL_ROUNDS = 3

# M7.2 MCP: the Methodology MCP server is OPTIONAL. Disabled by default so
# the agent still starts (and works) with the local M6.4 tools only.
DEFAULT_MCP_ENABLED = False
DEFAULT_MCP_SERVER_NAME = "aemp-methodology"
DEFAULT_MCP_SERVER_URL = "http://127.0.0.1:8770/mcp"
DEFAULT_MCP_CONNECT_TIMEOUT_S = 5.0
DEFAULT_MCP_CALL_TIMEOUT_S = 30.0

_TRUE_VALUES = ("1", "true", "yes", "on")
_FALSE_VALUES = ("0", "false", "no", "off")


def default_contracts_dir() -> Path:
    """Shared contract schemas of the parent repository (never copied)."""
    # config.py -> agent_backend -> src -> agent-backend -> repo root
    return Path(__file__).resolve().parents[3] / "contracts"


def default_skills_dir() -> Path:
    """Skill content lives beside the package: ``agent-backend/skills``."""
    # config.py -> agent_backend -> src -> agent-backend
    return Path(__file__).resolve().parents[2] / "skills"


def _optional_float(env: Mapping[str, str], key: str) -> float | None:
    value = env.get(key, "").strip()
    return float(value) if value else None


def _optional_int(env: Mapping[str, str], key: str) -> int | None:
    value = env.get(key, "").strip()
    return int(value) if value else None


def _optional_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    """Parse a boolean environment flag; garbage fails loudly (never guessed)."""
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{key} must be one of {_TRUE_VALUES + _FALSE_VALUES}, got {raw!r}"
    )


@dataclass(frozen=True)
class MCPConfig:
    """MCP transport settings (M7.2).

    Disabled by default: an M6.4 deployment keeps working untouched. When
    enabled, the agent talks to the Methodology MCP server instead of the
    local platform tools - never both, and never as an automatic fallback.
    """

    enabled: bool = DEFAULT_MCP_ENABLED
    server_name: str = DEFAULT_MCP_SERVER_NAME
    server_url: str = DEFAULT_MCP_SERVER_URL
    connect_timeout_s: float = DEFAULT_MCP_CONNECT_TIMEOUT_S
    call_timeout_s: float = DEFAULT_MCP_CALL_TIMEOUT_S

    def __post_init__(self) -> None:
        if not self.server_name.strip():
            raise ValueError("mcp server_name must be a non-empty string")
        if not self.server_url.strip():
            raise ValueError("mcp server_url must be a non-empty URL")
        if self.connect_timeout_s <= 0:
            raise ValueError(
                f"mcp connect_timeout_s must be positive, got {self.connect_timeout_s}"
            )
        if self.call_timeout_s <= 0:
            raise ValueError(
                f"mcp call_timeout_s must be positive, got {self.call_timeout_s}"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MCPConfig:
        env_map = os.environ if env is None else env
        return cls(
            enabled=_optional_bool(env_map, "AEMP_MCP_ENABLED", DEFAULT_MCP_ENABLED),
            server_name=env_map.get("AEMP_MCP_SERVER_NAME", DEFAULT_MCP_SERVER_NAME),
            server_url=env_map.get("AEMP_MCP_SERVER_URL", DEFAULT_MCP_SERVER_URL),
            connect_timeout_s=float(
                env_map.get(
                    "AEMP_MCP_CONNECT_TIMEOUT_S", str(DEFAULT_MCP_CONNECT_TIMEOUT_S)
                )
            ),
            call_timeout_s=float(
                env_map.get("AEMP_MCP_CALL_TIMEOUT_S", str(DEFAULT_MCP_CALL_TIMEOUT_S))
            ),
        )


@dataclass(frozen=True)
class LLMConfig:
    """LLM settings; the API key comes from the environment only.

    ``base_url`` / ``api_key`` / ``model`` may be None here - the runtime
    factory rejects an incomplete ``llm`` configuration at startup with a
    clear error (never a silent fallback).
    """

    provider: str = DEFAULT_LLM_PROVIDER
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout_s: float = DEFAULT_LLM_TIMEOUT_S
    temperature: float | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.timeout_s <= 0:
            raise ValueError(f"llm timeout_s must be positive, got {self.timeout_s}")
        if self.temperature is not None and not (0.0 <= self.temperature <= 2.0):
            raise ValueError(
                f"llm temperature must be in [0, 2], got {self.temperature}"
            )
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError(f"llm max_tokens must be positive, got {self.max_tokens}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMConfig:
        env_map = os.environ if env is None else env
        return cls(
            provider=env_map.get("AEMP_LLM_PROVIDER", DEFAULT_LLM_PROVIDER),
            base_url=env_map.get("AEMP_LLM_BASE_URL") or None,
            api_key=env_map.get("AEMP_LLM_API_KEY") or None,
            model=env_map.get("AEMP_LLM_MODEL") or None,
            timeout_s=float(
                env_map.get("AEMP_LLM_TIMEOUT_S", str(DEFAULT_LLM_TIMEOUT_S))
            ),
            temperature=_optional_float(env_map, "AEMP_LLM_TEMPERATURE"),
            max_tokens=_optional_int(env_map, "AEMP_LLM_MAX_TOKENS"),
        )


@dataclass(frozen=True)
class AgentConfig:
    """Resolved Agent Backend settings.

    Attributes:
        platform_base_url: Platform API base URL.
        request_timeout_s: PlatformClient HTTP timeout (never infinite).
        log_level: logging level name.
        host / port: Agent HTTP server bind address.
        contracts_dir: shared contract directory (read-only).
    """

    platform_base_url: str = DEFAULT_PLATFORM_BASE_URL
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    log_level: str = DEFAULT_LOG_LEVEL
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    contracts_dir: Path = field(default_factory=default_contracts_dir)
    skills_dir: Path = field(default_factory=default_skills_dir)
    runtime: str = DEFAULT_AGENT_RUNTIME
    llm: LLMConfig = field(default_factory=LLMConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS

    def __post_init__(self) -> None:
        if not self.platform_base_url.strip():
            raise ValueError("platform_base_url must be a non-empty URL")
        if self.request_timeout_s <= 0:
            raise ValueError(
                f"request_timeout_s must be positive, got {self.request_timeout_s}"
            )
        if not (0 < self.port < 65536):
            raise ValueError(f"port must be in 1..65535, got {self.port}")
        if self.runtime not in AGENT_RUNTIMES:
            raise ValueError(
                f"runtime must be one of {AGENT_RUNTIMES}, got {self.runtime!r}"
            )
        if self.max_tool_rounds < 1:
            raise ValueError(
                f"max_tool_rounds must be >= 1, got {self.max_tool_rounds}"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AgentConfig:
        """Build the config from environment variables (with defaults).

        All environment reading lives here - no ``os.getenv`` elsewhere.
        """
        env_map = os.environ if env is None else env
        return cls(
            platform_base_url=env_map.get(
                "AEMP_PLATFORM_BASE_URL", DEFAULT_PLATFORM_BASE_URL
            ),
            request_timeout_s=float(
                env_map.get("AGENT_REQUEST_TIMEOUT_S", str(DEFAULT_REQUEST_TIMEOUT_S))
            ),
            log_level=env_map.get("AGENT_LOG_LEVEL", DEFAULT_LOG_LEVEL),
            host=env_map.get("AGENT_HOST", DEFAULT_HOST),
            port=int(env_map.get("AGENT_PORT", str(DEFAULT_PORT))),
            contracts_dir=Path(
                env_map.get("AEMP_CONTRACTS_DIR", str(default_contracts_dir()))
            ),
            skills_dir=Path(
                env_map.get("AEMP_SKILLS_DIR", str(default_skills_dir()))
            ),
            runtime=env_map.get("AEMP_AGENT_RUNTIME", DEFAULT_AGENT_RUNTIME),
            llm=LLMConfig.from_env(env_map),
            mcp=MCPConfig.from_env(env_map),
            max_tool_rounds=int(
                env_map.get("AEMP_AGENT_MAX_TOOL_ROUNDS", str(DEFAULT_MAX_TOOL_ROUNDS))
            ),
        )
