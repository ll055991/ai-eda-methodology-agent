"""Skill value types: metadata + document.

Skill metadata is validated at load time; a malformed SKILL.md can never
reach the agent context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["SkillDocument", "SkillMetadata"]

_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


@dataclass(frozen=True)
class SkillMetadata:
    """Level-1 catalog data for one skill."""

    name: str
    description: str
    version: str
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_PATTERN.fullmatch(self.name):
            raise ValueError(f"invalid skill name {self.name!r}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"skill {self.name!r} needs a non-empty description")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError(f"skill {self.name!r} needs a non-empty version")
        object.__setattr__(
            self, "tags", tuple(str(tag) for tag in self.tags if str(tag).strip())
        )


@dataclass(frozen=True)
class SkillDocument:
    """One loaded SKILL.md (metadata + markdown body)."""

    metadata: SkillMetadata
    body: str

    def as_context_block(self, references: tuple[str, ...] = ()) -> str:
        """Explicit-boundary block injected into the model context."""
        parts = [
            f'<skill name="{self.metadata.name}" version="{self.metadata.version}">',
            self.body.strip(),
        ]
        if references:
            parts.append(
                "References (available on request, not loaded): "
                + ", ".join(references)
            )
        parts.append("</skill>")
        return "\n".join(parts)
