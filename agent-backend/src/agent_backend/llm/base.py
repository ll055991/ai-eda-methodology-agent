"""ModelProvider protocol: the seam between the runtime and any vendor.

The runtime never imports a concrete provider; ``llm/factory.py`` decides
which implementation to construct.
"""

from __future__ import annotations

from typing import Protocol

from agent_backend.llm.models import ModelRequest, ModelResponse

__all__ = ["ModelProvider"]


class ModelProvider(Protocol):
    """One text-completion provider (M6.3: no tool calling)."""

    name: str

    def chat(self, request: ModelRequest) -> ModelResponse:
        """Execute one model call.

        Raises:
            LLMError: Any failure, as one of the concrete subclasses.
        """
        ...
