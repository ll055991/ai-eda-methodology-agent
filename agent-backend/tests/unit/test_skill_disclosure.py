"""Progressive disclosure tests (M7.1): catalog -> SKILL.md -> references."""

from pathlib import Path

from agent_backend.skills import SkillLoader, SkillRegistry, SkillService

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def make_service() -> SkillService:
    return SkillService(SkillRegistry(SKILLS_ROOT), SkillLoader(SKILLS_ROOT))


class TestLevels:
    def test_catalog_is_metadata_only(self) -> None:
        catalog = make_service().catalog_text()
        assert "timing-analysis" in catalog
        assert "Interpret OpenSTA timing results" in catalog
        # no skill body / procedure leaked into the catalog
        assert "## Procedure" not in catalog
        assert "sta.wns_ns" not in catalog
        assert "<skill" not in catalog

    def test_loading_one_skill_does_not_load_all(self) -> None:
        context = make_service().build_skill_context("Analyze the STA timing results")
        assert '<skill name="timing-analysis"' in context
        assert '<skill name="library-qa"' not in context
        # a library-qa BODY phrase (its catalog description still appears)
        assert "Not currently implemented" not in context

    def test_irrelevant_message_loads_no_bodies(self) -> None:
        context = make_service().build_skill_context("What is the weather today?")
        assert "<skill" not in context
        assert "Available skills" in context  # the catalog is still there

    def test_reference_content_is_not_auto_loaded(self) -> None:
        context = make_service().build_skill_context("Analyze the STA timing results")
        assert "References (available on request, not loaded): sta-metrics.md" in context
        # a line that exists only in the reference file, never in SKILL.md
        assert "from `worst slack max`" not in context

    def test_context_is_bounded(self) -> None:
        service = SkillService(
            SkillRegistry(SKILLS_ROOT),
            SkillLoader(SKILLS_ROOT),
            max_context_bytes=200,
        )
        context = service.build_skill_context("Analyze the STA timing results")
        assert '<skill name="timing-analysis"' not in context  # body skipped
        assert len(context.encode("utf-8")) < 2048  # catalog only

    def test_system_prompt_keeps_base_rules_first(self) -> None:
        prompt = make_service().build_system_prompt("BASE RULES", "Analyze STA timing")
        assert prompt.startswith("BASE RULES")
        assert '<skill name="timing-analysis"' in prompt
