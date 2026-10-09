"""Tests for aemp.core.config."""

from pathlib import Path

import pytest

from aemp.core.config import ROOT_ENV_VAR, discover_settings, find_project_root, load_yaml
from aemp.core.errors import ConfigurationError


def make_project(tmp_path: Path) -> Path:
    """Create a minimal fake project tree with a pyproject.toml marker."""
    root = tmp_path / "proj"
    (root / "configs" / "tools").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    return root


class TestFindProjectRoot:
    def test_walks_up_from_nested_directory(self, tmp_path: Path) -> None:
        root = make_project(tmp_path)
        nested = root / "configs" / "tools"
        assert find_project_root(nested, env={}) == root

    def test_start_directory_itself_can_be_the_root(self, tmp_path: Path) -> None:
        root = make_project(tmp_path)
        assert find_project_root(root, env={}) == root

    def test_env_var_overrides_discovery(self, tmp_path: Path) -> None:
        root = make_project(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        found = find_project_root(elsewhere, env={ROOT_ENV_VAR: str(root)})
        assert found == root

    def test_env_var_must_point_to_existing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match=ROOT_ENV_VAR):
            find_project_root(tmp_path, env={ROOT_ENV_VAR: str(tmp_path / "nope")})

    def test_raises_when_no_marker_found(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="project root"):
            find_project_root(tmp_path, env={})


class TestDiscoverSettings:
    def test_layout_properties(self, tmp_path: Path) -> None:
        root = make_project(tmp_path)
        settings = discover_settings(start=root, env={})
        assert settings.project_root == root
        assert settings.config_dir == root / "configs"
        assert settings.flow_dir == root / "configs" / "flows"
        assert settings.rule_dir == root / "configs" / "rules"
        assert settings.tool_config == root / "configs" / "tools" / "tools.yaml"


class TestLoadYaml:
    def test_loads_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("tools:\n  yosys:\n    executable: yosys\n", encoding="utf-8")
        assert load_yaml(path) == {"tools": {"yosys": {"executable": "yosys"}}}

    def test_empty_file_loads_as_empty_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        assert load_yaml(path) == {}

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            load_yaml(tmp_path / "nope.yaml")

    def test_non_mapping_top_level_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="mapping"):
            load_yaml(path)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.yaml"
        path.write_text("a: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="Invalid YAML"):
            load_yaml(path)
