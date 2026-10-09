"""Tests for the ``aemp history`` CLI command (no EDA tools required)."""

from pathlib import Path

import pytest

from aemp.cli import main
from aemp.core.config import Settings
from aemp.runs.manager import RunManager


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake project workspace selected via AEMP_PROJECT_ROOT."""
    (tmp_path / "configs" / "tools").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    monkeypatch.setenv("AEMP_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_history_empty_project(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["history"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "No runs found." in output


def test_history_lists_runs(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = RunManager(Settings(project_root=project)).create_run("example")
    run.finalize("success")

    exit_code = main(["history"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "RUN ID" in output
    assert "STATUS" in output
    assert run.run_id in output
    assert "success" in output
    assert "example" in output


def test_history_respects_limit(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manager = RunManager(Settings(project_root=project))
    for _ in range(3):
        manager.create_run("example")

    exit_code = main(["history", "--limit", "2"])
    output = capsys.readouterr().out

    assert exit_code == 0
    listed = [line for line in output.splitlines() if "T" in line and "-" in line]
    assert len(listed) == 2


def test_history_invalid_limit_is_clean_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["history", "--limit", "0"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "--limit must be >= 1" in captured.err
    assert "Traceback" not in captured.err
