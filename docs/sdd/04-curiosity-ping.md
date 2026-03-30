# SDD-04: Curiosity Ping (Proactive Messaging)

## Priority: MEDIUM
## Depends On: SDD-03 (working Gateway)
## Estimated Scope: 1 new file, 2 files modified, ~100 lines

---

## 1. Goal

Implement proactive messaging: after the user goes quiet for 2-8 hours, the bot sends a random "checking in" message through the last active channel.

## 2. Behavior Spec

- **Trigger**: User's last message was N hours ago, where N is random in [min_hours, max_hours] (default: 2-8h)
- **Single-shot**: Fires once per idle period. Does not repeat until user sends another message.
- **Channel**: Uses the same channel and chat_id as the user's last message.
- **Content generation**: Sends a system-level prompt to Claude asking it to generate a proactive message in character.
- **Sleep guard**: If user's last message suggests they're going to sleep (detected via keyword heuristic), skip the ping.
- **Per-session**: Each channel:chat_id has its own timer. Multiple active conversations have independent pings.

## 3. Design

### 3.1 Class: `CuriosityPing`

```python
# nekobot/gateway/ping.py

import asyncio
import random
from datetime import datetime, timedelta

from loguru import logger

from nekobot.bus.events import InboundMessage
from nekobot.bus.queue import MessageBus
from nekobot.config.schema import PingConfig


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
    """

    def __init__(self, config: PingConfig, bus: MessageBus) -> None:
        self.config = config
        self.bus = bus
        self._timers: dict[str, asyncio.Task] = {}  # session_key → timer task
        self._last_channel: dict[str, tuple[str, str]] = {}  # session_key → (channel, chat_id)

    def reset_timer(self, session_key: str, channel: str, chat_id: str, last_content: str) -> None:
        """Call this every time a user sends a message. Resets the idle timer."""
        if not self.config.enabled:
            return

        # Cancel existing timer
        old = self._timers.pop(session_key, None)
        if old and not old.done():
            old.cancel()

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
            self._wait_and_fire(session_key, delay_seconds)
        )
        logger.debug("Ping timer set for {} in {:.1f}h", session_key, delay_hours)

    async def _wait_and_fire(self, session_key: str, delay: float) -> None:
        """Wait for delay then fire the ping."""
        try:
            await asyncio.sleep(delay)
            await self._fire(session_key)
        except asyncio.CancelledError:
            pass

    async def _fire(self, session_key: str) -> None:
        """Generate and send a proactive message."""
        channel_info = self._last_channel.get(session_key)
        if not channel_info:
            return

        channel, chat_id = channel_info
        logger.info("Curiosity ping firing for {}", session_key)

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

    def cancel_all(self) -> None:
        """Cancel all pending timers (for shutdown)."""
        for task in self._timers.values():
            if not task.done():
                task.cancel()
        self._timers.clear()
```

### 3.2 Wire into Gateway

In `gateway/router.py`, add ping integration:

```python
class Gateway:
    def __init__(self, ..., ping: CuriosityPing | None = None):
        # ... existing ...
        self.ping = ping

    async def _handle(self, msg: InboundMessage) -> str | None:
        # Reset ping timer on every real user message
        if self.ping and msg.sender_id != "system":
            self.ping.reset_timer(msg.session_key, msg.channel, msg.chat_id, msg.content)

        # ... rest of handle logic unchanged ...
```

### 3.3 Wire into `main.py`

```python
from nekobot.gateway.ping import CuriosityPing

ping = CuriosityPing(config.ping, bus) if config.ping.enabled else None
gateway = Gateway(
    config=gw_cfg,
    message_bus=bus,
    memory_store=memory,
    prompt_builder=prompt_builder,
    usage_tracker=usage,
    ping=ping,
)

# On shutdown:
if ping:
    ping.cancel_all()
```

## 4. Config

Already defined in `config/schema.py`:

```python
class PingConfig(Base):
    enabled: bool = True
    min_hours: float = 2.0
    max_hours: float = 8.0
```

## 5. Acceptance Criteria

- [x] `gateway/ping.py` created with `CuriosityPing` class
- [x] Timer starts when user sends a message
- [x] Timer cancels and resets on next user message
- [x] Sleep keywords skip the ping
- [x] When timer fires, synthetic InboundMessage published to bus
- [ ] Gateway processes ping message and sends response to user (needs E2E test)
- [x] Single-shot: ping fires at most once per idle period
- [x] `cancel_all()` cleans up on shutdown
- [x] `Gateway.__init__` accepts optional `ping` parameter
- [x] `main.py` creates and wires CuriosityPing

## 6. Edge Cases

- User sends message just as ping is about to fire → timer cancelled, no duplicate
- Multiple channels active → each has independent timer
- Gateway restart → timers lost (acceptable; they restart on next user message)
- Ping message references "[SYSTEM]" → Claude should treat it as system instruction and not echo it to user. If it does, adjust the synthetic prompt wording.
