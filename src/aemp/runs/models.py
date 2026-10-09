"""Shared run-domain constants and value types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RUN_STATUS_CREATED = "created"
RUN_STATUS_RUNNING = "running"  # reserved for M3 flow execution; not settable in M1
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
RUN_STATUSES = (
    RUN_STATUS_CREATED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    RUN_STATUS_FAILED,
)

EXECUTION_STATUSES = ("success", "failed", "unknown")

# Engineering statuses: the QA-engine vocabulary (pass/fail/unknown) plus
# the M1-era synonyms (success/failed) kept for backward compatibility -
# older writers and the finalize() mirror path still use them.
ENGINEERING_STATUSES = ("success", "failed", "unknown", "pass", "fail")


@dataclass(frozen=True)
class FileRecord:
    """Evidence metadata for one input or artifact file.

    ``exists=False`` records carry no ``bytes``/``sha256``: a hash is
    never fabricated for a file that was not actually read.
    """

    role: str
    path: str
    exists: bool
    bytes: int | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form matching the ``aemp.result/v0`` fileRecord shape."""
        data: dict[str, Any] = {"role": self.role, "path": self.path, "exists": self.exists}
        if self.exists:
            data["bytes"] = self.bytes
            data["sha256"] = self.sha256
        return data
