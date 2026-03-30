# SDD-06: Error Resilience & Graceful Degradation

## Priority: MEDIUM
## Depends On: SDD-03 (working Gateway)
## Estimated Scope: 2 files modified, ~80 lines

---

## 1. Goal

Make the Gateway robust against transient failures: SDK errors, network timeouts, session corruption, MCP tool failures. Currently errors are caught-and-logged but not retried or handled gracefully.

## 2. Current State

`gateway/router.py` line 96-97:
```python
except Exception:
    logger.exception("Error handling message from {}:{}", msg.channel, msg.chat_id)
```

This catches everything but:
- Does not send an error message back to the user (they see nothing)
- Does not retry transient failures
- Does not handle specific SDK error types
- Does not track error frequency (no circuit breaker)

## 3. Design

### 3.1 Error Response to User

When processing fails, send a brief error message back to the IM channel so the user isn't left waiting:

```python
async def run(self) -> None:
    while True:
        msg = await self.bus.consume_inbound()
        try:
            response = await self._handle(msg)
            if response:
                await self.bus.publish_outbound(
                    OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=response)
                )
        except Exception:
            logger.exception("Error handling message from {}:{}", msg.channel, msg.chat_id)
            # Notify user
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="something broke, try again later 🐈‍⬛",
                )
            )
```

### 3.2 Retry with Backoff

For transient SDK/network errors, retry up to 2 times with exponential backoff:

```python
import asyncio

MAX_RETRIES = 2
RETRY_BASE_DELAY = 2.0  # seconds

async def _handle_with_retry(self, msg: InboundMessage) -> str | None:
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await self._handle(msg)
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
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
    raise last_error
```

Update `run()` to call `_handle_with_retry()` instead of `_handle()`.

### 3.3 Session Recovery

If `resume=session_id` fails (corrupted session, expired, etc.), fall back to starting a new session:

```python
async def _handle(self, msg: InboundMessage) -> str | None:
    # ... existing setup ...

    try:
        return await self._query_claude(msg, session_id)
    except Exception as e:
        error_str = str(e).lower()
        # Detect session-related errors and retry without resume
        if session_id and ("session" in error_str or "resume" in error_str or "not found" in error_str):
            logger.warning("Session {} appears invalid, starting fresh", session_id[:8])
            self._sessions.pop(msg.session_key, None)
            self._save_sessions()
            return await self._query_claude(msg, session_id=None)
        raise
```

### 3.4 SDK Import Guard

Already partially done — `_handle()` catches `ImportError` for `claude_agent_sdk`. Ensure the error message is user-friendly:

```python
try:
    from claude_agent_sdk import ...
except ImportError:
    logger.error("claude-agent-sdk not installed")
    return "I can't think right now — SDK not available. 🐈‍⬛"
```

### 3.5 Prompt Template Guard

In `gateway/prompt.py`, handle missing template gracefully:

```python
def _load_template(self) -> str:
    if not self._template_path.exists():
        logger.error("System prompt template not found: {}", self._template_path)
        return "You are a helpful assistant."  # Minimal fallback
    return self._template_path.read_text()
```

## 4. Files to Modify

### `gateway/router.py`
- Add `_handle_with_retry()` wrapper
- Update `run()` to use retry wrapper and send error messages to user
- Add session recovery logic in `_handle()`
- Extract `_query_claude()` from `_handle()` for cleaner retry/recovery

### `gateway/prompt.py`
- Add fallback for missing template file

## 5. Acceptance Criteria

- [x] User receives an error message when processing fails (not silent)
- [x] Transient failures are retried up to 2 times with backoff
- [x] Invalid sessions are detected and a new session is started automatically
- [x] Missing system prompt template produces a fallback instead of crash
- [x] SDK import failure produces a user-visible error instead of crash
- [x] All error paths log with appropriate level (warning for retry, error for final failure)

## 6. What NOT to Build

- No circuit breaker (overkill for personal assistant)
- No error rate monitoring dashboard
- No automatic alerting
- No request queuing during outages

Keep it simple: retry, recover, notify user.
