"""Shared test fixtures."""

import pytest
from pathlib import Path

from nekobot.memory.store import MemoryStore


@pytest.fixture
def tmp_memory(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore with a temporary directory."""
    return MemoryStore(tmp_path / "memory")


@pytest.fixture
def populated_memory(tmp_memory: MemoryStore) -> MemoryStore:
    """MemoryStore pre-populated with sample data."""
    tmp_memory.write_fact("profile", "name", "User")
    tmp_memory.write_fact("profile", "machine", "M3 Pro 18GB")
    tmp_memory.write_fact("preference", "language", "中文")
    tmp_memory.write_fact("project", "nekobot", "building AI assistant")
    tmp_memory.write_fact("learning", "asyncio", "asyncio.Queue is useful for decoupled messaging")
    tmp_memory.append_journal("Discussed nekobot architecture design")
    tmp_memory.append_journal("Implemented memory layer")
    return tmp_memory
