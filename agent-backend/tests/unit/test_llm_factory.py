"""Provider / runtime factory tests: explicit errors, never fallback."""

import pytest

from agent_backend.application.chat import LLMAgentRuntime, StubAgentRuntime
from agent_backend.application.factory import create_agent_runtime
from agent_backend.application.langgraph_runtime import LangGraphAgentRuntime
from agent_backend.config import AgentConfig, LLMConfig
from agent_backend.llm.errors import LLMConfigurationError
from agent_backend.llm.factory import create_model_provider
from agent_backend.llm.openai_compatible import OpenAICompatibleProvider

FULL_LLM = LLMConfig(
    provider="openai_compatible",
    base_url="http://llm.test/v1",
    api_key="test-key",
    model="fake-model",
)


class TestProviderFactory:
    def test_valid_config_builds_openai_compatible(self) -> None:
        provider = create_model_provider(FULL_LLM)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "openai_compatible"

    def test_unknown_provider_rejected(self) -> None:
        config = LLMConfig(provider="mystery", base_url="x", api_key="y", model="z")
        with pytest.raises(LLMConfigurationError, match="unknown LLM provider"):
            create_model_provider(config)

    @pytest.mark.parametrize(
        ("overrides", "variable"),
        [
            ({"base_url": None}, "AEMP_LLM_BASE_URL"),
            ({"api_key": None}, "AEMP_LLM_API_KEY"),
            ({"model": None}, "AEMP_LLM_MODEL"),
        ],
    )
    def test_missing_configuration_names_the_variable(
        self, overrides: dict, variable: str
    ) -> None:
        values = {
            "provider": "openai_compatible",
            "base_url": "http://llm.test/v1",
            "api_key": "test-key",
            "model": "fake-model",
        }
        values.update(overrides)
        with pytest.raises(LLMConfigurationError, match=variable):
            create_model_provider(LLMConfig(**values))


class TestRuntimeFactory:
    def test_stub_runtime(self) -> None:
        assert isinstance(create_agent_runtime(AgentConfig()), StubAgentRuntime)

    def test_llm_runtime(self) -> None:
        config = AgentConfig(runtime="llm", llm=FULL_LLM)
        runtime = create_agent_runtime(config)
        # M6.5: the llm runtime is the LangGraph StateGraph runtime; the
        # M6.3/M6.4 name remains a compatible subclass of the same impl.
        assert isinstance(runtime, LangGraphAgentRuntime)
        assert issubclass(LLMAgentRuntime, LangGraphAgentRuntime)

    def test_llm_without_key_raises_and_never_falls_back(self) -> None:
        config = AgentConfig(
            runtime="llm",
            llm=LLMConfig(base_url="http://llm.test/v1", api_key=None, model="m"),
        )
        with pytest.raises(LLMConfigurationError, match="AEMP_LLM_API_KEY"):
            create_agent_runtime(config)

    def test_llm_without_model_raises(self) -> None:
        config = AgentConfig(
            runtime="llm",
            llm=LLMConfig(base_url="http://llm.test/v1", api_key="k", model=None),
        )
        with pytest.raises(LLMConfigurationError, match="AEMP_LLM_MODEL"):
            create_agent_runtime(config)

    def test_unknown_runtime_rejected(self) -> None:
        with pytest.raises(ValueError, match="runtime"):
            AgentConfig(runtime="mystery")


class TestConfigFromEnv:
    def test_env_parsing(self) -> None:
        env = {
            "AEMP_AGENT_RUNTIME": "llm",
            "AEMP_LLM_PROVIDER": "openai_compatible",
            "AEMP_LLM_BASE_URL": "http://llm.test/v1",
            "AEMP_LLM_API_KEY": "test-key",
            "AEMP_LLM_MODEL": "fake-model",
            "AEMP_LLM_TIMEOUT_S": "12.5",
            "AEMP_LLM_TEMPERATURE": "0.3",
            "AEMP_LLM_MAX_TOKENS": "256",
        }
        config = AgentConfig.from_env(env)
        assert config.runtime == "llm"
        assert config.llm.base_url == "http://llm.test/v1"
        assert config.llm.timeout_s == 12.5
        assert config.llm.temperature == 0.3
        assert config.llm.max_tokens == 256

    def test_defaults_are_stub_without_llm_config(self) -> None:
        config = AgentConfig.from_env({})
        assert config.runtime == "stub"
        assert config.llm.api_key is None
        assert config.llm.timeout_s == 60.0
