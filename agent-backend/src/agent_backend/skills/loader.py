"""SkillLoader: reads SKILL.md and references - strictly inside the root.

Path discipline: a skill name must match ``[a-z0-9][a-z0-9-]{0,63}`` and
every reference path is resolved and verified to stay inside the skill's
directory. Absolute paths, ``..`` traversal and symlink escapes are
rejected with :class:`SkillPathError` before any read happens.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import yaml

from agent_backend.skills.errors import (
    SkillNotFoundError,
    SkillPathError,
    SkillValidationError,
)
from agent_backend.skills.models import SkillDocument, SkillMetadata

__all__ = [
    "MAX_REFERENCE_BYTES",
    "MAX_SKILL_BYTES",
    "SKILL_FILENAME",
    "SkillLoader",
    "parse_skill_markdown",
]

MAX_SKILL_BYTES = 32 * 1024
MAX_REFERENCE_BYTES = 64 * 1024
SKILL_FILENAME = "SKILL.md"
_REFERENCES_DIR = "references"

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def parse_skill_markdown(text: str, source: str = "SKILL.md") -> SkillDocument:
    """Parse ``---`` frontmatter + markdown body into a document.

    Raises:
        SkillValidationError: Missing/invalid frontmatter or metadata.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillValidationError(f"{source}: missing YAML frontmatter")
    try:
        end = next(
            index for index in range(1, len(lines)) if lines[index].strip() == "---"
        )
    except StopIteration as exc:
        raise SkillValidationError(f"{source}: unterminated frontmatter") from exc
    try:
        front = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"{source}: invalid frontmatter YAML: {exc}") from exc
    if not isinstance(front, dict):
        raise SkillValidationError(f"{source}: frontmatter must be a mapping")
    tags = front.get("tags") or ()
    if isinstance(tags, str):
        tags = (tags,)
    try:
        metadata = SkillMetadata(
            name=str(front.get("name", "")),
            description=str(front.get("description", "")),
            version=str(front.get("version", "")),
            tags=tuple(tags),
        )
    except ValueError as exc:
        raise SkillValidationError(f"{source}: {exc}") from exc
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise SkillValidationError(f"{source}: empty skill body")
    return SkillDocument(metadata=metadata, body=body)


class SkillLoader:
    """Reads skill files under one configured root; nothing else."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _skill_dir(self, name: str) -> Path:
        if not _NAME_RE.fullmatch(name):
            raise SkillValidationError(f"invalid skill name {name!r}")
        return self._root / name

    def _read_bounded(self, path: Path, limit: int, what: str) -> str:
        if not path.is_file():
            raise SkillNotFoundError(f"{what} not found: {path.name}")
        size = path.stat().st_size
        if size > limit:
            raise SkillValidationError(
                f"{what} exceeds {limit} bytes ({size}): {path.name}"
            )
        return path.read_text(encoding="utf-8")

    def load_skill(self, name: str) -> SkillDocument:
        """Load one SKILL.md (name-validated, bounded)."""
        path = self._skill_dir(name) / SKILL_FILENAME
        text = self._read_bounded(path, MAX_SKILL_BYTES, "skill file")
        document = parse_skill_markdown(text, source=f"{name}/{SKILL_FILENAME}")
        if document.metadata.name != name:
            raise SkillValidationError(
                f"{name}: frontmatter name {document.metadata.name!r} "
                "does not match the skill directory"
            )
        return document

    def load_reference(self, name: str, relative_path: str) -> str:
        """Load one file from the skill's ``references/`` (paths are relative).

        The path is validated before any read: absolute paths (POSIX or
        Windows), ``..`` traversal and symlink escapes are all rejected.
        """
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or PurePosixPath(relative_path).is_absolute()
        ):
            raise SkillPathError(f"reference path must be relative: {relative_path!r}")
        normalized = PurePosixPath(relative_path.replace("\\", "/"))
        if any(part == ".." for part in normalized.parts):
            raise SkillPathError(
                f"reference path escapes the skill directory: {relative_path!r}"
            )
        skill_dir = self._skill_dir(name).resolve()
        target = (skill_dir / _REFERENCES_DIR / relative_path).resolve()
        if not target.is_relative_to(skill_dir):
            raise SkillPathError(
                f"reference path escapes the skill directory: {relative_path!r}"
            )
        return self._read_bounded(target, MAX_REFERENCE_BYTES, "reference")

    def reference_names(self, name: str) -> tuple[str, ...]:
        """Relative paths of the skill's references (no content read)."""
        references_dir = self._skill_dir(name) / _REFERENCES_DIR
        if not references_dir.is_dir():
            return ()
        return tuple(
            sorted(
                path.relative_to(references_dir).as_posix()
                for path in references_dir.rglob("*")
                if path.is_file()
            )
        )
