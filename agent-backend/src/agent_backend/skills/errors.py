"""Skill-layer errors (loading, validation, path safety)."""

from __future__ import annotations

__all__ = [
    "SkillError",
    "SkillNotFoundError",
    "SkillPathError",
    "SkillValidationError",
]


class SkillError(Exception):
    """Base class for skill-layer failures."""


class SkillNotFoundError(SkillError):
    """The requested skill or reference does not exist."""


class SkillValidationError(SkillError):
    """A SKILL.md is missing, malformed or duplicate."""


class SkillPathError(SkillError):
    """A skill path escapes its skill root (never allowed)."""
