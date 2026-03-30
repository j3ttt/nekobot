"""System prompt builder — loads SOUL/USER/AGENTS from prompts dir, injects memory + runtime."""

from datetime import datetime
from pathlib import Path

from loguru import logger

from nekobot.memory.store import MemoryStore


class PromptBuilder:
    """
    Loads prompt files from a directory and assembles the system prompt.

    File load order: SOUL.md → USER.md → AGENTS.md
    Then appends: Memory (core + active) → Runtime (time + channel)

    Each file is re-read on every build() call so edits take effect
    without restarting.
    """

    PROMPT_FILES = ["SOUL.md", "USER.md", "AGENTS.md"]

    def __init__(self, prompts_dir: str | Path, memory_store: MemoryStore) -> None:
        self._dir = Path(prompts_dir)
        self._memory = memory_store

    def _load_prompt_files(self) -> list[str]:
        """Load all prompt files from the prompts directory."""
        parts: list[str] = []
        for filename in self.PROMPT_FILES:
            path = self._dir / filename
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
            else:
                logger.warning("Prompt file missing: {}", path)
        if not parts:
            logger.error("No prompt files found in {}", self._dir)
            parts.append("You are a helpful assistant.")
        return parts

    def build(self, channel: str, chat_id: str) -> str:
        """Build the full system prompt with injected memory and runtime."""
        parts = self._load_prompt_files()

        # Memory sections
        core = self._memory.render_core()
        active = self._memory.render_active()
        parts.append(f"## Memory — Core\n\n{core}")
        parts.append(f"## Memory — Active\n\n{active}")

        # Runtime
        runtime = (
            f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M')} ({datetime.now().strftime('%A')})\n"
            f"- Channel: {channel}\n"
            f"- Chat: {chat_id}"
        )
        parts.append(f"## Runtime\n{runtime}")

        return "\n\n---\n\n".join(parts)
