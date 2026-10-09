"""Skill selection tests (M7.1 Tests 14-17): deterministic, no embeddings."""

from pathlib import Path

from agent_backend.skills import SkillLoader, SkillRegistry, SkillService

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def selected(message: str) -> tuple[str, ...]:
    service = SkillService(SkillRegistry(SKILLS_ROOT), SkillLoader(SKILLS_ROOT))
    return tuple(metadata.name for metadata in service.select(message))


def test_timing_request_loads_timing_analysis() -> None:
    assert "timing-analysis" in selected("Analyze the STA timing results and WNS")


def test_flow_request_loads_flow_engineering() -> None:
    names = selected("Run RTL to STA")
    assert "flow-engineering" in names
    assert "timing-analysis" in names  # capped at two, both relevant


def test_diagnosis_request_loads_failure_diagnosis() -> None:
    assert "failure-diagnosis" in selected("Why did the run fail?")


def test_chinese_request_matches_tags() -> None:
    names = selected("帮我看看这个 STA 为什么失败了")
    assert "timing-analysis" in names
    assert "failure-diagnosis" in names


def test_irrelevant_request_selects_nothing() -> None:
    assert selected("What is the weather today?") == ()


def test_selection_is_capped_at_two() -> None:
    names = selected("Run RTL flow and analyze STA timing and library PDK errors")
    assert len(names) <= 2


def test_status_word_does_not_match_sta_tag() -> None:
    # "status" must not be treated as the "sta" keyword
    assert "timing-analysis" not in selected("Show the run status")
