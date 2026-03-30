"""Tests for Gateway router: circuit breaker + concurrency."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nekobot.bus.events import InboundMessage, OutboundMessage
from nekobot.bus.queue import MessageBus
from nekobot.gateway.router import BATCH_WINDOW, CB_FAILURE_THRESHOLD, CircuitBreaker, Gateway
from nekobot.memory.store import MemoryStore
from nekobot.gateway.prompt import PromptBuilder
from nekobot.usage.tracker import UsageTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(
    content: str = "hello",
    channel: str = "test",
    chat_id: str = "c1",
    sender_id: str = "u1",
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id=chat_id,
        content=content,
        timestamp=datetime(2026, 1, 1, 12, 0),
    )


def _make_gateway(tmp_path, bus: MessageBus | None = None) -> Gateway:
    """Build a Gateway with minimal stubs (no real SDK)."""
    config = SimpleNamespace(
        workspace=".",
        data_dir=str(tmp_path / "data"),
        data_dir_resolved=tmp_path / "data",
        workspace_resolved=tmp_path / "workspace",
        permission_mode="bypassPermissions",
        model=None,
        forward_thinking=False,
        max_turns=None,
        max_budget_usd=None,
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)

    bus = bus or MessageBus()
    memory = MemoryStore(tmp_path / "memory")
    prompt = MagicMock(spec=PromptBuilder)
    prompt.build.return_value = "system prompt"
    usage = MagicMock(spec=UsageTracker)

    return Gateway(
        config=config,
        message_bus=bus,
        memory_store=memory,
        prompt_builder=prompt,
        usage_tracker=usage,
    )


# ---------------------------------------------------------------------------
# CircuitBreaker unit tests
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert cb.check() is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"
        assert cb.check() is False

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        assert cb.check() is True

    def test_success_resets_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.02)
        assert cb.state == "half_open"
        assert cb.check() is True

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        assert cb.state == "half_open"
        cb.record_success()
        assert cb.state == "closed"

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        _ = cb.state  # trigger half_open transition
        cb.record_failure()
        assert cb.state == "open"


# ---------------------------------------------------------------------------
# Gateway integration tests (mocked SDK)
# ---------------------------------------------------------------------------

# Fake SDK classes to patch into the module
_fake_sdk = SimpleNamespace(
    ClaudeSDKClient=MagicMock,
    ClaudeAgentOptions=lambda **kw: SimpleNamespace(**kw),
    CLINotFoundError=type("CLINotFoundError", (Exception,), {}),
    CLIConnectionError=type("CLIConnectionError", (Exception,), {}),
    ProcessError=type("ProcessError", (Exception,), {}),
    AssistantMessage=type("AssistantMessage", (), {}),
    ResultMessage=type("ResultMessage", (), {}),
    TextBlock=type("TextBlock", (), {}),
    ThinkingBlock=type("ThinkingBlock", (), {}),
)


def _patch_sdk():
    """Patch claude_agent_sdk imports in the router module."""
    return patch.dict("sys.modules", {"claude_agent_sdk": _fake_sdk})


class TestGatewayCircuitBreaker:
    @pytest.mark.asyncio
    async def test_breaker_trips_after_failures(self, tmp_path):
        gw = _make_gateway(tmp_path)
        msg = _make_msg()

        with _patch_sdk():
            # Force _handle to fail
            gw._handle = AsyncMock(side_effect=RuntimeError("API down"))

            # First CB_FAILURE_THRESHOLD * (MAX_RETRIES+1) calls should exhaust retries
            # But breaker trips after CB_FAILURE_THRESHOLD failures total
            for _ in range(CB_FAILURE_THRESHOLD):
                gw._breaker.record_failure()

            # Now breaker is open — should return friendly message
            result = await gw._handle_with_retry(msg)
            assert result is not None
            assert "temporarily unavailable" in result

    @pytest.mark.asyncio
    async def test_breaker_allows_after_recovery(self, tmp_path):
        gw = _make_gateway(tmp_path)
        gw._breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        msg = _make_msg()

        with _patch_sdk():
            gw._handle = AsyncMock(side_effect=RuntimeError("fail"))
            try:
                await gw._handle_with_retry(msg)
            except RuntimeError:
                pass

            # Breaker should be open now
            assert gw._breaker.state == "open"

            # Wait for recovery timeout
            await asyncio.sleep(0.02)
            assert gw._breaker.state == "half_open"

            # Successful call should close breaker
            gw._handle = AsyncMock(return_value="ok")
            result = await gw._handle_with_retry(msg)
            assert result == "ok"
            assert gw._breaker.state == "closed"

    @pytest.mark.asyncio
    async def test_breaker_rejects_during_open(self, tmp_path):
        gw = _make_gateway(tmp_path)
        msg = _make_msg()

        with _patch_sdk():
            # Manually open the breaker
            gw._breaker._state = "open"
            gw._breaker._opened_at = time.monotonic()

            result = await gw._handle_with_retry(msg)
            assert result is not None
            assert "\u26a1" in result


class TestGatewayConcurrency:
    @pytest.mark.asyncio
    async def test_same_session_concurrent_access(self, tmp_path):
        """Two messages for the same session_key should not corrupt state."""
        gw = _make_gateway(tmp_path)
        call_order: list[str] = []

        async def fake_handle(msg: InboundMessage) -> str | None:
            call_order.append(f"start-{msg.content}")
            await asyncio.sleep(0.01)
            call_order.append(f"end-{msg.content}")
            return f"reply-{msg.content}"

        with _patch_sdk():
            gw._handle = AsyncMock(side_effect=fake_handle)

            msg1 = _make_msg(content="msg1")
            msg2 = _make_msg(content="msg2")

            r1, r2 = await asyncio.gather(
                gw._handle_with_retry(msg1),
                gw._handle_with_retry(msg2),
            )
            assert r1 == "reply-msg1"
            assert r2 == "reply-msg2"
            # Both should have started and ended
            assert len(call_order) == 4

    @pytest.mark.asyncio
    async def test_rapid_message_burst_ordered(self, tmp_path):
        """Messages queued rapidly should all be processed."""
        bus = MessageBus()
        gw = _make_gateway(tmp_path, bus=bus)
        results: list[str] = []

        async def fake_handle(msg: InboundMessage) -> str | None:
            results.append(msg.content)
            return f"reply-{msg.content}"

        with _patch_sdk():
            gw._handle = AsyncMock(side_effect=fake_handle)

            # Queue 5 messages rapidly
            for i in range(5):
                await bus.publish_inbound(_make_msg(content=f"burst-{i}"))

            # Process them via the gateway loop (with timeout)
            async def run_loop():
                while True:
                    msg = await bus.consume_inbound()
                    response = await gw._handle_with_retry(msg)
                    if response:
                        await bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=response,
                            )
                        )

            loop_task = asyncio.create_task(run_loop())
            # Wait for all messages to be processed
            await asyncio.sleep(0.1)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

            assert len(results) == 5
            assert results == [f"burst-{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_concurrent_different_sessions(self, tmp_path):
        """Messages for different sessions should process independently."""
        gw = _make_gateway(tmp_path)
        processed: list[str] = []

        async def fake_handle(msg: InboundMessage) -> str | None:
            processed.append(msg.session_key)
            await asyncio.sleep(0.01)
            return "ok"

        with _patch_sdk():
            gw._handle = AsyncMock(side_effect=fake_handle)

            msgs = [_make_msg(chat_id=f"chat-{i}") for i in range(3)]
            await asyncio.gather(*(gw._handle_with_retry(m) for m in msgs))

            assert len(processed) == 3
            assert set(processed) == {f"test:chat-{i}" for i in range(3)}


# ---------------------------------------------------------------------------
# Message batching tests
# ---------------------------------------------------------------------------

class TestMessageBatching:
    @pytest.mark.asyncio
    async def test_batch_merges_rapid_messages(self, tmp_path):
        """3 messages within batch window → _handle called once with merged content."""
        bus = MessageBus()
        gw = _make_gateway(tmp_path, bus=bus)
        handled: list[str] = []

        async def fake_handle(msg: InboundMessage) -> str | None:
            handled.append(msg.content)
            return None

        with _patch_sdk():
            gw._handle = AsyncMock(side_effect=fake_handle)

            msgs = [
                _make_msg(content=f"part-{i}", chat_id="same")
                for i in range(3)
            ]
            for m in msgs:
                await bus.publish_inbound(m)

            # Run gateway loop briefly — it will create a batch task
            loop_task = asyncio.create_task(gw.run())
            await asyncio.sleep(BATCH_WINDOW + 0.3)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

            # Should have been called once with merged content
            assert len(handled) == 1
            assert "[12:00] part-0" in handled[0]
            assert "[12:00] part-1" in handled[0]
            assert "[12:00] part-2" in handled[0]

    @pytest.mark.asyncio
    async def test_single_message_passes_after_window(self, tmp_path):
        """A single message is still processed after BATCH_WINDOW delay."""
        bus = MessageBus()
        gw = _make_gateway(tmp_path, bus=bus)
        handled: list[str] = []

        async def fake_handle(msg: InboundMessage) -> str | None:
            handled.append(msg.content)
            return None

        with _patch_sdk():
            gw._handle = AsyncMock(side_effect=fake_handle)

            await bus.publish_inbound(_make_msg(content="solo", chat_id="c1"))

            loop_task = asyncio.create_task(gw.run())
            await asyncio.sleep(BATCH_WINDOW + 0.3)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

            assert len(handled) == 1
            # Single message should NOT be merged (no _batched flag)
            assert handled[0] == "solo"

    @pytest.mark.asyncio
    async def test_different_sessions_batch_independently(self, tmp_path):
        """Messages for different sessions batch separately."""
        bus = MessageBus()
        gw = _make_gateway(tmp_path, bus=bus)
        handled: list[str] = []

        async def fake_handle(msg: InboundMessage) -> str | None:
            handled.append(msg.content)
            return None

        with _patch_sdk():
            gw._handle = AsyncMock(side_effect=fake_handle)

            # Two messages for session A, one for session B
            await bus.publish_inbound(_make_msg(content="a1", chat_id="chatA"))
            await bus.publish_inbound(_make_msg(content="b1", chat_id="chatB"))
            await bus.publish_inbound(_make_msg(content="a2", chat_id="chatA"))

            loop_task = asyncio.create_task(gw.run())
            await asyncio.sleep(BATCH_WINDOW + 0.3)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

            # Two calls: one merged for chatA, one single for chatB
            assert len(handled) == 2
            # Find the merged one (contains both a1 and a2)
            merged = [h for h in handled if "a1" in h and "a2" in h]
            assert len(merged) == 1
            single = [h for h in handled if h == "b1"]
            assert len(single) == 1

    def test_merge_batch_single(self, tmp_path):
        """_merge_batch with single message returns it unchanged."""
        msg = _make_msg(content="only")
        result = Gateway._merge_batch([msg])
        assert result is msg

    def test_merge_batch_multiple(self, tmp_path):
        """_merge_batch joins content with timestamps and sets _batched flag."""
        msgs = [
            _make_msg(content="first"),
            _make_msg(content="second"),
        ]
        result = Gateway._merge_batch(msgs)
        assert "[12:00] first" in result.content
        assert "[12:00] second" in result.content
        assert result.metadata.get("_batched") is True
        assert result.channel == "test"

    @pytest.mark.asyncio
    async def test_batched_skips_timestamp_injection(self, tmp_path):
        """_handle skips timestamp injection for batched messages."""
        gw = _make_gateway(tmp_path)
        batched_msg = _make_msg(content="[12:00] already timestamped")
        batched_msg.metadata["_batched"] = True

        with _patch_sdk():
            # Capture what gets passed to _query_claude
            query_content: list[str] = []

            async def fake_query(msg, content, client):
                query_content.append(content)
                return None

            gw._query_claude = AsyncMock(side_effect=fake_query)
            gw._get_or_create_client = AsyncMock(return_value=MagicMock())

            await gw._handle(batched_msg)

            assert len(query_content) == 1
            # Should NOT have double timestamp like [2026-01-01 12:00] [12:00] ...
            assert not query_content[0].startswith("[2026")
