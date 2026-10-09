"""MCP resources: read-only methodology knowledge (M7.2).

Resources are the second MCP primitive (tools act, resources inform). This
server exposes exactly these families, all read-only Markdown:

    methodology://skills/catalog                     the M7.1 skill catalog
    methodology://skills/{name}                      one SKILL.md body
    methodology://skills/{name}/references/{path}    one reference document
    methodology://docs/{name}                        one platform document

Every entry is enumerated concretely (the catalog is small and fully
knowable at startup), so there is no template matching to get wrong and an
unknown URI simply does not exist. The catalog text also advertises each
skill's reference names, so a client can read them without guessing.

Security posture (spec §18) - a resource reader is a file reader, so the
allowlist is the whole design:

* the scheme must be ``methodology`` (``file://`` is not representable);
* a URI is split into plain segments, and ``%``/``\\``/``..``/drive letters
  are rejected outright (no encoding tricks, no traversal, no absolute paths);
* every resolved path must stay inside its configured root;
* names must match a strict pattern and the file must exist;
* every read is size-bounded.

Skill *content* is data owned by the M7.1 skill system; this module reads
those files (never agent code, never a Python import) and validates the
same invariants the skill loader does. It never executes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aemp.mcp.config import MAX_RESOURCE_BYTES
from aemp.mcp.errors import (
    ResourceNotFoundError,
    ResourcePathError,
    ResourceTooLargeError,
)

__all__ = [
    "CATALOG_URI",
    "DOCS_PREFIX",
    "MAX_SKILL_BYTES",
    "SKILLS_PREFIX",
    "ResourceContent",
    "ResourceService",
    "ResourceSpec",
]

SCHEME = "methodology"
SKILLS_PREFIX = f"{SCHEME}://skills"
DOCS_PREFIX = f"{SCHEME}://docs"
CATALOG_URI = f"{SKILLS_PREFIX}/catalog"
SKILL_FILENAME = "SKILL.md"
REFERENCES_DIR = "references"
MIME_MARKDOWN = "text/markdown"

#: M7.1 parity: a skill body is bounded to 32 KiB, a reference to 64 KiB.
MAX_SKILL_BYTES = 32 * 1024

_SKILL_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DOC_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md")


@dataclass(frozen=True)
class ResourceSpec:
    """One exposed resource (or URI template, when ``template`` is set)."""

    uri: str
    name: str
    title: str
    description: str
    mime_type: str = MIME_MARKDOWN
    template: bool = False


@dataclass(frozen=True)
class ResourceContent:
    """One read resource: bounded text and its media type."""

    uri: str
    mime_type: str
    text: str


@dataclass(frozen=True)
class _SkillEntry:
    name: str
    description: str
    version: str
    tags: tuple[str, ...]
    references: tuple[str, ...]


class ResourceService:
    """Allowlisted, bounded reads from the configured knowledge roots.

    Args:
        skills_dir: Root of the skill content (``None`` disables the skill
            family). A missing directory simply yields an empty family.
        docs_dir: Root of the methodology documents (``None`` disables the
            document family).
        max_bytes: Hard limit for any single resource.
    """

    def __init__(
        self,
        *,
        skills_dir: Path | None = None,
        docs_dir: Path | None = None,
        max_bytes: int = MAX_RESOURCE_BYTES,
    ) -> None:
        self._skills_dir = Path(skills_dir) if skills_dir is not None else None
        self._docs_dir = Path(docs_dir) if docs_dir is not None else None
        self._max_bytes = max_bytes
        self._skills: dict[str, _SkillEntry] = _discover_skills(self._skills_dir)

    # -- discovery ----------------------------------------------------------

    def resources(self) -> tuple[ResourceSpec, ...]:
        """Concrete, listable resources (catalog + skills + refs + docs)."""
        specs: list[ResourceSpec] = []
        if self._skills:
            specs.append(
                ResourceSpec(
                    uri=CATALOG_URI,
                    name="skill-catalog",
                    title="Skill catalog",
                    description=(
                        "Catalog of the installed methodology skills (name, "
                        "description, version, tags)."
                    ),
                )
            )
        for entry in self._skills.values():
            specs.append(
                ResourceSpec(
                    uri=f"{SKILLS_PREFIX}/{entry.name}",
                    name=f"skill-{entry.name}",
                    title=f"Skill: {entry.name}",
                    description=entry.description,
                )
            )
            for reference in entry.references:
                specs.append(
                    ResourceSpec(
                        uri=f"{SKILLS_PREFIX}/{entry.name}/{REFERENCES_DIR}/{reference}",
                        name=f"skill-{entry.name}-{reference.replace('/', '-')}",
                        title=f"{entry.name} reference: {reference}",
                        description=(
                            f"Reference document of the {entry.name} skill "
                            "(read-only)."
                        ),
                    )
                )
        for name in _list_documents(self._docs_dir):
            specs.append(
                ResourceSpec(
                    uri=f"{DOCS_PREFIX}/{name}",
                    name=f"doc-{name}",
                    title=f"Document: {name}",
                    description="Platform methodology document (read-only).",
                )
            )
        return tuple(specs)

    def templates(self) -> tuple[ResourceSpec, ...]:
        """URI patterns for the families above (documentation/introspection).

        The server enumerates every resource concretely, so these patterns
        are advisory: a client can read any ``methodology://skills/...`` or
        ``methodology://docs/...`` URI that the reader's allowlist accepts.
        """
        specs: list[ResourceSpec] = []
        if self._skills:
            specs.append(
                ResourceSpec(
                    uri=f"{SKILLS_PREFIX}/{{name}}",
                    name="skill",
                    title="Skill body",
                    description="One skill's SKILL.md body (read-only).",
                    template=True,
                )
            )
            specs.append(
                ResourceSpec(
                    uri=f"{SKILLS_PREFIX}/{{name}}/{REFERENCES_DIR}/{{path}}",
                    name="skill-reference",
                    title="Skill reference",
                    description="One reference document of a skill (read-only).",
                    template=True,
                )
            )
        if _list_documents(self._docs_dir):
            specs.append(
                ResourceSpec(
                    uri=f"{DOCS_PREFIX}/{{name}}",
                    name="document",
                    title="Methodology document",
                    description="One platform document (read-only).",
                    template=True,
                )
            )
        return tuple(specs)

    # -- reads --------------------------------------------------------------

    def read(self, uri: Any) -> ResourceContent:
        """Read one resource by URI.

        Raises:
            ResourcePathError: Malformed URI, unknown scheme, traversal,
                absolute path or an out-of-allowlist name.
            ResourceNotFoundError: Well-formed but nothing to read.
            ResourceTooLargeError: The file exceeds the size limit.
        """
        text_uri = str(uri)
        segments = _split_uri(text_uri)
        head = segments[0]

        if head == "skills":
            return self._read_skill_uri(text_uri, segments[1:])
        if head == "docs":
            return self._read_doc_uri(text_uri, segments[1:])
        raise ResourcePathError(
            f"unsupported resource family in {text_uri!r}; expected "
            f"{SKILLS_PREFIX}/... or {DOCS_PREFIX}/..."
        )

    # -- skill family -------------------------------------------------------

    def _read_skill_uri(self, uri: str, rest: tuple[str, ...]) -> ResourceContent:
        if self._skills_dir is None:
            raise ResourceNotFoundError("skill resources are not configured")
        if not rest:
            raise ResourcePathError(f"skill resource needs a name: {uri!r}")
        if rest[0] == "catalog":
            if len(rest) != 1:
                raise ResourcePathError(f"malformed catalog URI: {uri!r}")
            if not self._skills:
                # Nothing is catalogued, so the catalog is not a resource:
                # reading a URI that is not listed stays "not found".
                raise ResourceNotFoundError("no skills are configured")
            return ResourceContent(uri=uri, mime_type=MIME_MARKDOWN, text=self._catalog())
        name = _validate_name(rest[0], _SKILL_NAME_RE, "skill")
        if len(rest) == 1:
            entry = self._skills.get(name)
            if entry is None:
                raise ResourceNotFoundError(f"unknown skill: {name!r}")
            path = self._skills_dir / name / SKILL_FILENAME
            return ResourceContent(
                uri=uri,
                mime_type=MIME_MARKDOWN,
                text=self._read_file(path, self._skills_dir, MAX_SKILL_BYTES, uri),
            )
        if len(rest) >= 3 and rest[1] == REFERENCES_DIR:
            entry = self._skills.get(name)
            if entry is None:
                raise ResourceNotFoundError(f"unknown skill: {name!r}")
            relative = "/".join(rest[2:])
            return ResourceContent(
                uri=uri,
                mime_type=MIME_MARKDOWN,
                text=self._read_reference(name, relative, uri),
            )
        raise ResourcePathError(f"malformed skill URI: {uri!r}")

    def _catalog(self) -> str:
        lines = ["# Methodology skills", ""]
        for entry in self._skills.values():
            lines.append(f"## {entry.name}")
            lines.append("")
            lines.append(entry.description)
            lines.append("")
            lines.append(f"- version: {entry.version}")
            if entry.tags:
                lines.append(f"- tags: {', '.join(entry.tags)}")
            lines.append(f"- uri: {SKILLS_PREFIX}/{entry.name}")
            if entry.references:
                lines.append(f"- references: {', '.join(entry.references)}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _read_reference(self, name: str, relative: str, uri: str) -> str:
        if not relative:
            raise ResourcePathError(f"reference path is empty: {uri!r}")
        skills_dir = self._skills_dir
        assert skills_dir is not None  # guarded by _read_skill_uri
        skill_dir = (skills_dir / name).resolve()
        target = (skill_dir / REFERENCES_DIR / relative).resolve()
        if not target.is_relative_to(skill_dir):
            raise ResourcePathError(
                f"reference path escapes the skill directory: {relative!r}"
            )
        return self._read_file(target, skill_dir, self._max_bytes, uri)

    # -- document family ----------------------------------------------------

    def _read_doc_uri(self, uri: str, rest: tuple[str, ...]) -> ResourceContent:
        if self._docs_dir is None:
            raise ResourceNotFoundError("document resources are not configured")
        if len(rest) != 1:
            raise ResourcePathError(f"malformed document URI: {uri!r}")
        name = _validate_name(rest[0], _DOC_NAME_RE, "document")
        if name not in _list_documents(self._docs_dir):
            raise ResourceNotFoundError(f"unknown document: {name!r}")
        path = self._docs_dir / name
        return ResourceContent(
            uri=uri,
            mime_type=MIME_MARKDOWN,
            text=self._read_file(path, self._docs_dir, self._max_bytes, uri),
        )

    # -- bounded read -------------------------------------------------------

    @staticmethod
    def _read_file(path: Path, root: Path, limit: int, uri: str) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ResourcePathError(f"resource escapes its root: {uri!r}")
        if not resolved.is_file():
            raise ResourceNotFoundError(f"resource not found: {uri!r}")
        size = resolved.stat().st_size
        if size > limit:
            raise ResourceTooLargeError(
                f"resource exceeds {limit} bytes ({size}): {uri!r}"
            )
        text = resolved.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > limit:  # decoded-size safety net
            raise ResourceTooLargeError(f"resource exceeds {limit} bytes: {uri!r}")
        return text


# -- helpers ----------------------------------------------------------------


def _split_uri(uri: str) -> tuple[str, ...]:
    """Split a methodology URI into validated segments.

    Rejects everything that is not a plain, relative, in-scheme path. This
    runs before any filesystem access - the reader never joins a string it
    has not validated.
    """
    if not isinstance(uri, str) or not uri:
        raise ResourcePathError("resource URI must be a non-empty string")
    if "\\" in uri:
        raise ResourcePathError(f"backslashes are not allowed in a URI: {uri!r}")
    if "%" in uri:
        raise ResourcePathError(f"percent-encoding is not allowed in a URI: {uri!r}")
    if not uri.startswith(f"{SCHEME}://"):
        raise ResourcePathError(
            f"unsupported URI scheme in {uri!r}; expected '{SCHEME}://'"
        )
    remainder = uri[len(f"{SCHEME}://") :]
    if not remainder or remainder.startswith("/"):
        raise ResourcePathError(f"malformed resource URI: {uri!r}")
    segments = tuple(remainder.split("/"))
    for segment in segments:
        if not segment:
            raise ResourcePathError(f"empty path segment in URI: {uri!r}")
        if segment in (".", ".."):
            raise ResourcePathError(f"relative path segment in URI: {uri!r}")
        if re.match(r"^[A-Za-z]:", segment):
            raise ResourcePathError(f"absolute drive path in URI: {uri!r}")
        if segment.strip() != segment:
            raise ResourcePathError(f"padded path segment in URI: {uri!r}")
    return segments


def _validate_name(name: str, pattern: re.Pattern[str], what: str) -> str:
    if not pattern.fullmatch(name):
        raise ResourcePathError(f"invalid {what} name: {name!r}")
    return name


def _list_documents(docs_dir: Path | None) -> tuple[str, ...]:
    if docs_dir is None or not docs_dir.is_dir():
        return ()
    return tuple(
        sorted(
            path.name
            for path in docs_dir.iterdir()
            if path.is_file() and _DOC_NAME_RE.fullmatch(path.name)
        )
    )


def _discover_skills(skills_dir: Path | None) -> dict[str, _SkillEntry]:
    """Index ``<skills_dir>/<name>/SKILL.md`` (validated, never executed)."""
    if skills_dir is None or not skills_dir.is_dir():
        return {}
    entries: dict[str, _SkillEntry] = {}
    for directory in sorted(skills_dir.iterdir()):
        if not directory.is_dir():
            continue
        skill_file = directory / SKILL_FILENAME
        if not skill_file.is_file():
            continue
        try:
            metadata = _read_frontmatter(skill_file)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            # A malformed skill is a configuration bug: fail loudly.
            raise ResourcePathError(
                f"invalid skill metadata in {directory.name}/{SKILL_FILENAME}: {exc}"
            ) from exc
        if metadata["name"] != directory.name:
            raise ResourcePathError(
                f"{directory.name}: frontmatter name {metadata['name']!r} does not "
                "match the directory"
            )
        entries[directory.name] = _SkillEntry(
            name=directory.name,
            description=metadata["description"],
            version=metadata["version"],
            tags=metadata["tags"],
            references=_list_references(directory),
        )
    return entries


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Minimal ``---`` frontmatter reader (M7.1 parity, data only)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = next(
            index for index in range(1, len(lines)) if lines[index].strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError("unterminated frontmatter") from exc
    front = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(front, dict):
        raise ValueError("frontmatter must be a mapping")
    tags = front.get("tags") or ()
    if isinstance(tags, str):
        tags = (tags,)
    return {
        "name": str(front.get("name", "")),
        "description": str(front.get("description", "")),
        "version": str(front.get("version", "")),
        "tags": tuple(str(tag) for tag in tags if str(tag).strip()),
    }


def _list_references(skill_dir: Path) -> tuple[str, ...]:
    references_dir = skill_dir / REFERENCES_DIR
    if not references_dir.is_dir():
        return ()
    return tuple(
        sorted(
            path.relative_to(references_dir).as_posix()
            for path in references_dir.rglob("*")
            if path.is_file()
        )
    )
