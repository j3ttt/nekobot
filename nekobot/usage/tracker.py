"""Usage tracking — records cost and token usage from Claude Agent SDK ResultMessage."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class UsageTracker:
    """Append-only JSONL usage log."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "usage.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        session_id: str,
        channel: str,
        cost_usd: float | None = None,
        usage: Any = None,
        num_turns: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "channel": channel,
        }
        if cost_usd is not None:
            entry["cost_usd"] = cost_usd
        if usage:
            entry["input_tokens"] = getattr(usage, "input_tokens", 0)
            entry["output_tokens"] = getattr(usage, "output_tokens", 0)
            entry["cache_read_tokens"] = getattr(usage, "cache_read_tokens", 0)
            entry["cache_creation_tokens"] = getattr(usage, "cache_creation_tokens", 0)
        if num_turns is not None:
            entry["num_turns"] = num_turns
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms

        with open(self._path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.debug(
            "Usage: session={} cost=${} turns={}",
            session_id[:8] if session_id else "?",
            cost_usd or "?",
            num_turns or "?",
        )
