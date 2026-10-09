"""SkillRegistry: discovers and validates the skills under one root.

Only the configured root is scanned; duplicate or malformed skills fail
loudly at startup (never silently dropped).
"""

from __future__ import annotations

from pathlib import Path

from agent_backend.skills.errors import SkillNotFoundError, SkillValidationError
from agent_backend.skills.loader import SKILL_FILENAME, parse_skill_markdown
from agent_backend.skills.models import SkillMetadata

__all__ = ["SkillRegistry"]


class SkillRegistry:
    """Name -> metadata catalog of the available skills."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._skills: dict[str, SkillMetadata] = {}
        self._discover()

    def _discover(self) -> None:
        if not self._root.is_dir():
            raise SkillValidationError(f"skills root not found: {self._root}")
        for directory in sorted(self._root.iterdir()):
            if not directory.is_dir():
                continue
            skill_file = directory / SKILL_FILENAME
            if not skill_file.is_file():
                continue
            document = parse_skill_markdown(
                skill_file.read_text(encoding="utf-8"),
                source=f"{directory.name}/{SKILL_FILENAME}",
            )
            metadata = document.metadata
            if metadata.name != directory.name:
                raise SkillValidationError(
                    f"{directory.name}: frontmatter name {metadata.name!r} "
                    "does not match the directory"
                )
            if metadata.name in self._skills:
                raise SkillValidationError(
                    f"duplicate skill name: {metadata.name!r}"
                )
            self._skills[metadata.name] = metadata

    def list(self) -> tuple[SkillMetadata, ...]:
        return tuple(self._skills[name] for name in sorted(self._skills))

    def get(self, name: str) -> SkillMetadata:
        metadata = self._skills.get(name)
        if metadata is None:
            raise SkillNotFoundError(f"unknown skill: {name!r}")
        return metadata

    def exists(self, name: str) -> bool:
        return name in self._skills
