"""Skill system (M7.1): knowledge + procedure, never execution.

    Skill   = Knowledge + Procedure + Guidance   (this package)
    Tool    = Executable Capability              (tools/, unchanged)
    Gateway = Security / execution boundary      (tools/gateway.py)

Skills are Markdown data loaded on demand (progressive disclosure) and
injected into the model context. They can never execute code, never bypass
the ToolGateway, the PolicyEngine or HITL.
"""

from agent_backend.skills.catalog import render_catalog
from agent_backend.skills.errors import (
    SkillError,
    SkillNotFoundError,
    SkillPathError,
    SkillValidationError,
)
from agent_backend.skills.loader import (
    MAX_REFERENCE_BYTES,
    MAX_SKILL_BYTES,
    SkillLoader,
    parse_skill_markdown,
)
from agent_backend.skills.models import SkillDocument, SkillMetadata
from agent_backend.skills.registry import SkillRegistry
from agent_backend.skills.service import (
    MAX_SELECTED_SKILLS,
    MAX_SKILL_CONTEXT_BYTES,
    SkillService,
)

__all__ = [
    "MAX_REFERENCE_BYTES",
    "MAX_SELECTED_SKILLS",
    "MAX_SKILL_BYTES",
    "MAX_SKILL_CONTEXT_BYTES",
    "SkillDocument",
    "SkillError",
    "SkillLoader",
    "SkillMetadata",
    "SkillNotFoundError",
    "SkillPathError",
    "SkillRegistry",
    "SkillService",
    "SkillValidationError",
    "parse_skill_markdown",
    "render_catalog",
]
