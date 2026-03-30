"""Tests for config loader."""

from pathlib import Path

from nekobot.config.loader import load_config


class TestLoadConfig:
    def test_explicit_path(self, tmp_path: Path):
        cfg_file = tmp_path / "my_config.yaml"
        cfg_file.write_text("gateway:\n  workspace: /tmp/ws\n")

        config = load_config(cfg_file)
        assert config.gateway.workspace == "/tmp/ws"

    def test_explicit_path_missing(self, tmp_path: Path):
        config = load_config(tmp_path / "nonexistent.yaml")
        # Falls back to defaults
        assert config.gateway.workspace == "~/.nekobot/workspace"

    def test_default_search_cwd(self, tmp_path: Path, monkeypatch):
        import nekobot.config.loader as loader_mod

        # Place config.yaml in cwd, override search paths to skip ~/.nekobot/
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(loader_mod, "_SEARCH_PATHS", [tmp_path / "config.yaml"])
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("gateway:\n  workspace: /tmp/cwd_ws\n")

        config = load_config()
        assert config.gateway.workspace == "/tmp/cwd_ws"

    def test_no_config_returns_defaults(self, tmp_path: Path, monkeypatch):
        import nekobot.config.loader as loader_mod

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(loader_mod, "_SEARCH_PATHS", [tmp_path / "nonexistent.yaml"])

        config = load_config()
        assert config.gateway.prompts_dir == "~/.nekobot/prompts"

    def test_unknown_fields_ignored(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("gateway:\n  system_prompt_path: old_value\n  workspace: /tmp/ws\n")

        config = load_config(cfg_file)
        assert config.gateway.workspace == "/tmp/ws"
        assert not hasattr(config.gateway, "system_prompt_path")
