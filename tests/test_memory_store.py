"""Tests for MemoryStore."""

import json
from pathlib import Path

from nekobot.memory.store import MemoryStore


class TestMemoryWrite:
    def test_write_core_fact(self, tmp_memory: MemoryStore):
        tmp_memory.write_fact("profile", "name", "test_user")
        core = tmp_memory.load_core()
        assert core["profile"]["name"] == "test_user"

    def test_write_active_fact(self, tmp_memory: MemoryStore):
        tmp_memory.write_fact("project", "foo", "building foo")
        active = tmp_memory.load_active()
        assert active["project"]["foo"] == "building foo"

    def test_write_archive(self, tmp_memory: MemoryStore):
        tmp_memory.write_fact("learning", "python_tips", "Use generators for memory efficiency")
        archive_file = tmp_memory._archive_path / "learning" / "python_tips.md"
        assert archive_file.exists()
        assert "generators" in archive_file.read_text()

    def test_write_unknown_category_defaults_to_active(self, tmp_memory: MemoryStore):
        tmp_memory.write_fact("unknown_cat", "key", "value")
        active = tmp_memory.load_active()
        assert active["unknown_cat"]["key"] == "value"

    def test_write_multiple_facts(self, tmp_memory: MemoryStore):
        facts = [
            ("profile", "name", "User"),
            ("project", "nekobot", "in progress"),
            ("learning", "rust", "ownership system"),
        ]
        tmp_memory.write_facts(facts)
        assert tmp_memory.load_core()["profile"]["name"] == "User"
        assert tmp_memory.load_active()["project"]["nekobot"] == "in progress"
        assert (tmp_memory._archive_path / "learning" / "rust.md").exists()

    def test_upsert_overwrites(self, tmp_memory: MemoryStore):
        tmp_memory.write_fact("profile", "name", "old_name")
        tmp_memory.write_fact("profile", "name", "new_name")
        assert tmp_memory.load_core()["profile"]["name"] == "new_name"


class TestMemoryRead:
    def test_load_empty(self, tmp_memory: MemoryStore):
        assert tmp_memory.load_core() == {}
        assert tmp_memory.load_active() == {}
        assert tmp_memory.load_journal() == []

    def test_render_core(self, populated_memory: MemoryStore):
        rendered = populated_memory.render_core()
        assert "User" in rendered
        assert "M3 Pro" in rendered
        assert "中文" in rendered

    def test_render_active(self, populated_memory: MemoryStore):
        rendered = populated_memory.render_active()
        assert "nekobot" in rendered
        assert "architecture" in rendered  # from journal

    def test_render_empty(self, tmp_memory: MemoryStore):
        assert "no core memory" in tmp_memory.render_core()
        assert "no active memory" in tmp_memory.render_active()


class TestJournal:
    def test_append_and_load(self, tmp_memory: MemoryStore):
        tmp_memory.append_journal("entry 1")
        tmp_memory.append_journal("entry 2")
        tmp_memory.append_journal("entry 3")
        entries = tmp_memory.load_journal(limit=2)
        assert len(entries) == 2
        assert entries[0]["summary"] == "entry 2"
        assert entries[1]["summary"] == "entry 3"

    def test_journal_limit(self, tmp_memory: MemoryStore):
        for i in range(10):
            tmp_memory.append_journal(f"entry {i}")
        entries = tmp_memory.load_journal(limit=5)
        assert len(entries) == 5
        assert entries[0]["summary"] == "entry 5"
