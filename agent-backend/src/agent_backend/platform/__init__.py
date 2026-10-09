"""Platform access: HTTP-only client + error model.

Nothing in this package may import Platform code; the Platform API
contract is the only interface.
"""

from agent_backend.platform.client import PlatformClient
from agent_backend.platform.errors import (
    PlatformApiError,
    PlatformClientError,
    PlatformConnectionError,
    PlatformContractError,
)

__all__ = [
    "PlatformApiError",
    "PlatformClient",
    "PlatformClientError",
    "PlatformConnectionError",
    "PlatformContractError",
]
