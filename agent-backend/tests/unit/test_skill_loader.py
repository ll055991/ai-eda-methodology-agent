"""SkillLoader tests: bounded reads + strict path safety (M7.1)."""

from pathlib import Path

import pytest

from agent_backend.skills import (
    SkillLoader,
    SkillNotFoundError,
    SkillPathError,
    SkillValidationError,
)

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


class TestLoading:
    def test_load_skill_md(self) -> None:
        document = SkillLoader(SKILLS_ROOT).load_skill("timing-analysis")
        assert document.metadata.name == "timing-analysis"
        assert document.metadata.version == "1.0"
        assert "sta.wns_ns" in document.body

    def test_load_reference(self) -> None:
        text = SkillLoader(SKILLS_ROOT).load_reference(
            "timing-analysis", "sta-metrics.md"
        )
        assert "sta.wns_ns" in text and "sta.tns_ns" in text

    def test_reference_names_are_listed_without_content(self) -> None:
        names = SkillLoader(SKILLS_ROOT).reference_names("timing-analysis")
        assert names == ("sta-metrics.md",)

    def test_missing_reference(self) -> None:
        with pytest.raises(SkillNotFoundError, match="reference"):
            SkillLoader(SKILLS_ROOT).load_reference("timing-analysis", "nope.md")

    def test_missing_skill(self) -> None:
        with pytest.raises(SkillNotFoundError, match="skill file"):
            SkillLoader(SKILLS_ROOT).load_skill("nope")

    def test_oversized_skill_rejected(self, tmp_path: Path) -> None:
        directory = tmp_path / "big"
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            "---\nname: big\ndescription: d\nversion: '1'\n---\n\n" + "x" * 40000,
            encoding="utf-8",
        )
        with pytest.raises(SkillValidationError, match="exceeds"):
            SkillLoader(tmp_path).load_skill("big")


class TestPathSafety:
    def test_traversal_rejected(self) -> None:
        with pytest.raises(SkillPathError, match="escapes"):
            SkillLoader(SKILLS_ROOT).load_reference(
                "timing-analysis", "../flow-engineering/SKILL.md"
            )

    def test_nested_traversal_rejected(self) -> None:
        with pytest.raises(SkillPathError, match="escapes"):
            SkillLoader(SKILLS_ROOT).load_reference(
                "timing-analysis", "references/../../flow-engineering/SKILL.md"
            )

    @pytest.mark.parametrize("path", ["/etc/passwd", "C:/Windows/win.ini"])
    def test_absolute_path_rejected(self, path: str) -> None:
        with pytest.raises(SkillPathError, match="relative"):
            SkillLoader(SKILLS_ROOT).load_reference("timing-analysis", path)

    def test_skill_name_traversal_rejected(self) -> None:
        with pytest.raises(SkillValidationError, match="invalid skill name"):
            SkillLoader(SKILLS_ROOT).load_skill("../../secret")
