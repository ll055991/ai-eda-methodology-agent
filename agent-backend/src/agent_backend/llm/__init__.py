"""LLM adapter layer: provider abstraction + OpenAI-compatible provider.

The runtime depends on the ``ModelProvider`` protocol only; concrete
vendors stay behind ``llm/factory.py``. No vendor SDK is used - HTTP via
httpx (M6.3).
"""

from agent_backend.llm.base import ModelProvider
from agent_backend.llm.errors import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from agent_backend.llm.factory import create_model_provider
from agent_backend.llm.models import (
    ChatMessage,
    ModelRequest,
    ModelResponse,
    ModelTrace,
    ModelUsage,
)

__all__ = [
    "ChatMessage",
    "LLMAuthenticationError",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMTimeoutError",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelTrace",
    "ModelUsage",
    "create_model_provider",
]
