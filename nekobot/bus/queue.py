"""Async message queue for decoupled channel-gateway communication."""

import asyncio

from nekobot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus decoupling IM channels from the gateway.

    Channels push to inbound queue, gateway processes and pushes to outbound.
    """

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()
