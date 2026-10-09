"""Report parser base: file handling here, format logic in subclasses.

``parse(path)`` reads the file ONCE (eda reports can be large) and hands
the text to :meth:`parse_text`, which is what unit tests exercise without
any file system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from aemp.core.errors import InvalidRequestError
from aemp.parsers.errors import ReportNotFoundError
from aemp.parsers.models import ParsedReport

__all__ = ["ReportParser"]


class ReportParser(ABC):
    """Base class for all report parsers: report text in, facts out.

    Parsers are pure: no Docker, no tool execution, no RunManager, no
    database. They also never judge - a parser produces metrics; PASS/FAIL
    is the QA engine's job (M5).
    """

    tool_name: str

    def parse(self, path: Path) -> ParsedReport:
        """Read *path* once and parse its text.

        Raises:
            ReportNotFoundError: If *path* does not exist.
            ReportParseError: If the content is empty or unrecognized.
        """
        path = Path(path)
        if not path.is_file():
            msg = f"Report file does not exist: {path}"
            raise ReportNotFoundError(msg)
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.parse_text(text, source=path.name)

    @abstractmethod
    def parse_text(self, text: str, *, source: str = "<text>") -> ParsedReport:
        """Parse report *text* into structured facts.

        Raises:
            ReportParseError: If the content is empty or unrecognized.
        """
        ...  # pragma: no cover

    @staticmethod
    def _require_tool_name(tool_name: str) -> str:
        if not tool_name or not tool_name.strip():
            msg = "tool_name must be a non-empty string"
            raise InvalidRequestError(msg)
        return tool_name
