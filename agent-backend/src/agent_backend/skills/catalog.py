"""Level-1 catalog rendering (metadata only, never skill content)."""

from __future__ import annotations

from collections.abc import Iterable

from agent_backend.skills.models import SkillMetadata

__all__ = ["render_catalog"]


def render_catalog(entries: Iterable[SkillMetadata]) -> str:
    """Render metadata lines for the model; no SKILL.md body is included."""
    lines = ["Available skills (metadata only; full content loads on demand):"]
    materialized = list(entries)
    if not materialized:
        lines.append("- (no skills installed)")
    for metadata in materialized:
        tags = f" [tags: {', '.join(metadata.tags)}]" if metadata.tags else ""
        lines.append(
            f"- {metadata.name} (v{metadata.version}): {metadata.description}{tags}"
        )
    return "\n".join(lines)
