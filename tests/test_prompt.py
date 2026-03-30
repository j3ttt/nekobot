"""Tests for PromptBuilder."""

from pathlib import Path
from unittest.mock import MagicMock

from nekobot.gateway.prompt import PromptBuilder


class TestPromptBuilder:
    def _make_prompts_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "prompts"
        d.mkdir()
        (d / "SOUL.md").write_text("# Soul\nI am nekobot.")
        (d / "USER.md").write_text("# User\nName: tester")
        (d / "AGENTS.md").write_text("# Agents\nUse Read for files.")
        return d

    def test_assembles_all_sections(self, tmp_path: Path):
        prompts_dir = self._make_prompts_dir(tmp_path)
        store = MagicMock()
        store.render_core.return_value = "- name: test_user"
        store.render_active.return_value = "- project: testing"

        builder = PromptBuilder(prompts_dir, store)
        result = builder.build("telegram", "12345")

        assert "I am nekobot." in result
        assert "Name: tester" in result
        assert "Use Read for files." in result
        assert "- name: test_user" in result
        assert "- project: testing" in result
        assert "Channel: telegram" in result
        assert "Chat: 12345" in result

    def test_section_order(self, tmp_path: Path):
        prompts_dir = self._make_prompts_dir(tmp_path)
        store = MagicMock()
        store.render_core.return_value = "core_data"
        store.render_active.return_value = "active_data"

        builder = PromptBuilder(prompts_dir, store)
        result = builder.build("cli", "local")

        # Verify ordering: SOUL < USER < AGENTS < Memory Core < Memory Active < Runtime
        idx_soul = result.index("I am nekobot.")
        idx_user = result.index("Name: tester")
        idx_agents = result.index("Use Read for files.")
        idx_core = result.index("core_data")
        idx_active = result.index("active_data")
        idx_runtime = result.index("Channel: cli")

        assert idx_soul < idx_user < idx_agents < idx_core < idx_active < idx_runtime

    def test_missing_prompt_file(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "SOUL.md").write_text("# Soul only")
        # USER.md and AGENTS.md missing

        store = MagicMock()
        store.render_core.return_value = ""
        store.render_active.return_value = ""

        builder = PromptBuilder(prompts_dir, store)
        result = builder.build("telegram", "12345")

        assert "# Soul only" in result
        assert "Channel: telegram" in result

    def test_empty_prompts_dir_fallback(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        store = MagicMock()
        store.render_core.return_value = ""
        store.render_active.return_value = ""

        builder = PromptBuilder(prompts_dir, store)
        result = builder.build("telegram", "12345")

        assert "helpful assistant" in result
