"""Layered memory store: core, active, archive, journal."""

import json
from datetime import datetime
from pathlib import Path

from loguru import logger


class MemoryStore:
    """
    Manages layered long-term memory.

    Layers:
        core.json     — stable facts (profile, preference, relationship)
        active.json   — volatile context (project, todo, recent_event)
        archive/      — searchable knowledge (learning, tech_detail, reference)
        journal.jsonl — append-only conversation summaries
    """

    def __init__(self, memory_path: Path) -> None:
        self.root = memory_path
        self.root.mkdir(parents=True, exist_ok=True)

        self._core_path = self.root / "core.json"
        self._active_path = self.root / "active.json"
        self._archive_path = self.root / "archive"
        self._journal_path = self.root / "journal.jsonl"

        # Ensure directories exist
        for sub in ("archive/learning", "archive/tech_detail", "archive/reference"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load_core(self) -> dict:
        return self._load_json(self._core_path)

    def load_active(self) -> dict:
        return self._load_json(self._active_path)

    def load_journal(self, limit: int = 5) -> list[dict]:
        """Load the most recent journal entries."""
        if not self._journal_path.exists():
            return []
        lines = self._journal_path.read_text().strip().splitlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    # ------------------------------------------------------------------
    # Render for prompt injection
    # ------------------------------------------------------------------

    def render_core(self) -> str:
        """Render core.json as markdown for system prompt injection."""
        data = self.load_core()
        if not data:
            return "(no core memory yet)"
        return self._dict_to_markdown(data)

    def render_active(self) -> str:
        """Render active.json + recent journal as markdown."""
        parts = []

        active = self.load_active()
        if active:
            parts.append(self._dict_to_markdown(active))

        journal = self.load_journal(limit=5)
        if journal:
            parts.append("### Recent")
            for entry in journal:
                ts = entry.get("timestamp", "")
                summary = entry.get("summary", "")
                parts.append(f"- [{ts}] {summary}")

        return "\n\n".join(parts) if parts else "(no active memory yet)"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_fact(self, category: str, key: str, value: str) -> None:
        """Write a single fact to the appropriate layer.

        Supports two formats:
          - Direct category: category="preference", key="style", value="..."
          - Layer prefix:    category="core", key="preference.style", value="..."
                             category="archive", key="nanobot", value="..."
        """
        core_categories = {"profile", "preference", "relationship"}
        active_categories = {"project", "todo", "recent_event"}
        archive_categories = {"learning", "tech_detail", "reference"}

        # Handle layer-prefixed writes: core.xxx / archive.xxx
        if category == "core":
            # key may be "preference.style" → sub_cat="preference", real_key="style"
            if "." in key:
                sub_cat, real_key = key.split(".", 1)
            else:
                sub_cat, real_key = key, key
            self._upsert_json(self._core_path, sub_cat, real_key, value)
            return
        if category == "archive":
            # Archive to default "reference" subdir, auto-clean active
            self._write_archive("reference", key, value)
            return
        if category == "active":
            # key may be "recent_event.xxx" → sub_cat="recent_event", real_key="xxx"
            if "." in key:
                sub_cat, real_key = key.split(".", 1)
            else:
                sub_cat, real_key = key, key
            self._upsert_json(self._active_path, sub_cat, real_key, value)
            return

        if category in core_categories:
            self._upsert_json(self._core_path, category, key, value)
        elif category in active_categories:
            self._upsert_json(self._active_path, category, key, value)
        elif category in archive_categories:
            self._write_archive(category, key, value)
        else:
            logger.warning("Unknown memory category '{}', writing to active", category)
            self._upsert_json(self._active_path, category, key, value)

    def write_facts(self, facts: list[tuple[str, str, str]]) -> None:
        """Write multiple (category, key, value) facts."""
        for category, key, value in facts:
            self.write_fact(category, key, value)

    def append_journal(self, summary: str) -> None:
        """Append a conversation summary to the journal."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
        }
        with open(self._journal_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _save_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def _upsert_json(self, path: Path, category: str, key: str, value: str) -> None:
        data = self._load_json(path)
        data.setdefault(category, {})[key] = value
        self._save_json(path, data)
        logger.debug("Memory write: {}.{} = {}", category, key, value)

    def _write_archive(self, category: str, key: str, value: str) -> None:
        """Write an archive entry as a standalone .md file.

        Also removes the same key from active.json if it exists,
        so archiving automatically cleans up active memory.
        """
        dir_path = self._archive_path / category
        dir_path.mkdir(parents=True, exist_ok=True)
        safe_key = key.replace("/", "_").replace(" ", "_")
        file_path = dir_path / f"{safe_key}.md"
        file_path.write_text(f"# {key}\n\n{value}\n")
        logger.debug("Archive write: {}/{}", category, safe_key)

        # Auto-clean: remove from active.json if same key exists
        self._remove_from_active(category, key)

    def _remove_from_active(self, category: str, key: str) -> bool:
        """Remove a key from active.json. Returns True if something was removed."""
        active = self._load_json(self._active_path)
        # Check all active categories for matching key
        for cat in list(active.keys()):
            if isinstance(active[cat], dict) and key in active[cat]:
                del active[cat][key]
                if not active[cat]:
                    del active[cat]
                self._save_json(self._active_path, active)
                logger.info("Auto-cleaned active.{}.{} after archive write", cat, key)
                return True
        return False

    def archive_active_items(self, keys: list[tuple[str, str]]) -> int:
        """Move (category, key) pairs from active.json to archive/.

        Returns number of items archived.
        """
        active = self._load_json(self._active_path)
        count = 0
        for cat, key in keys:
            if cat in active and isinstance(active[cat], dict) and key in active[cat]:
                value = active[cat].pop(key)
                self._write_archive(cat, key, str(value))
                count += 1
                if not active[cat]:
                    del active[cat]
        if count:
            self._save_json(self._active_path, active)
        return count

    @staticmethod
    def _dict_to_markdown(data: dict) -> str:
        """Convert a nested dict to markdown sections."""
        lines = []
        for section, entries in data.items():
            lines.append(f"### {section}")
            if isinstance(entries, dict):
                for k, v in entries.items():
                    lines.append(f"- {k}: {v}")
            else:
                lines.append(f"- {entries}")
            lines.append("")
        return "\n".join(lines).strip()
