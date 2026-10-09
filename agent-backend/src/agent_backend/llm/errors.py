"""LLM error hierarchy: configuration, transport, provider, response.

Callers can distinguish "we are misconfigured" from "the network failed"
from "the provider rejected us" from "the provider answered garbage".
No automatic fallback anywhere - errors surface as-is.
"""

from __future__ import annotations

__all__ = [
    "LLMAuthenticationError",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMTimeoutError",
]


class LLMError(Exception):
    """Base class for all LLM-layer failures."""


class LLMConfigurationError(LLMError):
    """Missing/invalid LLM configuration (base URL, API key, model, provider)."""


class LLMAuthenticationError(LLMError):
    """The provider rejected the credentials (HTTP 401/403)."""


class LLMConnectionError(LLMError):
    """The provider could not be reached (refused / DNS / protocol)."""


class LLMTimeoutError(LLMError):
    """The provider did not answer within the configured timeout."""


class LLMRateLimitError(LLMError):
    """The provider rate-limited the request (HTTP 429)."""


class LLMProviderError(LLMError):
    """The provider returned an error status (non-auth, non-rate-limit)."""


class LLMResponseError(LLMError):
    """HTTP 200 but the body does not match the expected response shape."""
