"""SHA-256 helpers.

This module is the base of the future Input -> Artifact -> Run
traceability chain: every artifact recorded by the platform will carry a
digest produced here. Lineage bookkeeping itself is a later milestone.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> str:
    """Return the SHA-256 hex digest of the file at *path*.

    The file is read in chunks so arbitrarily large artifacts never
    exceed a fixed memory footprint.

    Args:
        path: File to digest.
        chunk_size: Read buffer size in bytes.

    Returns:
        Lowercase hex digest.

    Raises:
        FileNotFoundError: If *path* does not exist.
        IsADirectoryError: If *path* is a directory.
    """
    if path.is_dir():
        msg = f"Expected a file, got a directory: {path}"
        raise IsADirectoryError(msg)
    if not path.is_file():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
