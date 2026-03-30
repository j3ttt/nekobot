"""
Curiosity Ping: proactive messaging system.

After the user goes quiet for a random interval in [min_hours, max_hours],
Bot sends a proactive check-in message through the last active channel.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from loguru import logger

from nekobot.bus.events import InboundMessage

if TYPE_CHECKING:
    from nekobot.bus.queue import MessageBus
    from nekobot.config.schema import PingConfig
    from nekobot.gateway.state import StateEmitter


# Keywords that suggest the user is going to sleep
_SLEEP_KEYWORDS = {"晚安", "睡了", "good night", "gn", "going to bed", "睡觉"}


class CuriosityPing:
    """
    Per-session idle timer that triggers proactive messages.

    Lifecycle:
        1. User sends a message → reset_timer(session_key, channel, chat_id, last_content)
        2. Timer fires after random delay → _fire(session_key)
        3. Publishes a synthetic InboundMessage to the bus with a system prompt
           asking Claude to generate a proactive message
        4. Gateway processes it like any other message, sends response to user

    Single-shot behavior:
        - Fires once per idle period
        - Does not repeat until user sends another message
        - Cancelled and reset on every new user message
    """

    def __init__(self, config: PingConfig, bus: MessageBus, state: StateEmitter | None = None) -> None:
        """
        Initialize the curiosity ping system.

        Args:
            config: Ping configuration (enabled, min_hours, max_hours)
            bus: MessageBus for publishing synthetic messages
            state: Optional StateEmitter for broadcasting state changes
        """
        self.config = config
        self.bus = bus
        self._state = state
        self._timers: dict[str, asyncio.Task] = {}  # session_key → timer task
        self._last_channel: dict[str, tuple[str, str]] = {}  # session_key → (channel, chat_id)

    def reset_timer(
        self, session_key: str, channel: str, chat_id: str, last_content: str
    ) -> None:
        """
        Reset the idle timer for a session.

        Call this every time a user sends a message. Cancels any existing timer
        and schedules a new one (unless sleep keywords are detected).

        Args:
            session_key: Unique session identifier (typically channel:chat_id)
            channel: Channel name (telegram, discord, etc.)
            chat_id: Chat ID within the channel
            last_content: Content of the user's last message (for sleep detection)
        """
        if not self.config.enabled:
            return

        # Cancel existing timer
        old = self._timers.pop(session_key, None)
        if old and not old.done():
            old.cancel()
            logger.debug("Cancelled existing ping timer for {}", session_key)

        # Check sleep intent
        content_lower = last_content.lower().strip()
        if any(kw in content_lower for kw in _SLEEP_KEYWORDS):
            logger.debug("Sleep keyword detected for {}, skipping ping", session_key)
            return

        # Store last active channel
        self._last_channel[session_key] = (channel, chat_id)

        # Schedule new timer
        delay_hours = random.uniform(self.config.min_hours, self.config.max_hours)
        delay_seconds = delay_hours * 3600
        self._timers[session_key] = asyncio.create_task(
            self._wait_and_fire(session_key, delay_seconds),
            name=f"ping-{session_key}",
        )
        logger.debug("Ping timer set for {} in {:.1f}h", session_key, delay_hours)

    async def _wait_and_fire(self, session_key: str, delay: float) -> None:
        """
        Wait for the specified delay then fire the ping.

        Args:
            session_key: Session to fire ping for
            delay: Delay in seconds before firing
        """
        try:
            await asyncio.sleep(delay)
            await self._fire(session_key)
        except asyncio.CancelledError:
            logger.debug("Ping timer cancelled for {}", session_key)

    async def _fire(self, session_key: str) -> None:
        """
        Generate and send a proactive message.

        Publishes a synthetic InboundMessage with a system instruction asking
        Claude to generate a brief, in-character check-in message.

        Args:
            session_key: Session to send ping to
        """
        channel_info = self._last_channel.get(session_key)
        if not channel_info:
            logger.warning("No channel info for session {}, skipping ping", session_key)
            return

        channel, chat_id = channel_info
        logger.info("Curiosity ping firing for {}", session_key)

        if self._state:
            from nekobot.gateway.state import BotState
            await self._state.emit(BotState.ping, session_key)

        # Publish a synthetic inbound message that instructs Claude to
        # generate a proactive check-in message
        await self.bus.publish_inbound(
            InboundMessage(
                channel=channel,
                sender_id="system",
                chat_id=chat_id,
                content=(
                    "[SYSTEM] The user has been idle for a while. "
                    "Generate a brief, in-character message to check in on them. "
                    "Be natural — reference something from recent context if possible. "
                    "Keep it short (1-2 sentences). Do not mention that this is a system prompt."
                ),
                metadata={"is_ping": True},
            )
        )

        # Clean up — single shot
        self._timers.pop(session_key, None)
        logger.debug("Ping sent for {}, timer removed", session_key)

    def cancel_all(self) -> None:
        """
        Cancel all pending timers.

        Call this during shutdown to clean up all pending ping tasks.
        """
        logger.info("Cancelling {} pending ping timers", len(self._timers))
        for task in self._timers.values():
            if not task.done():
                task.cancel()
        self._timers.clear()
