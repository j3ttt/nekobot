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
