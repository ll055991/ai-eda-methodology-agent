"""Parser-layer errors. Distinct on purpose (they must never be conflated):

- file missing            -> ReportNotFoundError
- file exists but the content is empty / unrecognizable -> ReportParseError
- a known report missing ONE metric -> NOT an error: the metric is absent
  and a diagnostic records the gap (unknown != 0).
"""

from __future__ import annotations

from aemp.core.errors import AEMPError

__all__ = ["ReportNotFoundError", "ReportParseError"]


class ReportNotFoundError(AEMPError):
    """The report file does not exist on disk."""


class ReportParseError(AEMPError):
    """The report content is empty or does not match any known format."""
