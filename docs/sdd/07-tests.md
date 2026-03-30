# SDD-07: Test Suite

## Priority: MEDIUM
## Depends On: SDD-03 (working Gateway)
## Estimated Scope: 5 new files, ~300 lines

---

## 1. Goal

Create unit tests for the modules that can be tested without the Claude Agent SDK. Integration tests for the SDK-dependent parts are covered by SDD-03's smoke test scripts.

## 2. Test Strategy

| Module | Testable Without SDK? | Test Type |
|--------|----------------------|-----------|
| `memory/store.py` | Yes | Unit |
| `memory/extractor.py` | Yes | Unit |
| `memory/search.py` | Yes | Unit |
| `gateway/prompt.py` | Yes (mock MemoryStore) | Unit |
| `bus/events.py` | Yes | Unit |
| `bus/queue.py` | Yes | Unit |
| `config/loader.py` | Yes | Unit |
| `usage/tracker.py` | Yes | Unit |
| `gateway/router.py` | No (needs SDK) | Integration (SDD-03) |
| `gateway/tools.py` | No (needs SDK) | Integration (SDD-03) |
| `channels/telegram.py` | No (needs Telegram) | Manual |

## 3. Test Directory Structure

```
tests/
├── __init__.py
├── conftest.py           # Shared fixtures (tmp dirs, sample data)
├── test_memory_store.py  # MemoryStore read/write/render
├── test_extractor.py     # <memory_write> extraction
├── test_search.py        # Archive keyword search
├── test_prompt.py        # PromptBuilder template injection
└── test_bus.py           # MessageBus publish/consume
```

## 4. Implementation

### 4.1 `tests/conftest.py`

```python
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
```

### 4.2 `tests/test_memory_store.py`

```python
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
```

### 4.3 `tests/test_extractor.py`

```python
"""Tests for memory_write extraction."""

from nekobot.memory.extractor import extract_memory_writes


class TestExtractor:
    def test_basic_extraction(self):
        response = """Here's my response.

<memory_write>
- profile.name: User
- project.nekobot: architecture done
</memory_write>

Done."""
        cleaned, facts = extract_memory_writes(response)
        assert "memory_write" not in cleaned
        assert "Here's my response." in cleaned
        assert "Done." in cleaned
        assert len(facts) == 2
        assert facts[0] == ("profile", "name", "User")
        assert facts[1] == ("project", "nekobot", "architecture done")

    def test_no_memory_write(self):
        response = "Just a normal response."
        cleaned, facts = extract_memory_writes(response)
        assert cleaned == response
        assert facts == []

    def test_multiple_blocks(self):
        response = """First part.

<memory_write>
- profile.age: 25
</memory_write>

Middle part.

<memory_write>
- project.foo: done
</memory_write>

End."""
        cleaned, facts = extract_memory_writes(response)
        assert len(facts) == 2
        assert "First part." in cleaned
        assert "Middle part." in cleaned
        assert "End." in cleaned

    def test_no_category_prefix(self):
        response = """<memory_write>
- some_key: some_value
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert facts[0] == ("active", "some_key", "some_value")

    def test_colon_in_value(self):
        response = """<memory_write>
- profile.url: https://example.com
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert facts[0] == ("profile", "url", "https://example.com")

    def test_empty_block(self):
        response = """<memory_write>
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert facts == []

    def test_malformed_line_skipped(self):
        response = """<memory_write>
- profile.name: valid
- no_colon_here
- project.foo: also valid
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert len(facts) == 2
```

### 4.4 `tests/test_search.py`

```python
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
```

### 4.5 `tests/test_prompt.py`

```python
"""Tests for PromptBuilder."""

from pathlib import Path
from unittest.mock import MagicMock

from nekobot.gateway.prompt import PromptBuilder


class TestPromptBuilder:
    def test_placeholder_replacement(self, tmp_path: Path):
        template = tmp_path / "system_prompt.md"
        template.write_text(
            "# Test\n\n## Core\n{MEMORY_CORE}\n\n## Active\n{MEMORY_ACTIVE}\n\n## Runtime\n{RUNTIME}"
        )

        store = MagicMock()
        store.render_core.return_value = "- name: test_user"
        store.render_active.return_value = "- project: testing"

        builder = PromptBuilder(template, store)
        result = builder.build("telegram", "12345")

        assert "- name: test_user" in result
        assert "- project: testing" in result
        assert "Channel: telegram" in result
        assert "Chat: 12345" in result
        assert "{MEMORY_CORE}" not in result
        assert "{MEMORY_ACTIVE}" not in result
        assert "{RUNTIME}" not in result

    def test_missing_template(self, tmp_path: Path):
        builder = PromptBuilder(tmp_path / "nonexistent.md", MagicMock())
        # Currently this would raise FileNotFoundError.
        # After SDD-06, it should return a fallback prompt.
```

### 4.6 `tests/test_bus.py`

```python
"""Tests for MessageBus."""

import asyncio

import pytest

from nekobot.bus.events import InboundMessage, OutboundMessage
from nekobot.bus.queue import MessageBus


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_publish_consume_inbound(self):
        bus = MessageBus()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        await bus.publish_inbound(msg)
        result = await bus.consume_inbound()
        assert result.content == "hello"
        assert result.session_key == "test:c1"

    @pytest.mark.asyncio
    async def test_publish_consume_outbound(self):
        bus = MessageBus()
        msg = OutboundMessage(channel="test", chat_id="c1", content="reply")
        await bus.publish_outbound(msg)
        result = await bus.consume_outbound()
        assert result.content == "reply"

    @pytest.mark.asyncio
    async def test_session_key_override(self):
        msg = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="c1",
            content="hi", session_key_override="custom:key",
        )
        assert msg.session_key == "custom:key"
```

## 5. Running Tests

```bash
cd /path/to/nekobot
pip install -e ".[dev]"
pytest tests/ -v
```

## 6. Acceptance Criteria

- [x] `tests/` directory created with all files above
- [x] `pytest tests/ -v` passes all tests (29 passed)
- [x] Test coverage for: MemoryStore CRUD, extractor parsing, archive search, prompt building, message bus
- [x] No tests depend on Claude Agent SDK or network access
- [x] Tests use `tmp_path` fixture (no filesystem side effects)
