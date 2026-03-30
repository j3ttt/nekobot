"""Channel manager — initializes, starts, stops channels and dispatches outbound."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from nekobot.bus.events import OutboundMessage
from nekobot.bus.queue import MessageBus
from nekobot.channels.base import BaseChannel
from nekobot.config.schema import Config


class ChannelManager:
    """
    Manages chat channels and routes outbound messages.

    Channels are initialized lazily based on config. Adding a new channel:
    1. Add its config class to config/schema.py ChannelsConfig
    2. Add the lazy import block in _init_channels() below
    3. Implement BaseChannel in channels/<name>.py
    """

    def __init__(self, config: Config, bus: MessageBus) -> None:
        self.config = config
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._init_channels()

    def _init_channels(self) -> None:
        ch = self.config.channels

        if ch.telegram.enabled:
            try:
                from nekobot.channels.telegram import TelegramChannel

                self.channels["telegram"] = TelegramChannel(ch.telegram, self.bus)
                logger.info("Telegram channel enabled")
            except ImportError as e:
                logger.warning("Telegram not available: {}", e)

        if ch.discord.enabled:
            try:
                from nekobot.channels.discord import DiscordChannel

                self.channels["discord"] = DiscordChannel(ch.discord, self.bus)
                logger.info("Discord channel enabled")
            except ImportError as e:
                logger.warning("Discord not available: {}", e)

        if ch.dingtalk.enabled:
            try:
                from nekobot.channels.dingtalk import DingTalkChannel

                self.channels["dingtalk"] = DingTalkChannel(ch.dingtalk, self.bus)
                logger.info("DingTalk channel enabled")
            except ImportError as e:
                logger.warning("DingTalk not available: {}", e)

        # Validate: empty allow_from is a likely misconfiguration
        for name, channel in self.channels.items():
            if getattr(channel.config, "allow_from", None) == []:
                raise SystemExit(
                    f'"{name}" has empty allowFrom (denies all). '
                    f'Set ["*"] to allow everyone, or add specific user IDs.'
                )

    async def start_all(self) -> None:
        if not self.channels:
            logger.warning("No channels enabled")
            return

        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        tasks = [
            asyncio.create_task(self._start_channel(name, ch))
            for name, ch in self.channels.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception:
                logger.exception("Error stopping {}", name)

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        try:
            await channel.start()
        except Exception:
            logger.exception("Failed to start {}", name)

    async def _dispatch_outbound(self) -> None:
        """Route outbound messages to the correct channel."""
        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)
                channel = self.channels.get(msg.channel)
                if channel:
                    try:
                        await channel.send(msg)
                    except Exception:
                        logger.exception("Error sending to {}", msg.channel)
                else:
                    logger.warning("Unknown channel: {}", msg.channel)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    @property
    def enabled_channels(self) -> list[str]:
        return list(self.channels.keys())
