"""File evidence recording: hash files into metadata records (never copy data)."""

from __future__ import annotations

from pathlib import Path

from aemp.core.hashing import sha256_file
from aemp.runs.models import FileRecord

__all__ = ["record_file"]


def record_file(path: Path, role: str, *, base: Path) -> FileRecord:
    """Build a :class:`FileRecord` for *path*.

    Non-absolute *path* values are resolved against *base* (the project
    root for inputs, the run directory for artifacts). The recorded path
    is stored relative to *base* whenever the file lives under it, using
    POSIX-style separators (portable, JSON-friendly); otherwise the
    absolute path is recorded, which is the only honest option for files
    outside the workspace.

    Missing files are recorded with ``exists=False`` and without
    ``bytes``/``sha256`` - a hash is never fabricated.

    Args:
        path: File location to record (absolute, or relative to *base*).
        role: Evidence role (e.g. ``input``, ``rtl``, ``netlist``).
        base: Directory *path* is resolved against and stored relative to.

    Returns:
        The immutable evidence record.
    """
    actual = path if path.is_absolute() else base / path
    exists = actual.is_file()
    size: int | None = None
    digest: str | None = None
    if exists:
        size = actual.stat().st_size
        digest = sha256_file(actual)
    return FileRecord(
        role=role, path=_display_path(actual, base), exists=exists, bytes=size, sha256=digest
    )


def _display_path(actual: Path, base: Path) -> str:
    """Path as recorded in JSON: *base*-relative when possible, else absolute."""
    resolved = actual.resolve()
    try:
        return resolved.relative_to(base.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()
