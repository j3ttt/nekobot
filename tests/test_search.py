"""Tests for archive keyword search."""

from pathlib import Path

from nekobot.memory.search import search_archive


class TestArchiveSearch:
    def _populate(self, archive_path: Path):
        archive_path.mkdir(parents=True, exist_ok=True)
        (archive_path / "learning").mkdir()
        (archive_path / "learning" / "python_asyncio.md").write_text(
            "# Python Asyncio\n\nasyncio.Queue is useful for decoupled messaging patterns."
        )
        (archive_path / "learning" / "rust_ownership.md").write_text(
            "# Rust Ownership\n\nRust uses ownership and borrowing to manage memory safely."
        )
        (archive_path / "tech_detail").mkdir()
        (archive_path / "tech_detail" / "docker_compose.md").write_text(
            "# Docker Compose\n\nUse docker compose for multi-container orchestration."
        )

    def test_keyword_match(self, tmp_path: Path):
        archive = tmp_path / "archive"
        self._populate(archive)
        results = search_archive(archive, "asyncio")
        assert len(results) >= 1
        assert results[0]["title"] == "Python Asyncio"

    def test_no_match(self, tmp_path: Path):
        archive = tmp_path / "archive"
        self._populate(archive)
        results = search_archive(archive, "kubernetes")
        assert results == []

    def test_multi_keyword(self, tmp_path: Path):
        archive = tmp_path / "archive"
        self._populate(archive)
        results = search_archive(archive, "rust ownership memory")
        assert len(results) >= 1
        assert "Rust" in results[0]["title"]

    def test_max_results(self, tmp_path: Path):
        archive = tmp_path / "archive"
        self._populate(archive)
        results = search_archive(archive, "the", max_results=1)
        assert len(results) <= 1

    def test_empty_archive(self, tmp_path: Path):
        archive = tmp_path / "archive"
        results = search_archive(archive, "anything")
        assert results == []
