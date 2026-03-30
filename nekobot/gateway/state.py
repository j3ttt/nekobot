"""StateEmitter: broadcast bot state changes over WebSocket."""

from __future__ import annotations

import asyncio
import json
import time
from enum import IntEnum
from typing import Any

from loguru import logger

class BotState(IntEnum):
    """Bot display states, ordered by priority (highest wins for global state)."""

    idle = 0
    ping = 1
    speaking = 2
    thinking = 3
    working = 4
    error = 5

    def __str__(self) -> str:
        return self.name


class StateEmitter:
    """Track per-session bot state and broadcast changes via WebSocket.

    Global state = highest priority across all active sessions.
    New WebSocket clients receive the current state immediately on connect.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9100) -> None:
        self._sessions: dict[str, BotState] = {}
        self._clients: set[Any] = set()
        self._host = host
        self._port = port
        self._server: Any = None

    @property
    def state(self) -> BotState:
        """Global state: highest priority across all sessions."""
        if not self._sessions:
            return BotState.idle
        return max(self._sessions.values())

    async def emit(self, state: BotState, session: str | None = None) -> None:
        """Update state for a session and broadcast to all WS clients."""
        if session:
            if state == BotState.idle:
                self._sessions.pop(session, None)
            else:
                self._sessions[session] = state
        # If no session specified, just broadcast (e.g. direct idle reset)

        msg = json.dumps({
            "type": "state",
            "state": str(state),
            "session": session,
            "ts": int(time.time()),
        })

        if self._clients:
            await asyncio.gather(
                *(self._safe_send(ws, msg) for ws in set(self._clients)),
                return_exceptions=True,
            )

    async def _safe_send(self, ws: Any, msg: str) -> None:
        try:
            await ws.send(msg)
        except Exception:
            self._clients.discard(ws)

    async def _handler(self, ws: Any) -> None:
        """Handle a new WebSocket connection."""
        self._clients.add(ws)
        logger.debug("State WS client connected (total: {})", len(self._clients))

        # Push current state immediately
        current = json.dumps({
            "type": "state",
            "state": str(self.state),
            "session": None,
            "ts": int(time.time()),
        })
        try:
            await ws.send(current)
            async for _ in ws:
                pass  # keep alive, ignore incoming
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            logger.debug("State WS client disconnected (total: {})", len(self._clients))

    async def run(self) -> None:
        """Start the WebSocket server."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets not installed — StateEmitter disabled")
            return

        self._server = await websockets.serve(
            self._handler,
            self._host,
            self._port,
        )
        logger.info("StateEmitter WebSocket listening on ws://{}:{}/", self._host, self._port)
        await self._server.wait_closed()

    async def stop(self) -> None:
        """Shut down the WebSocket server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
