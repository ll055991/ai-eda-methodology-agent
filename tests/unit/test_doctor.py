"""Tests for aemp doctor.

No EDA tool installation is required: executable discovery and version
probes are mocked. The only test touching the real filesystem reads the
repository's own ``configs/tools/tools.yaml``.
"""

import subprocess
from pathlib import Path

import pytest

from aemp import doctor
from aemp.core.config import Settings, find_project_root
from aemp.core.errors import ConfigurationError

TOOLS_YAML = """\
tools:
  yosys:
    executable: yosys
    version_args: ["-V"]
  opensta:
    executable: sta
    version_args: ["-version"]
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    tool_dir = tmp_path / "configs" / "tools"
    tool_dir.mkdir(parents=True)
    (tool_dir / "tools.yaml").write_text(TOOLS_YAML, encoding="utf-8")
    return Settings(project_root=tmp_path)


def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
    """Version probe stand-in that answers per tool, as a real tool would."""
    output = "Yosys 0.36+db\n" if "yosys" in argv[0] else "OpenSTA 2.6.0\n"
    return subprocess.CompletedProcess(argv, returncode=0, stdout=output, stderr="")


class TestRunDoctor:
    def test_all_tools_found(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(doctor.subprocess, "run", fake_run)

        report = doctor.run_doctor(settings)

        assert [tool.name for tool in report.tools] == ["yosys", "opensta"]
        assert all(tool.status == doctor.STATUS_OK for tool in report.tools)
        assert report.tools[0].version == "Yosys 0.36+db"
        assert report.tools[1].version == "OpenSTA 2.6.0"
        assert report.overall_status == doctor.OVERALL_READY

    def test_tool_missing(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
        monkeypatch.setattr(doctor.subprocess, "run", fake_run)

        report = doctor.run_doctor(settings)

        assert all(tool.status == doctor.STATUS_MISSING for tool in report.tools)
        assert all(tool.path is None for tool in report.tools)
        assert report.overall_status == doctor.OVERALL_NOT_READY

    def test_version_probe_failure_yields_unknown(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Tool exists but the version command produces no usable output:
        # version must be UNKNOWN, never guessed, and the tool still counts
        # as located.
        monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(
            doctor.subprocess,
            "run",
            lambda argv, **_k: subprocess.CompletedProcess(argv, 1, stdout="", stderr=""),
        )

        report = doctor.run_doctor(settings)

        assert all(tool.status == doctor.STATUS_OK for tool in report.tools)
        assert all(tool.version == doctor.VERSION_UNKNOWN for tool in report.tools)
        assert report.overall_status == doctor.OVERALL_READY

    def test_version_probe_timeout_yields_unknown(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/fake/bin/{name}")

        def timed_out(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1.0)

        monkeypatch.setattr(doctor.subprocess, "run", timed_out)

        report = doctor.run_doctor(settings)

        assert all(tool.version == doctor.VERSION_UNKNOWN for tool in report.tools)

    def test_probe_falls_back_to_stderr(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(
            doctor.subprocess,
            "run",
            lambda argv, **_k: subprocess.CompletedProcess(argv, 0, stdout="", stderr="v1.2.3\n"),
        )

        report = doctor.run_doctor(settings)

        assert report.tools[0].version == "v1.2.3"


class TestRenderReport:
    def test_missing_tool_renders_not_found_without_version(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

        text = doctor.render_report(doctor.run_doctor(settings))

        assert "NOT FOUND" in text
        assert "MISSING" in text
        assert "NOT READY" in text
        assert "Version" not in text.split("EDA Tools")[1].split("Overall")[0]

    def test_ready_report_layout(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(doctor.subprocess, "run", fake_run)

        text = doctor.render_report(doctor.run_doctor(settings))

        assert text.startswith("AEMP Environment Doctor")
        assert "Python" in text and "Platform" in text
        assert "READY" in text


class TestLoadToolSpecs:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            doctor.load_tool_specs(tmp_path / "nope.yaml")

    def test_empty_tools_mapping_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "tools.yaml"
        path.write_text("tools: {}\n", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            doctor.load_tool_specs(path)

    def test_non_string_executable_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "tools.yaml"
        path.write_text("tools:\n  yosys:\n    executable: 42\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="executable"):
            doctor.load_tool_specs(path)

    def test_non_string_version_args_raise(self, tmp_path: Path) -> None:
        path = tmp_path / "tools.yaml"
        path.write_text(
            "tools:\n  yosys:\n    executable: yosys\n    version_args: [-V]\n",
            encoding="utf-8",
        )
        # -V parses as the string "-V" here, so use a genuinely invalid value.
        path.write_text(
            "tools:\n  yosys:\n    executable: yosys\n    version_args: [42]\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="version_args"):
            doctor.load_tool_specs(path)

    def test_repository_tools_yaml_is_valid(self) -> None:
        # Guards the committed configs/tools/tools.yaml against regressions.
        root = find_project_root(Path(__file__))
        specs = doctor.load_tool_specs(Settings(project_root=root).tool_config)
        assert {"yosys", "openroad", "opensta", "klayout"} <= {spec.name for spec in specs}
