"""SkillRegistry tests: discovery, validation, uniqueness (M7.1)."""

from pathlib import Path

import pytest

from agent_backend.skills import SkillNotFoundError, SkillRegistry, SkillValidationError

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def write_skill(root: Path, dir_name: str, frontmatter: str) -> None:
    directory = root / dir_name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\n\n# Body\n\ntext\n", encoding="utf-8"
    )


class TestDiscovery:
    def test_lists_the_four_installed_skills(self) -> None:
        registry = SkillRegistry(SKILLS_ROOT)
        assert [metadata.name for metadata in registry.list()] == [
            "failure-diagnosis",
            "flow-engineering",
            "library-qa",
            "timing-analysis",
        ]

    def test_get_existing_and_exists(self) -> None:
        registry = SkillRegistry(SKILLS_ROOT)
        metadata = registry.get("timing-analysis")
        assert metadata.version == "1.0"
        assert "sta" in metadata.tags
        assert registry.exists("timing-analysis") is True
        assert registry.exists("nope") is False

    def test_unknown_skill_raises(self) -> None:
        registry = SkillRegistry(SKILLS_ROOT)
        with pytest.raises(SkillNotFoundError, match="unknown skill"):
            registry.get("nope")


class TestValidation:
    def test_missing_root_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SkillValidationError, match="root"):
            SkillRegistry(tmp_path / "missing")

    def test_duplicate_skill_name_rejected(self) -> None:
        # name == directory is enforced, so duplicates can only appear when
        # discovery runs twice over the same registry - exercise that branch.
        registry = SkillRegistry(SKILLS_ROOT)
        with pytest.raises(SkillValidationError, match="duplicate skill name"):
            registry._discover()

    def test_mismatched_directory_name_rejected(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "one", "name: other\ndescription: d\nversion: '1'")
        with pytest.raises(SkillValidationError, match="does not match"):
            SkillRegistry(tmp_path)

    def test_missing_description_rejected(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "broken", "name: broken\nversion: '1'")
        with pytest.raises(SkillValidationError, match="description"):
            SkillRegistry(tmp_path)

    def test_missing_version_rejected(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "broken", "name: broken\ndescription: d")
        with pytest.raises(SkillValidationError, match="version"):
            SkillRegistry(tmp_path)

    def test_invalid_name_rejected(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "Bad_Name", "name: Bad_Name\ndescription: d\nversion: '1'")
        with pytest.raises(SkillValidationError):
            SkillRegistry(tmp_path)

    def test_missing_frontmatter_rejected(self, tmp_path: Path) -> None:
        directory = tmp_path / "raw"
        directory.mkdir()
        (directory / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
        with pytest.raises(SkillValidationError, match="frontmatter"):
            SkillRegistry(tmp_path)
