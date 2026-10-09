"""Report parser registry: "how do we understand a tool's output?".

Deliberately separate from the M3 ToolRegistry ("how do we execute a
tool?"). v1 registers OpenSTA only; future parsers (openroad, klayout,
yosys) plug in without touching the engine.
"""

from __future__ import annotations

from aemp.core.errors import InvalidRequestError
from aemp.parsers.base import ReportParser

__all__ = ["ReportParserRegistry"]


class ReportParserRegistry:
    """Name -> parser mapping with explicit, validated resolution."""

    def __init__(self) -> None:
        self._parsers: dict[str, ReportParser] = {}

    def register(self, name: str, parser: ReportParser) -> None:
        if name in self._parsers:
            msg = f"Report parser already registered: {name!r}"
            raise InvalidRequestError(msg)
        self._parsers[name] = parser

    def resolve(self, name: str) -> ReportParser:
        parser = self._parsers.get(name)
        if parser is None:
            msg = f"Unknown report parser: {name!r} (registered: {sorted(self._parsers)})"
            raise InvalidRequestError(msg)
        return parser
