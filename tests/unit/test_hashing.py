"""Tests for aemp.core.hashing."""

import hashlib
from pathlib import Path

import pytest

from aemp.core.hashing import sha256_bytes, sha256_file

# Well-known digests used as fixed vectors.
_SHA256_HELLO = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
_SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sha256_bytes_known_vector() -> None:
    assert sha256_bytes(b"hello") == _SHA256_HELLO


def test_sha256_file_known_content(tmp_path: Path) -> None:
    path = tmp_path / "hello.txt"
    path.write_bytes(b"hello")
    assert sha256_file(path) == _SHA256_HELLO


def test_sha256_file_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert sha256_file(path) == _SHA256_EMPTY


def test_sha256_file_reads_in_chunks(tmp_path: Path) -> None:
    # 4 MiB payload, larger than the default 1 MiB chunk: exercises the
    # streaming loop and must match a one-shot digest.
    payload = b"\xde\xad\xbe\xef" * (1024 * 1024)
    path = tmp_path / "big.bin"
    path.write_bytes(payload)
    assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing.bin"):
        sha256_file(tmp_path / "missing.bin")


def test_sha256_file_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(IsADirectoryError):
        sha256_file(tmp_path)
