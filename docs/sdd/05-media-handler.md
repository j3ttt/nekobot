# SDD-05: Media Handler (Voice Transcription + Image)

## Priority: MEDIUM
## Depends On: SDD-03 (working Gateway)
## Estimated Scope: 1 new file, 1 file modified, ~80 lines

---

## 1. Goal

Handle non-text media from IM channels: transcribe voice messages to text, and prepare images for Claude (via file path, since Claude Code can read images with the Read tool).

## 2. Current State

`channels/telegram.py` already downloads media files to `~/.nekobot/media/` and appends `[voice: /path/to/file]` or `[image: /path/to/file]` to the message content. However:
- No voice transcription (nanobot used Groq Whisper API)
- Images are referenced by path but not explicitly handled

## 3. Design

### 3.1 Voice Transcription

Two options:
- **Option A**: Use OpenAI Whisper API (or Groq's Whisper endpoint) — requires API key
- **Option B**: Let Claude handle it — Claude Code can invoke `ffmpeg` via Bash and use a local whisper model

**Chosen: Option A (API-based)** for reliability. Claude Code can't natively transcribe audio. Add a config field for the transcription API key.

### 3.2 Image Handling

Claude Code's `Read` tool can view images directly. The Telegram channel already saves images and puts `[image: /path]` in the content. Claude will naturally use `Read` to view the file.

No additional handling needed for images. Just ensure the path is accessible from the workspace.

### 3.3 Class: `MediaHandler`

```python
# nekobot/gateway/media.py

"""Media processing: voice transcription and file preparation."""

from pathlib import Path

import httpx
from loguru import logger


class MediaHandler:
    """
    Processes media files before they reach Claude.

    Currently handles:
    - Voice/audio → text transcription (Groq Whisper API)
    - Images → no-op (Claude Code reads images directly via Read tool)
    """

    def __init__(self, transcription_api_key: str = "", proxy: str | None = None) -> None:
        self._api_key = transcription_api_key
        self._proxy = proxy

    async def process_content(self, content: str) -> str:
        """
        Process message content, replacing media tags with transcriptions.

        Looks for patterns like [voice: /path/to/file.ogg] and replaces
        with [voice transcription: <text>].
        """
        import re

        async def replace_voice(match: re.Match) -> str:
            file_path = match.group(1).strip()
            if not self._api_key:
                return match.group(0)  # No API key, leave as-is
            transcription = await self.transcribe(Path(file_path))
            if transcription:
                return f"[voice transcription: {transcription}]"
            return match.group(0)

        # Process voice/audio tags
        pattern = re.compile(r"\[(voice|audio): ([^\]]+)\]")
        result = content
        for match in pattern.finditer(content):
            file_path = match.group(2).strip()
            if not self._api_key:
                continue
            transcription = await self.transcribe(Path(file_path))
            if transcription:
                result = result.replace(match.group(0), f"[voice transcription: {transcription}]")

        return result

    async def transcribe(self, file_path: Path) -> str | None:
        """Transcribe audio file using Groq Whisper API."""
        if not file_path.exists():
            logger.warning("Audio file not found: {}", file_path)
            return None

        if not self._api_key:
            logger.debug("No transcription API key, skipping transcription")
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0, proxy=self._proxy) as client:
                with open(file_path, "rb") as f:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        files={"file": (file_path.name, f, "audio/ogg")},
                        data={"model": "whisper-large-v3"},
                    )
                if resp.status_code == 200:
                    text = resp.json().get("text", "").strip()
                    logger.info("Transcribed {}: {}...", file_path.name, text[:50])
                    return text
                else:
                    logger.warning("Transcription failed: HTTP {}", resp.status_code)
                    return None
        except Exception as e:
            logger.error("Transcription error: {}", e)
            return None
```

### 3.4 Config Addition

Add to `config/schema.py` in `GatewayConfig`:

```python
class GatewayConfig(Base):
    # ... existing fields ...
    transcription_api_key: str = ""  # Groq API key for Whisper transcription
    transcription_proxy: str | None = None
```

### 3.5 Wire into Gateway

In `gateway/router.py`, process media before sending to Claude:

```python
class Gateway:
    def __init__(self, ..., media_handler: MediaHandler | None = None):
        self.media = media_handler

    async def _handle(self, msg: InboundMessage) -> str | None:
        # ... existing code ...

        # Pre-process media (voice transcription)
        content = msg.content
        if self.media and msg.media:
            content = await self.media.process_content(content)

        # Use processed content instead of msg.content for query()
        # ... rest of handle logic, but pass `content` to query(prompt=content, ...)
```

### 3.6 Wire into `main.py`

```python
from nekobot.gateway.media import MediaHandler

media = None
if gw_cfg.transcription_api_key:
    media = MediaHandler(
        transcription_api_key=gw_cfg.transcription_api_key,
        proxy=gw_cfg.transcription_proxy,
    )

gateway = Gateway(..., media_handler=media)
```

## 4. Acceptance Criteria

- [x] `gateway/media.py` created with `MediaHandler` class
- [ ] Voice messages are transcribed via Groq Whisper API when API key is configured (needs Groq key to test)
- [x] `[voice: /path]` in content is replaced with `[voice transcription: text]`
- [x] Images are left as-is (`[image: /path]`) — Claude reads them directly
- [x] No error when API key is not configured (graceful no-op)
- [x] `GatewayConfig` has `transcription_api_key` and `transcription_proxy` fields
- [x] `main.py` creates and wires MediaHandler when API key is set

## 5. Testing

```bash
# Place a test .ogg file at /tmp/test_voice.ogg
python -c "
import asyncio
from nekobot.gateway.media import MediaHandler
h = MediaHandler(transcription_api_key='YOUR_GROQ_KEY')
result = asyncio.run(h.process_content('[voice: /tmp/test_voice.ogg]'))
print(result)
"
```
