"""
Gateway router: InboundMessage → Claude Agent SDK ClaudeSDKClient → OutboundMessage.

This is the core coordination layer of nekobot. It:
1. Receives messages from IM channels via MessageBus
2. Builds a custom system prompt (personality + memory + tools guide)
3. Sends messages via ClaudeSDKClient (persistent per session)
4. Extracts memory_write tags from responses
5. Sends responses back through MessageBus

SDK notes (claude-agent-sdk 0.1.x):
- ClaudeSDKClient maintains conversation state across messages
- client.query(prompt) sends a new message, client.receive_response() streams back
- System prompt is set once per client lifecycle (at creation)
- Session IDs are still persisted for cross-restart recovery via resume
- Error hierarchy: ClaudeSDKError → CLIConnectionError → CLINotFoundError
                   ClaudeSDKError → ProcessError (has exit_code, stderr)
                   ClaudeSDKError → CLIJSONDecodeError
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

MAX_RETRIES = 2
RETRY_BASE_DELAY = 2.0  # seconds
BATCH_WINDOW = 2.5  # seconds — wait for more messages before processing

# Circuit breaker constants
CB_FAILURE_THRESHOLD = 3
CB_RECOVERY_TIMEOUT = 60.0  # seconds before half-open probe


class CircuitBreaker:
    """Simple circuit breaker: closed → open → half_open → closed."""

    def __init__(
        self,
        failure_threshold: int = CB_FAILURE_THRESHOLD,
        recovery_timeout: float = CB_RECOVERY_TIMEOUT,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = "closed"
        self._failure_count = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        # Auto-transition open → half_open after timeout
        if self._state == "open" and time.monotonic() - self._opened_at >= self._recovery_timeout:
            self._state = "half_open"
        return self._state

    def check(self) -> bool:
        """Return True if request is allowed, False if breaker is open."""
        s = self.state
        if s == "closed":
            return True
        if s == "half_open":
            return True  # allow probe
        return False  # open

    def record_success(self) -> None:
        if self._state == "half_open":
            self._state = "closed"
            self._failure_count = 0
            logger.info("Circuit breaker closed after successful probe")
        elif self._state == "closed":
            self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == "half_open":
            self._state = "open"
            self._opened_at = time.monotonic()
            logger.warning("Circuit breaker re-opened after failed probe")
        elif self._failure_count >= self._failure_threshold:
            self._state = "open"
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit breaker opened after {} consecutive failures",
                self._failure_count,
            )

from nekobot.bus.events import InboundMessage, OutboundMessage
from nekobot.bus.queue import MessageBus
from nekobot.gateway.hooks import PreCompactHook
from nekobot.gateway.prompt import PromptBuilder
from nekobot.memory.extractor import extract_memory_writes
from nekobot.memory.store import MemoryStore
from nekobot.usage.tracker import UsageTracker

if TYPE_CHECKING:
    from nekobot.config.schema import GatewayConfig
    from nekobot.gateway.media import MediaHandler
    from nekobot.gateway.ping import CuriosityPing
    from nekobot.gateway.state import StateEmitter


class Gateway:
    """
    Core gateway: bridges IM channels to Claude Code via Agent SDK.

    Uses ClaudeSDKClient for persistent sessions — each channel:chat_id
    gets its own client that maintains conversation state across messages.

    Architecture:
        MessageBus.inbound → Gateway.run() → ClaudeSDKClient → MessageBus.outbound
    """

    def __init__(
        self,
        config: GatewayConfig,
        message_bus: MessageBus,
        memory_store: MemoryStore,
        prompt_builder: PromptBuilder,
        usage_tracker: UsageTracker,
        mcp_servers: dict[str, Any] | None = None,
        media_handler: MediaHandler | None = None,
        ping: CuriosityPing | None = None,
        state: StateEmitter | None = None,
    ) -> None:
        self.config = config
        self.bus = message_bus
        self.memory = memory_store
        self.prompt = prompt_builder
        self.usage = usage_tracker
        self._mcp_servers = mcp_servers or {}
        self.media = media_handler
        self.ping = ping
        self.state = state

        # session_key → live ClaudeSDKClient instance
        self._clients: dict[str, Any] = {}
        # session_key → session_id (persisted for cross-restart recovery)
        self._sessions: dict[str, str] = {}
        # session_key → last error message (persisted, injected on next resume)
        self._session_errors: dict[str, str] = {}
        self._sessions_path = config.data_dir_resolved / "sessions.json"
        self._load_sessions()

        # Per-session stderr buffer: captures CLI-level errors (e.g. 402 quota)
        # that get swallowed into generic ProcessError/CLIConnectionError
        self._stderr_lines: dict[str, list[str]] = {}

        # Global circuit breaker — quota exhaustion is account-wide
        self._breaker = CircuitBreaker()

        # Async dialogue: batch rapid messages per session
        self._batch_queues: dict[str, list[InboundMessage]] = {}
        self._batch_tasks: dict[str, asyncio.Task[None]] = {}

        # PreCompact hook: intercept compaction → /memorizing
        self._pre_compact_hook = PreCompactHook(
            memory=memory_store,
            bus=message_bus,
            session_lookup=self._session_id_to_location,
        )

    # ------------------------------------------------------------------
    # Session persistence (for cross-restart recovery)
    # ------------------------------------------------------------------

    def _load_sessions(self) -> None:
        """Load sessions from disk.

        Format: {session_key: {id: str, last_error?: str}}
        Backward compat: {session_key: str} (old format, just session_id)
        """
        if not self._sessions_path.exists():
            return
        try:
            raw = json.loads(self._sessions_path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for key, val in raw.items():
            if isinstance(val, str):
                # Old format: bare session_id string
                self._sessions[key] = val
            elif isinstance(val, dict):
                self._sessions[key] = val["id"]
                if val.get("last_error"):
                    self._session_errors[key] = val["last_error"]

    def _save_sessions(self) -> None:
        self._sessions_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        for key, session_id in self._sessions.items():
            entry: dict[str, Any] = {"id": session_id}
            if key in self._session_errors:
                entry["last_error"] = self._session_errors[key]
            data[key] = entry
        self._sessions_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )

    # ------------------------------------------------------------------
    # Session error tracking
    # ------------------------------------------------------------------

    def _record_session_error(self, session_key: str, exc: Exception) -> None:
        """Record an error for a session so the agent knows on next resume."""
        # Use stderr-extracted message if available (more specific), else exception
        stderr_msg = self._extract_stderr_error(session_key)
        if stderr_msg:
            error_msg = stderr_msg
        else:
            error_msg = f"{type(exc).__name__}: {exc}"
        self._session_errors[session_key] = error_msg
        self._save_sessions()
        logger.info("Recorded session error for {}: {}", session_key, error_msg)

    def _pop_session_error_context(self, session_key: str) -> str | None:
        """Pop and return error context to prepend to next message, if any."""
        error = self._session_errors.pop(session_key, None)
        if error:
            self._save_sessions()
        return error

    # ------------------------------------------------------------------
    # stderr capture
    # ------------------------------------------------------------------

    def _make_stderr_callback(self, session_key: str):
        """Create a stderr callback that captures CLI output for a session."""
        buf = self._stderr_lines.setdefault(session_key, [])

        def _on_stderr(line: str) -> None:
            buf.append(line)
            # Log at debug; these are CLI internals (token counts, API retries, etc.)
            logger.debug("CLI stderr [{}]: {}", session_key, line.rstrip())

        return _on_stderr

    def _extract_stderr_error(self, session_key: str) -> str | None:
        """Extract the most relevant error from captured stderr lines.

        The Claude CLI logs real API errors to stderr but the SDK surfaces
        them as generic ProcessError/CLIConnectionError. This recovers the
        original error message.
        """
        lines = self._stderr_lines.pop(session_key, [])
        if not lines:
            return None
        # Scan in reverse (most recent first) for any error-like line
        for line in reversed(lines):
            lower = line.lower()
            if any(kw in lower for kw in ("error", "fail", "refused", "timeout", "exceeded", "denied")):
                return line.strip()
        return None

    # ------------------------------------------------------------------
    # Session reverse lookup (session_id → session_key / channel+chat_id)
    # ------------------------------------------------------------------

    def _session_id_to_key(self, session_id: str) -> str | None:
        """Reverse lookup: session_id → session_key."""
        for key, sid in self._sessions.items():
            if sid == session_id:
                return key
        return None

    def _session_id_to_location(self, session_id: str) -> tuple[str, str] | None:
        """Reverse lookup: session_id → (channel, chat_id)."""
        key = self._session_id_to_key(session_id)
        if not key:
            return None
        # session_key format: "channel:chat_id"
        parts = key.split(":", 1)
        if len(parts) != 2:
            return None
        return parts[0], parts[1]

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    async def _get_or_create_client(
        self, session_key: str, system_prompt: str
    ) -> Any:
        """Get existing client or create+connect a new one for this session."""
        if session_key in self._clients:
            return self._clients[session_key]

        from claude_agent_sdk import CLINotFoundError, ClaudeSDKClient

        session_id = self._sessions.get(session_key)
        options = self._build_options(system_prompt, session_id, session_key)
        client = ClaudeSDKClient(options=options)
        try:
            await client.connect()
        except CLINotFoundError:
            # Fatal: CLI not installed, no point retrying
            raise
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
            raise

        self._clients[session_key] = client
        logger.info(
            "Client created for {} (resume={})",
            session_key,
            session_id[:8] if session_id else "none",
        )
        return client

    async def _discard_client(self, session_key: str) -> None:
        """Disconnect and remove a client. Does NOT clear stderr — call
        _record_session_error first if you need the stderr buffer."""
        client = self._clients.pop(session_key, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                logger.debug("Error disconnecting client for {}", session_key)

    async def shutdown(self) -> None:
        """Disconnect all clients. Call on process shutdown."""
        for key in list(self._clients):
            await self._discard_client(key)
        logger.info("All SDK clients disconnected")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop: consume inbound messages, batch per session, then process."""
        logger.info("Gateway started, waiting for messages...")
        while True:
            msg = await self.bus.consume_inbound()
            sk = msg.session_key
            self._batch_queues.setdefault(sk, []).append(msg)
            if sk not in self._batch_tasks:
                self._batch_tasks[sk] = asyncio.create_task(self._process_batch(sk))

    async def _process_batch(self, session_key: str) -> None:
        """Wait for the batch window, then merge and process queued messages."""
        await asyncio.sleep(BATCH_WINDOW)
        self._batch_tasks.pop(session_key, None)
        batch = self._batch_queues.pop(session_key, [])
        if not batch:
            return
        msg = self._merge_batch(batch)
        try:
            response = await self._handle_with_retry(msg)
            if response:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=response,
                        reply_to=msg.metadata.get("message_id"),
                        metadata=msg.metadata,
                    )
                )
        except Exception as e:
            logger.exception("Error handling message from {}:{}", msg.channel, msg.chat_id)
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=self._user_error_message(e, msg.session_key),
                    metadata=msg.metadata,
                )
            )

    @staticmethod
    def _merge_batch(batch: list[InboundMessage]) -> InboundMessage:
        """Merge a batch of messages into a single InboundMessage."""
        if len(batch) == 1:
            return batch[0]
        parts: list[str] = []
        all_media: list[str] = []
        for m in batch:
            ts = m.timestamp.strftime("%H:%M")
            parts.append(f"[{ts}] {m.content}")
            all_media.extend(m.media)
        merged_meta = dict(batch[-1].metadata)
        merged_meta["_batched"] = True
        return InboundMessage(
            channel=batch[-1].channel,
            sender_id=batch[-1].sender_id,
            chat_id=batch[-1].chat_id,
            content="\n".join(parts),
            timestamp=batch[-1].timestamp,
            media=all_media,
            metadata=merged_meta,
        )

    def _user_error_message(self, exc: Exception, session_key: str) -> str:
        """Produce a user-facing error message from an SDK exception."""
        # Best source: _record_session_error already extracted stderr
        recorded = self._session_errors.get(session_key)
        if recorded:
            return recorded

        # Fallback: check stderr buffer directly (in case _record wasn't called)
        stderr_error = self._extract_stderr_error(session_key)
        if stderr_error:
            return stderr_error

        # Last resort: use the exception itself
        return f"{type(exc).__name__}: {exc}"

    async def _handle_with_retry(self, msg: InboundMessage) -> str | None:
        """Wrap _handle with retry logic for transient failures.

        Non-retryable errors (CLINotFoundError) are raised immediately.
        Circuit breaker prevents hammering the API during sustained failures.
        """
        from claude_agent_sdk import CLINotFoundError

        if not self._breaker.check():
            logger.warning("Circuit breaker open, rejecting message from {}:{}", msg.channel, msg.chat_id)
            return "\u26a1 Service temporarily unavailable, please retry in a minute."

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = await self._handle(msg)
                self._breaker.record_success()
                return result
            except CLINotFoundError:
                raise  # Fatal, no point retrying
            except Exception as e:
                last_error = e
                self._breaker.record_failure()
                if attempt < MAX_RETRIES:
                    if not self._breaker.check():
                        logger.warning("Circuit breaker tripped mid-retry for {}:{}", msg.channel, msg.chat_id)
                        return "\u26a1 Service temporarily unavailable, please retry in a minute."
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "Attempt {}/{} failed for {}:{}, retrying in {:.0f}s: {}",
                        attempt + 1, MAX_RETRIES + 1, msg.channel, msg.chat_id, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "All {} attempts failed for {}:{}",
                        MAX_RETRIES + 1, msg.channel, msg.chat_id,
                    )
        raise last_error  # type: ignore[misc]

    async def _emit(self, state_name: str, session: str | None = None) -> None:
        """Emit a state change if StateEmitter is available."""
        if self.state:
            from nekobot.gateway.state import BotState
            await self.state.emit(BotState[state_name], session)

    async def _handle(self, msg: InboundMessage) -> str | None:
        """Process a single inbound message through Claude Agent SDK."""
        # Reset ping timer on every real user message
        if self.ping and msg.sender_id != "system":
            self.ping.reset_timer(msg.session_key, msg.channel, msg.chat_id, msg.content)

        # Late import check: claude-agent-sdk may not be installed during tests
        try:
            from claude_agent_sdk import (  # noqa: F401
                CLIConnectionError,
                CLINotFoundError,
                ClaudeSDKClient,
                ProcessError,
            )
        except ImportError:
            logger.error("claude-agent-sdk not installed. pip install claude-agent-sdk")
            return "Internal error: Agent SDK not available."

        # Pre-process media (voice transcription)
        content = msg.content
        if self.media:
            content = await self.media.process_content(content)

        # Inject message timestamp (skip for batched/internal system messages)
        if not msg.metadata.get("_batched") and msg.sender_id != "system":
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
            content = f"[{ts}] {content}"

        # Build system prompt (used when creating a new client for this session)
        system_prompt = self.prompt.build(msg.channel, msg.chat_id)

        # Query Claude with typed error handling
        try:
            await self._emit("thinking", msg.session_key)
            client = await self._get_or_create_client(msg.session_key, system_prompt)
            result = await self._query_claude(msg, content, client)
            await self._emit("idle", msg.session_key)
            return result
        except CLINotFoundError:
            await self._emit("error", msg.session_key)
            raise  # Fatal
        except (CLIConnectionError, ProcessError) as e:
            # Connection/process errors → discard client (connection is broken),
            # but keep session_id so conversation history survives for next resume.
            logger.warning(
                "SDK error for {}: {} ({})",
                msg.session_key, type(e).__name__, e,
            )
            await self._emit("error", msg.session_key)
            await self._discard_client(msg.session_key)
            self._record_session_error(msg.session_key, e)
            raise
        except Exception as e:
            # Unknown errors — discard broken client but preserve session
            await self._emit("error", msg.session_key)
            error_str = str(e).lower()
            if msg.session_key in self._clients:
                logger.warning("Unexpected error for {}, discarding client: {}", msg.session_key, e)
                await self._discard_client(msg.session_key)
            # Only clear session if the error indicates the session itself is invalid
            if any(kw in error_str for kw in ("session not found", "invalid session", "no such session")):
                logger.warning("Session for {} appears invalid, clearing", msg.session_key)
                self._sessions.pop(msg.session_key, None)
                self._session_errors.pop(msg.session_key, None)
                self._save_sessions()
            else:
                self._record_session_error(msg.session_key, e)
            raise

    async def _query_claude(
        self, msg: InboundMessage, content: str, client: Any
    ) -> str | None:
        """Send a message to a connected client and process the response.

        Streams intermediate AssistantMessage text to the user immediately
        (via MessageBus) so they don't experience silent waiting during
        long tool-call chains.  Only the *final* AssistantMessage text is
        returned to the caller for memory extraction and the main outbound
        publish.
        """
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ThinkingBlock

        # If the previous exchange failed, prepend context so the agent knows
        prev_error = self._pop_session_error_context(msg.session_key)
        if prev_error:
            content = (
                f"[System: the previous exchange was interrupted — {prev_error}. "
                f"The user's message at that time may not have been processed.]\n\n"
                f"{content}"
            )
            logger.info("Injected error context for {}", msg.session_key)

        logger.info("Calling Claude for {}:{}", msg.channel, msg.chat_id)

        await client.query(content)

        # Real-time streaming: push every AssistantMessage immediately as it arrives.
        # Keep a copy of the last raw text for memory extraction after ResultMessage.
        first_text_emitted = False

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                text_parts: list[str] = []
                thinking_parts: list[str] = []

                for block in message.content:
                    if isinstance(block, TextBlock):
                        if not first_text_emitted:
                            first_text_emitted = True
                            await self._emit("speaking", msg.session_key)
                        text_parts.append(block.text)
                    elif self.config.forward_thinking and isinstance(block, ThinkingBlock):
                        thinking_parts.append(block.thinking)

                raw_text = "".join(text_parts)

                # Extract memory writes before sending so tags don't leak to user
                cleaned, facts = extract_memory_writes(raw_text)
                if facts:
                    self.memory.write_facts(facts)
                    logger.info("Extracted {} memory facts", len(facts))

                # Build display content: prepend thinking if present
                display = cleaned.strip()
                if thinking_parts and self.config.forward_thinking:
                    thinking_text = "\n".join(thinking_parts)
                    display = f"[thinking]\n{thinking_text}\n[/thinking]\n\n{display}"

                if display:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=display,
                            metadata=msg.metadata,
                        )
                    )

            elif isinstance(message, ResultMessage):
                # Persist session_id for cross-restart recovery
                if message.session_id:
                    self._sessions[msg.session_key] = message.session_id
                    self._save_sessions()

                # Record usage
                self.usage.record(
                    session_id=message.session_id or "",
                    channel=msg.channel,
                    cost_usd=message.total_cost_usd,
                    usage=message.usage,
                    num_turns=message.num_turns,
                    duration_ms=message.duration_ms,
                )

        # All messages already pushed via bus; memory extracted per-message above
        return None

    def _build_options(self, system_prompt: str, session_id: str | None, session_key: str = "") -> Any:
        """Build ClaudeAgentOptions for a new ClaudeSDKClient."""
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        opts: dict[str, Any] = {
            "tools": {"type": "preset", "preset": "claude_code"},
            "system_prompt": system_prompt,
            "permission_mode": self.config.permission_mode,
            "cwd": str(self.config.workspace_resolved),
            "setting_sources": ["user", "project"],
            "stderr": self._make_stderr_callback(session_key),
            "hooks": {
                "PreCompact": [HookMatcher(hooks=[self._pre_compact_hook])],
            },
        }

        if self.config.cli_path:
            opts["cli_path"] = str(Path(self.config.cli_path).expanduser())

        if session_id:
            opts["resume"] = session_id

        if self.config.model:
            opts["model"] = self.config.model

        if self.config.max_turns:
            opts["max_turns"] = self.config.max_turns

        if self.config.max_budget_usd is not None:
            opts["max_budget_usd"] = self.config.max_budget_usd

        # Wire MCP servers if available
        if self._mcp_servers:
            opts["mcp_servers"] = self._mcp_servers

        return ClaudeAgentOptions(**opts)
