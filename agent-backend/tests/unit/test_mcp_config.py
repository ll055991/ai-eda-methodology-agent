"""MCP configuration tests (M7.2).

MCP is optional and off by default: a M6.4 deployment must keep working
without a single new environment variable. Garbage flag values fail loudly
(never guessed).
"""

import pytest

from agent_backend.config import (
    DEFAULT_MCP_SERVER_NAME,
    DEFAULT_MCP_SERVER_URL,
    AgentConfig,
    MCPConfig,
)


def test_defaults_keep_mcp_disabled() -> None:
    config = MCPConfig()

    assert config.enabled is False
    assert config.server_name == DEFAULT_MCP_SERVER_NAME
    assert config.server_url == DEFAULT_MCP_SERVER_URL
    assert config.connect_timeout_s > 0
    assert config.call_timeout_s > 0


def test_agent_config_reads_mcp_settings() -> None:
    config = AgentConfig.from_env(
        {
            "AEMP_MCP_ENABLED": "true",
            "AEMP_MCP_SERVER_URL": "http://127.0.0.1:9999/mcp",
            "AEMP_MCP_CONNECT_TIMEOUT_S": "1.5",
            "AEMP_MCP_CALL_TIMEOUT_S": "9",
        }
    )

    assert config.mcp.enabled is True
    assert config.mcp.server_url == "http://127.0.0.1:9999/mcp"
    assert config.mcp.connect_timeout_s == 1.5
    assert config.mcp.call_timeout_s == 9.0


def test_agent_config_defaults_to_local_tools() -> None:
    assert AgentConfig.from_env({}).mcp.enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_true_flags(raw: str) -> None:
    assert MCPConfig.from_env({"AEMP_MCP_ENABLED": raw}).enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off"])
def test_false_flags(raw: str) -> None:
    assert MCPConfig.from_env({"AEMP_MCP_ENABLED": raw}).enabled is False


def test_garbage_flag_fails_loudly() -> None:
    with pytest.raises(ValueError):
        MCPConfig.from_env({"AEMP_MCP_ENABLED": "maybe"})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"server_url": ""},
        {"server_name": ""},
        {"connect_timeout_s": 0},
        {"call_timeout_s": -1},
    ],
)
def test_invalid_settings_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        MCPConfig(**kwargs)
