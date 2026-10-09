"""PlatformClient error model: business vs transport vs contract errors.

Three families that must stay distinguishable:

- :class:`PlatformApiError` — the Platform answered with a stable error
  document (RUN_NOT_FOUND, FLOW_VALIDATION_FAILED, ...): a business fact.
- :class:`PlatformConnectionError` — the Agent could not talk to the
  Platform at all (refused / timeout / DNS): a transport fact.
- :class:`PlatformContractError` — an answer arrived but does not satisfy
  the shared contract (non-JSON, missing/unexpected fields): an
  integration bug, never silently accepted.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "PlatformApiError",
    "PlatformClientError",
    "PlatformConnectionError",
    "PlatformContractError",
]


class PlatformClientError(Exception):
    """Base class for all PlatformClient failures."""


class PlatformConnectionError(PlatformClientError):
    """The Platform could not be reached (transport-level failure)."""


class PlatformApiError(PlatformClientError):
    """The Platform returned a stable API error document.

    Attributes:
        code: Contract error code (e.g. ``RUN_NOT_FOUND``).
        message: Human-readable message from the Platform.
        details: Machine-readable extras from the Platform.
        http_status: HTTP status the Platform answered with.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        http_status: int,
    ) -> None:
        super().__init__(f"{code} ({http_status}): {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.http_status = http_status


class PlatformContractError(PlatformClientError):
    """A Platform response violates the shared API contract."""
