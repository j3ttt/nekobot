"""Tests for the StateEmitter module."""

import asyncio
import json

import pytest

from nekobot.gateway.state import BotState, StateEmitter

# ---- BotState enum ----


class TestBotState:
    def test_all_states_exist(self):
        assert set(BotState.__members__) == {"idle", "ping", "speaking", "thinking", "working", "error"}

    def test_priority_order(self):
        assert BotState.idle < BotState.ping < BotState.speaking < BotState.thinking < BotState.working < BotState.error

    def test_str_is_name(self):
        assert str(BotState.idle) == "idle"
        assert str(BotState.thinking) == "thinking"

    def test_max_picks_highest_priority(self):
        states = [BotState.idle, BotState.thinking, BotState.speaking]
        assert max(states) == BotState.thinking

    def test_error_beats_everything(self):
        states = [BotState.working, BotState.error, BotState.thinking]
        assert max(states) == BotState.error


# ---- StateEmitter state logic ----


class TestStateEmitterLogic:
    def test_initial_state_is_idle(self):
        emitter = StateEmitter()
        assert emitter.state == BotState.idle

    @pytest.mark.asyncio
    async def test_emit_updates_session(self):
        emitter = StateEmitter()
        await emitter.emit(BotState.thinking, "telegram:123")
        assert emitter.state == BotState.thinking

    @pytest.mark.asyncio
    async def test_emit_idle_removes_session(self):
        emitter = StateEmitter()
        await emitter.emit(BotState.thinking, "telegram:123")
        await emitter.emit(BotState.idle, "telegram:123")
        assert emitter.state == BotState.idle
        assert "telegram:123" not in emitter._sessions

    @pytest.mark.asyncio
    async def test_global_state_is_max_priority(self):
        emitter = StateEmitter()
        await emitter.emit(BotState.speaking, "session-a")
        await emitter.emit(BotState.thinking, "session-b")
        assert emitter.state == BotState.thinking

    @pytest.mark.asyncio
    async def test_global_drops_to_remaining(self):
        emitter = StateEmitter()
        await emitter.emit(BotState.thinking, "session-a")
        await emitter.emit(BotState.speaking, "session-b")
        # Remove the higher-priority session
        await emitter.emit(BotState.idle, "session-a")
        assert emitter.state == BotState.speaking

    @pytest.mark.asyncio
    async def test_all_idle_returns_idle(self):
        emitter = StateEmitter()
        await emitter.emit(BotState.thinking, "s1")
        await emitter.emit(BotState.working, "s2")
        await emitter.emit(BotState.idle, "s1")
        await emitter.emit(BotState.idle, "s2")
        assert emitter.state == BotState.idle

    @pytest.mark.asyncio
    async def test_error_state_highest(self):
        emitter = StateEmitter()
        await emitter.emit(BotState.working, "s1")
        await emitter.emit(BotState.error, "s2")
        assert emitter.state == BotState.error


# ---- WebSocket integration ----


class TestStateEmitterWebSocket:
    @pytest.mark.asyncio
    async def test_ws_connect_receives_current_state(self):
        import websockets

        emitter = StateEmitter(port=0)  # port=0 won't work with websockets.serve
        # Use a real port
        emitter = StateEmitter(host="127.0.0.1", port=19100)
        server_task = asyncio.create_task(emitter.run())

        try:
            # Give server time to start
            await asyncio.sleep(0.1)

            async with websockets.connect("ws://127.0.0.1:19100") as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
                msg = json.loads(raw)
                assert msg["type"] == "state"
                assert msg["state"] == "idle"
                assert "ts" in msg
        finally:
            await emitter.stop()
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_ws_receives_state_changes(self):
        import websockets

        emitter = StateEmitter(host="127.0.0.1", port=19101)
        server_task = asyncio.create_task(emitter.run())

        try:
            await asyncio.sleep(0.1)

            async with websockets.connect("ws://127.0.0.1:19101") as ws:
                # Consume initial state
                await asyncio.wait_for(ws.recv(), timeout=2)

                # Emit a state change
                await emitter.emit(BotState.thinking, "test:session")

                raw = await asyncio.wait_for(ws.recv(), timeout=2)
                msg = json.loads(raw)
                assert msg["state"] == "thinking"
                assert msg["session"] == "test:session"

                # Emit idle
                await emitter.emit(BotState.idle, "test:session")
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
                msg = json.loads(raw)
                assert msg["state"] == "idle"
        finally:
            await emitter.stop()
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_multiple_clients_receive_broadcast(self):
        import websockets

        emitter = StateEmitter(host="127.0.0.1", port=19102)
        server_task = asyncio.create_task(emitter.run())

        try:
            await asyncio.sleep(0.1)

            async with (
                websockets.connect("ws://127.0.0.1:19102") as ws1,
                websockets.connect("ws://127.0.0.1:19102") as ws2,
            ):
                # Consume initial states
                await asyncio.wait_for(ws1.recv(), timeout=2)
                await asyncio.wait_for(ws2.recv(), timeout=2)

                # Emit
                await emitter.emit(BotState.working, "cron:job1")

                msg1 = json.loads(await asyncio.wait_for(ws1.recv(), timeout=2))
                msg2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=2))
                assert msg1["state"] == "working"
                assert msg2["state"] == "working"
        finally:
            await emitter.stop()
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):
                pass
