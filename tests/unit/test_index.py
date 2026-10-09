"""Tests for the SQLite run index (stdlib sqlite3; filesystem stays source of truth)."""

import json
import sqlite3
from pathlib import Path

import pytest

from aemp.core.config import Settings
from aemp.core.errors import InvalidRequestError, StorageError
from aemp.runs.index import INDEX_FILENAME, RunIndex
from aemp.runs.manager import RunManager


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(project_root=tmp_path)


def test_db_path_is_inside_runs_dir(settings: Settings) -> None:
    index = RunManager(settings).index
    assert index.db_path == settings.runs_dir / INDEX_FILENAME
    assert INDEX_FILENAME == "index.sqlite3"


def test_list_runs_empty(settings: Settings) -> None:
    assert RunIndex(settings.runs_dir / INDEX_FILENAME).list_runs() == []


def test_list_runs_limit(settings: Settings) -> None:
    manager = RunManager(settings)
    for _ in range(3):
        manager.create_run("example")
    rows = manager.index.list_runs(limit=2)
    assert len(rows) == 2


def test_explicit_timestamps_order_deterministically(settings: Settings) -> None:
    manager = RunManager(settings)
    # Same real created_at second; id ordering must break the tie.
    manager.create_run("example", run_id="20261001T000000Z-aaaaaa")
    manager.create_run("example", run_id="20261002T000000Z-bbbbbb")
    rows = manager.index.list_runs()
    assert [row["run_id"] for row in rows] == [
        "20261002T000000Z-bbbbbb",
        "20261001T000000Z-aaaaaa",
    ]


def test_artifacts_persisted_in_index(settings: Settings) -> None:
    run = RunManager(settings).create_run("example")
    artifact = run.run_dir / "artifacts" / "demo.txt"
    artifact.write_bytes(b"payload\n")
    run.add_artifact(artifact, role="netlist")

    conn = sqlite3.connect(settings.runs_dir / INDEX_FILENAME)
    try:
        rows = conn.execute(
            'SELECT run_id, role, path, "exists", bytes FROM artifacts'
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(run.run_id, "netlist", "artifacts/demo.txt", 1, 8)]


def test_invalid_status_rejected(settings: Settings) -> None:
    index = RunIndex(settings.runs_dir / INDEX_FILENAME)
    with pytest.raises(InvalidRequestError, match="Invalid run status"):
        index.upsert_run(
            run_id="20261007T103000Z-a1b2c3",
            status="paused",  # lifecycle states are a closed set
            created_at="2026-10-07T10:30:00Z",
            started_at="2026-10-07T10:30:00Z",
            finished_at=None,
            operation="example",
            engineering_status="unknown",
            run_dir="runs/20261007T103000Z-a1b2c3",
        )


def test_unwritable_index_raises_storage_error(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the index directory should be", encoding="utf-8")
    index = RunIndex(blocker / "index.sqlite3")  # parent exists as a FILE
    with pytest.raises(StorageError, match="Cannot open run index"):
        index.list_runs()


def test_rebuild_reconstructs_index_from_filesystem(settings: Settings) -> None:
    manager = RunManager(settings)
    first = manager.create_run("example", run_id="20261001T000000Z-aaaaaa")
    first.finalize("success")
    manager.create_run("example", run_id="20261002T000000Z-bbbbbb")  # stays created

    db_path = settings.runs_dir / INDEX_FILENAME
    db_path.unlink()  # simulate a lost/corrupt index

    rebuilt = RunIndex(db_path).rebuild(settings.runs_dir)
    assert rebuilt == 2

    rows = RunIndex(db_path).list_runs()
    by_id = {row["run_id"]: row for row in rows}
    assert by_id["20261001T000000Z-aaaaaa"]["status"] == "success"
    assert by_id["20261002T000000Z-bbbbbb"]["status"] == "created"


def test_rebuild_skips_corrupt_run_directories(
    settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manager = RunManager(settings)
    manager.create_run("example", run_id="20261001T000000Z-aaaaaa")

    broken_dir = settings.runs_dir / "20261003T000000Z-cccccc"
    broken_dir.mkdir(parents=True)
    (broken_dir / "run.json").write_text("{not json", encoding="utf-8")

    invalid_dir = settings.runs_dir / "20261004T000000Z-dddddd"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "run.json").write_text(
        json.dumps({"schema": "aemp.result/v0"}), encoding="utf-8"  # missing required fields
    )

    count = RunIndex(settings.runs_dir / INDEX_FILENAME).rebuild(settings.runs_dir)
    assert count == 1
    assert any("20261003T000000Z-cccccc" in message for message in caplog.messages)
