"""ResourceService unit tests (M7.2): the allowlist IS the security model.

A resource reader is a file reader, so every rejection below is a security
property, not a formatting preference: unknown scheme, traversal, absolute
paths, percent-encoding tricks, unknown names and oversized files.
"""

from pathlib import Path

import pytest

from aemp.mcp.errors import (
    ResourceNotFoundError,
    ResourcePathError,
    ResourceTooLargeError,
)
from aemp.mcp.resources import (
    CATALOG_URI,
    DOCS_PREFIX,
    SKILLS_PREFIX,
    ResourceService,
)

SKILL_TEMPLATE = """---
name: {name}
description: {description}
version: "1.0"
tags: [sta, timing]
---

# {name}

Body of {name}.
"""


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    skills = tmp_path / "skills"
    (skills / "timing-analysis" / "references").mkdir(parents=True)
    (skills / "timing-analysis" / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name="timing-analysis", description="Read STA results"),
        encoding="utf-8",
    )
    (skills / "timing-analysis" / "references" / "sta-metrics.md").write_text(
        "# STA metrics\n", encoding="utf-8"
    )
    (skills / "flow-engineering").mkdir()
    (skills / "flow-engineering" / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name="flow-engineering", description="Run flows"),
        encoding="utf-8",
    )
    (skills / "not-a-skill").mkdir()  # no SKILL.md -> not a skill
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "01-architecture.md").write_text("# Architecture\n", encoding="utf-8")
    (docs / "notes.txt").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text("secret\n", encoding="utf-8")
    return skills, docs


def _service(skills: Path, docs: Path, *, max_bytes: int = 65536) -> ResourceService:
    return ResourceService(skills_dir=skills, docs_dir=docs, max_bytes=max_bytes)


def test_catalog_lists_skills_only(roots: tuple[Path, Path]) -> None:
    service = _service(*roots)
    listed = [spec.uri for spec in service.resources()]

    assert CATALOG_URI in listed
    assert f"{SKILLS_PREFIX}/timing-analysis" in listed
    assert f"{SKILLS_PREFIX}/flow-engineering" in listed
    assert f"{SKILLS_PREFIX}/not-a-skill" not in listed
    assert f"{DOCS_PREFIX}/01-architecture.md" in listed
    assert f"{DOCS_PREFIX}/notes.txt" not in listed  # markdown only


def test_references_are_enumerated(roots: tuple[Path, Path]) -> None:
    service = _service(*roots)
    listed = [spec.uri for spec in service.resources()]

    assert f"{SKILLS_PREFIX}/timing-analysis/references/sta-metrics.md" in listed
    assert all(
        "not-a-skill" not in uri for uri in listed
    ), "a directory without SKILL.md contributes nothing"


def test_catalog_text_is_markdown_metadata(roots: tuple[Path, Path]) -> None:
    text = _service(*roots).read(CATALOG_URI).text

    assert "timing-analysis" in text
    assert "Read STA results" in text
    assert "version: 1.0" in text


def test_skill_body_and_reference_are_readable(roots: tuple[Path, Path]) -> None:
    service = _service(*roots)

    skill = service.read(f"{SKILLS_PREFIX}/timing-analysis")
    assert skill.mime_type == "text/markdown"
    assert "Body of timing-analysis." in skill.text

    reference = service.read(
        f"{SKILLS_PREFIX}/timing-analysis/references/sta-metrics.md"
    )
    assert reference.text == "# STA metrics\n"


def test_document_is_readable(roots: tuple[Path, Path]) -> None:
    content = _service(*roots).read(f"{DOCS_PREFIX}/01-architecture.md")

    assert content.text == "# Architecture\n"
    assert content.uri == f"{DOCS_PREFIX}/01-architecture.md"


def test_templates_are_declared(roots: tuple[Path, Path]) -> None:
    templates = {spec.uri for spec in _service(*roots).templates()}

    assert f"{SKILLS_PREFIX}/{{name}}" in templates
    assert f"{DOCS_PREFIX}/{{name}}" in templates


@pytest.mark.parametrize(
    "uri",
    [
        "methodology://skills/../../secret",
        "methodology://skills/timing-analysis/references/../../../secret.md",
        "methodology://docs/../secret.md",
        "file:///etc/passwd",
        "http://example.com/x",
        "C:\\secret",
        "C:/secret",
        "methodology:///etc/passwd",
        "methodology://skills/%2e%2e/secret",
        "methodology://skills/timing-analysis\\..\\secret",
        "methodology://skills/ timed-analysis",
        "methodology://skills/",
        "",
    ],
)
def test_uri_is_rejected_before_any_read(roots: tuple[Path, Path], uri: str) -> None:
    with pytest.raises(ResourcePathError):
        _service(*roots).read(uri)


def test_unknown_names_are_not_found(roots: tuple[Path, Path]) -> None:
    service = _service(*roots)

    with pytest.raises(ResourceNotFoundError):
        service.read(f"{SKILLS_PREFIX}/no-such-skill")
    with pytest.raises(ResourceNotFoundError):
        service.read(f"{DOCS_PREFIX}/no-such-doc.md")
    with pytest.raises(ResourceNotFoundError):
        service.read(f"{SKILLS_PREFIX}/timing-analysis/references/missing.md")


def test_reference_of_unknown_skill_is_rejected(roots: tuple[Path, Path]) -> None:
    with pytest.raises(ResourceNotFoundError):
        _service(*roots).read(f"{SKILLS_PREFIX}/nope/references/x.md")


def test_oversized_resource_is_refused(roots: tuple[Path, Path]) -> None:
    service = _service(*roots, max_bytes=4)

    with pytest.raises(ResourceTooLargeError):
        service.read(f"{DOCS_PREFIX}/01-architecture.md")


def test_oversized_skill_body_is_refused(roots: tuple[Path, Path]) -> None:
    service = _service(*roots)
    body = roots[0] / "timing-analysis" / "SKILL.md"
    body.write_text(
        SKILL_TEMPLATE.format(name="timing-analysis", description="desc")
        + ("x" * 40 * 1024),
        encoding="utf-8",
    )

    with pytest.raises(ResourceTooLargeError):
        service.read(f"{SKILLS_PREFIX}/timing-analysis")


def test_missing_roots_yield_an_empty_catalog(tmp_path: Path) -> None:
    service = ResourceService(skills_dir=tmp_path / "nope", docs_dir=None)

    assert service.resources() == ()
    assert service.templates() == ()
    with pytest.raises(ResourceNotFoundError):
        service.read(CATALOG_URI)


def test_malformed_skill_fails_loudly(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    (skills / "broken").mkdir(parents=True)
    (skills / "broken" / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")

    with pytest.raises(ResourcePathError):
        ResourceService(skills_dir=skills, docs_dir=None)


def test_skill_directory_name_must_match_frontmatter(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    (skills / "timing-analysis").mkdir(parents=True)
    (skills / "timing-analysis" / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name="other-name", description="desc"), encoding="utf-8"
    )

    with pytest.raises(ResourcePathError):
        ResourceService(skills_dir=skills, docs_dir=None)
