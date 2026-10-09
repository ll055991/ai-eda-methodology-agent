"""SkillService: selection + progressive context injection (M7.1).

Deterministic keyword/tag matching - no embeddings, no vector DB, no RAG.
The catalog (Level 1) is always available; only matching SKILL.md bodies
(Level 2) are injected, inside explicit ``<skill>`` boundaries and bounded
by ``MAX_SKILL_CONTEXT_BYTES``. References (Level 3) are never auto-loaded.
"""

from __future__ import annotations

import re

from agent_backend.logging import get_logger
from agent_backend.skills.catalog import render_catalog
from agent_backend.skills.loader import SkillLoader
from agent_backend.skills.models import SkillMetadata
from agent_backend.skills.registry import SkillRegistry

__all__ = ["MAX_SELECTED_SKILLS", "MAX_SKILL_CONTEXT_BYTES", "SkillService"]

logger = get_logger(__name__)

MAX_SKILL_CONTEXT_BYTES = 32 * 1024
MAX_SELECTED_SKILLS = 2

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _keyword_matches(keyword: str, text: str) -> bool:
    """ASCII keywords match on word boundaries; CJK keywords by substring."""
    keyword = keyword.lower()
    if keyword.isascii():
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return keyword in text


class SkillService:
    """Selects and loads skills for one agent request."""

    def __init__(
        self,
        registry: SkillRegistry,
        loader: SkillLoader,
        *,
        max_context_bytes: int = MAX_SKILL_CONTEXT_BYTES,
        max_skills: int = MAX_SELECTED_SKILLS,
    ) -> None:
        self._registry = registry
        self._loader = loader
        self._max_context_bytes = max_context_bytes
        self._max_skills = max_skills

    # -- discovery (Level 1) -------------------------------------------------

    def catalog_text(self) -> str:
        return render_catalog(self._registry.list())

    def select(self, message: str) -> tuple[SkillMetadata, ...]:
        """Rank skills by deterministic keyword/tag evidence."""
        text = message.lower()
        scored: list[tuple[int, str, SkillMetadata]] = []
        for metadata in self._registry.list():
            score = self._score(metadata, text)
            if score > 0:
                scored.append((-score, metadata.name, metadata))
        scored.sort()
        return tuple(metadata for _, _, metadata in scored[: self._max_skills])

    @staticmethod
    def _score(metadata: SkillMetadata, text: str) -> int:
        score = 0
        for tag in metadata.tags:
            if _keyword_matches(tag, text):
                score += 2
        words = _WORD_RE.findall(f"{metadata.name} {metadata.description}".lower())
        for word in words:
            if len(word) >= 4 and _keyword_matches(word, text):
                score += 1
        return score

    # -- progressive disclosure (Levels 2/3) ---------------------------------

    def build_skill_context(self, message: str) -> str:
        """Catalog + selected SKILL.md bodies, bounded; references stay out."""
        parts = [self.catalog_text()]
        used = len(parts[0].encode("utf-8"))
        selected = self.select(message)
        for metadata in selected:
            document = self._loader.load_skill(metadata.name)
            block = document.as_context_block(
                self._loader.reference_names(metadata.name)
            )
            block_bytes = len(block.encode("utf-8"))
            if used + block_bytes > self._max_context_bytes:
                logger.warning(
                    "skill context budget exceeded; skipping %s", metadata.name
                )
                break
            parts.append(block)
            used += block_bytes
        logger.info(
            "skills selected: %s",
            ", ".join(metadata.name for metadata in selected) or "(none)",
        )
        return "\n\n".join(parts)

    def build_system_prompt(self, base_prompt: str, message: str) -> str:
        """The agent system prompt = base rules + skill context."""
        return f"{base_prompt}\n\n{self.build_skill_context(message)}"
