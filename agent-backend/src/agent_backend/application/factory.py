"""Runtime factory: the ONLY place that maps config to a runtime.

The HTTP layer calls :func:`create_agent_runtime`; it never knows which
provider or runtime exists. No fallback: selecting ``llm`` without valid
LLM configuration raises :class:`LLMConfigurationError` at startup.

M7.2 adds exactly one more choice, made in the same place: the tool
transport. ``AEMP_MCP_ENABLED=true`` builds the MCP gateway, otherwise the
M6.4 local platform tools are used. The two are never mixed automatically.
"""

from __future__ import annotations

from agent_backend.application.approval import ApprovalManager
from agent_backend.application.chat import AgentRuntime, StubAgentRuntime
from agent_backend.application.langgraph_runtime import (
    LangGraphAgentRuntime,
    default_checkpointer,
)
from agent_backend.config import AgentConfig
from agent_backend.llm.errors import LLMConfigurationError
from agent_backend.llm.factory import create_model_provider
from agent_backend.logging import get_logger
from agent_backend.mcp import build_mcp_gateway
from agent_backend.platform.client import PlatformClient
from agent_backend.policy import PolicyEngine
from agent_backend.skills import SkillLoader, SkillRegistry, SkillService
from agent_backend.tools.gateway import RegistryToolGateway, ToolGateway
from agent_backend.tools.platform import build_platform_registry

__all__ = ["RUNTIME_LLM", "RUNTIME_STUB", "create_agent_runtime"]

RUNTIME_STUB = "stub"
RUNTIME_LLM = "llm"

logger = get_logger(__name__)


def _build_skill_service(config: AgentConfig) -> SkillService:
    """Skill content (data) + skill runtime (code), strictly separated."""
    registry = SkillRegistry(config.skills_dir)
    return SkillService(registry, SkillLoader(config.skills_dir))


def _build_tool_gateway(config: AgentConfig) -> ToolGateway:
    """The runtime's capability transport: MCP when enabled, local otherwise.

    The runtime receives the gateway, never a client or a URL. Selecting MCP
    is explicit configuration; there is NO fallback to the local tools when
    the MCP server is unavailable (MCP errors stay visible, spec §35).
    """
    if config.mcp.enabled:
        logger.info(
            "tool transport: mcp server=%s url=%s",
            config.mcp.server_name,
            config.mcp.server_url,
        )
        return build_mcp_gateway(config.mcp)

    logger.info("tool transport: local platform tools url=%s", config.platform_base_url)
    client = PlatformClient(
        config.platform_base_url, timeout_s=config.request_timeout_s
    )
    return RegistryToolGateway(build_platform_registry(client))


def create_agent_runtime(config: AgentConfig) -> AgentRuntime:
    """Build the runtime named by ``config.runtime``.

    Raises:
        LLMConfigurationError: Unknown runtime, or ``llm`` with missing
            provider configuration. Never falls back to the stub.
    """
    if config.runtime == RUNTIME_STUB:
        return StubAgentRuntime()
    if config.runtime == RUNTIME_LLM:
        if not config.llm.model:
            raise LLMConfigurationError(
                "AEMP_LLM_MODEL is required for the llm runtime"
            )
        provider = create_model_provider(config.llm)
        return LangGraphAgentRuntime(
            provider=provider,
            model=config.llm.model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            gateway=_build_tool_gateway(config),
            max_tool_rounds=config.max_tool_rounds,
            policy=PolicyEngine.default(),
            approvals=ApprovalManager(),
            checkpointer=default_checkpointer(),
            skills=_build_skill_service(config),
        )
    raise LLMConfigurationError(
        f"unknown runtime {config.runtime!r} (expected 'stub' or 'llm')"
    )
