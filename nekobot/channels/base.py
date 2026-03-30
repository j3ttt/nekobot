"""Base channel interface for chat platforms."""

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from nekobot.bus.events import InboundMessage, OutboundMessage
from nekobot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) implements this interface
    to integrate with the nekobot message bus.
    """

    name: str = "base"

    def __init__(self, config: Any, bus: MessageBus) -> None:
        self.config = config
        self.bus = bus
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages (long-running)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop and clean up."""

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through this channel."""

    def is_allowed(self, sender_id: str) -> bool:
        """Check if sender is permitted. Empty list = deny all; '*' = allow all.

        sender_id may be "id|username" (Telegram) — match against the id part too.
        """
        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list:
            logger.warning("{}: allow_from is empty - all access denied", self.name)
            return False
        if "*" in allow_list:
            return True
        # Match full sender_id or just the numeric id prefix (before '|')
        sender_str = str(sender_id)
        sender_id_part = sender_str.split("|")[0]
        return sender_str in allow_list or sender_id_part in allow_list

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """Check permissions and forward to bus."""
        if not self.is_allowed(sender_id):
            logger.warning("Access denied for {} on {}", sender_id, self.name)
            return

        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                sender_id=str(sender_id),
                chat_id=str(chat_id),
                content=content,
                media=media or [],
                metadata=metadata or {},
                session_key_override=session_key,
            )
        )

    @property
    def is_running(self) -> bool:
        return self._running
