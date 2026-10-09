"""Provider factory: the ONLY place that maps config to a concrete provider.

No fallback, no vendor guessing: unknown providers and missing
configuration raise :class:`LLMConfigurationError` at startup.
"""

from __future__ import annotations

from agent_backend.config import LLMConfig
from agent_backend.llm.base import ModelProvider
from agent_backend.llm.errors import LLMConfigurationError
from agent_backend.llm.openai_compatible import OpenAICompatibleProvider

__all__ = ["PROVIDER_OPENAI_COMPATIBLE", "create_model_provider"]

PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"


def create_model_provider(config: LLMConfig) -> ModelProvider:
    """Build the provider named by ``config.provider``.

    Raises:
        LLMConfigurationError: Unknown provider, or missing base URL /
            API key / model. Never falls back to another provider.
    """
    if config.provider != PROVIDER_OPENAI_COMPATIBLE:
        raise LLMConfigurationError(
            f"unknown LLM provider {config.provider!r} "
            f"(only {PROVIDER_OPENAI_COMPATIBLE!r} is supported)"
        )
    missing = [
        variable
        for variable, value in (
            ("AEMP_LLM_BASE_URL", config.base_url),
            ("AEMP_LLM_API_KEY", config.api_key),
            ("AEMP_LLM_MODEL", config.model),
        )
        if not value or not str(value).strip()
    ]
    if missing:
        raise LLMConfigurationError(
            "missing LLM configuration: " + ", ".join(missing)
        )
    assert config.base_url is not None and config.api_key is not None
    return OpenAICompatibleProvider(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_s=config.timeout_s,
    )
