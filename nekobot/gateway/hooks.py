"""PreCompact hook: intercept default compaction, replace with /memorizing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

if TYPE_CHECKING:
    from nekobot.bus.queue import MessageBus
    from nekobot.memory.store import MemoryStore

MEMORIZING_PROMPT_PATH = Path("~/.nekobot/prompts/MEMORIZING.md").expanduser()


class PreCompactHook:
    """Intercepts auto/manual compaction, replaces with /compact <MEMORIZING.md>.

    Re-entry guard: /memorizing triggers /compact with custom_instructions,
    which fires this hook again. PreCompactHookInput.custom_instructions
    will be non-null, so we allow it through. No flag needed.

    Ref: claude-agent-sdk PreCompactHookInput:
        hook_event_name: "PreCompact"
        trigger: "manual" | "auto"
        custom_instructions: str | None
    """

    def __init__(
        self,
        memory: MemoryStore,
        bus: MessageBus,
        session_lookup: Callable[[str], tuple[str, str] | None],
    ) -> None:
        self.memory = memory
        self.bus = bus
        self._session_lookup = session_lookup  # session_id → (channel, chat_id)

    async def __call__(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = input_data.get("session_id", "")
        trigger = input_data.get("trigger", "auto")
        custom = input_data.get("custom_instructions")

        # If /compact was called with custom instructions (e.g., from memorize tool),
        # it's already a custom compact — don't intercept
        if custom:
            logger.info("PreCompact hook: custom instructions present, allowing compact (session={})", session_id[:8])
            return {}

        logger.info("PreCompact hook fired (trigger={}, session={})", trigger, session_id[:8])

        # Notify user
        location = self._session_lookup(session_id)
        if location:
            channel, chat_id = location
            from nekobot.bus.events import OutboundMessage
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=f"🧠 整理记忆中... (trigger: {trigger})",
                )
            )

            # Schedule /compact with MEMORIZING.md via bus (same path as memorize tool)
            try:
                instructions = MEMORIZING_PROMPT_PATH.read_text()
            except FileNotFoundError:
                logger.error("MEMORIZING.md not found at {}", MEMORIZING_PROMPT_PATH)
                return {}  # allow default compact as fallback

            from nekobot.bus.events import InboundMessage
            session_key = f"{channel}:{chat_id}"
            await self.bus.publish_inbound(
                InboundMessage(
                    channel="system",
                    sender_id="system",
                    chat_id="memorize",
                    content=f"/compact {instructions}",
                    session_key_override=session_key,
                )
            )
            logger.info("PreCompact hook: scheduled /compact for session {}", session_key)
        else:
            logger.warning("Cannot resolve session {}, allowing default compact", session_id[:8])
            return {}  # allow default compact as fallback

        # Block default compaction
        return {"decision": "block", "reason": "Replaced by /memorizing"}
