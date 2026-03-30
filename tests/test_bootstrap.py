"""Tests for bootstrap.ensure_home()."""

from pathlib import Path

from nekobot.bootstrap import ensure_home, _DIRS, _SEED_FILES, _SYMLINKS


class TestEnsureHome:
    def test_creates_directories(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        ensure_home(home)

        for d in _DIRS:
            assert (home / d).is_dir()

    def test_seeds_default_files(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        ensure_home(home)

        for _, dst_rel in _SEED_FILES:
            dst = home / dst_rel
            assert dst.exists(), f"Missing seeded file: {dst_rel}"
            assert dst.stat().st_size > 0

    def test_does_not_overwrite_existing(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        home.mkdir()
        (home / "prompts").mkdir()

        custom = home / "prompts" / "SOUL.md"
        custom.write_text("my custom soul")

        ensure_home(home)

        assert custom.read_text() == "my custom soul"

    def test_idempotent(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        ensure_home(home)
        ensure_home(home)

        for d in _DIRS:
            assert (home / d).is_dir()

    def test_returns_home_path(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        result = ensure_home(home)
        assert result == home

    def test_creates_symlinks(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        ensure_home(home)

        for link_rel, target in _SYMLINKS:
            link = home / link_rel
            assert link.is_symlink(), f"Expected symlink: {link_rel}"
            assert str(link.readlink()) == target

    def test_symlink_resolves_to_real_dir(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        ensure_home(home)

        # A file in workspace/skills/ should be visible via .claude/skills/
        skills_dir = home / "workspace" / "skills"
        (skills_dir / "test-skill").mkdir()
        (skills_dir / "test-skill" / "SKILL.md").write_text("test")

        via_symlink = home / "workspace" / ".claude" / "skills" / "test-skill" / "SKILL.md"
        assert via_symlink.exists()
        assert via_symlink.read_text() == "test"

    def test_migrates_existing_real_directory(self, tmp_path: Path):
        home = tmp_path / ".nekobot"
        ws = home / "workspace"
        # Simulate old bootstrap: .claude/skills is a real directory with content
        old_skills = ws / ".claude" / "skills" / "my-skill"
        old_skills.mkdir(parents=True)
        (old_skills / "SKILL.md").write_text("my skill content")

        ensure_home(home)

        # Old content should be migrated to workspace/skills/
        assert (ws / "skills" / "my-skill" / "SKILL.md").read_text() == "my skill content"
        # .claude/skills should now be a symlink
        assert (ws / ".claude" / "skills").is_symlink()
