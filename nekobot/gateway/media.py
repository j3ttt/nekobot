"""Media processing: voice transcription and file preparation."""

from __future__ import annotations

import re
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
        """
        Initialize MediaHandler with optional transcription API key.

        Args:
            transcription_api_key: Groq API key for Whisper transcription
            proxy: Optional proxy URL for HTTP requests
        """
        self._api_key = transcription_api_key
        self._proxy = proxy

    async def process_content(self, content: str) -> str:
        """
        Process message content, replacing media tags with transcriptions.

        Looks for patterns like [voice: /path/to/file.ogg] or [audio: /path/to/file.ogg]
        and replaces with [voice transcription: <text>].

        Images ([image: /path]) are left as-is since Claude Code can read them directly.

        Args:
            content: Message content with potential media tags

        Returns:
            Processed content with transcriptions inserted
        """
        if not self._api_key:
            # No API key configured, return content as-is
            return content

        # Process voice/audio tags
        pattern = re.compile(r"\[(voice|audio):\s*([^\]]+)\]")
        result = content

        for match in pattern.finditer(content):
            tag_type = match.group(1)
            file_path = match.group(2).strip()
            transcription = await self.transcribe(Path(file_path))

            if transcription:
                replacement = f"[voice transcription: {transcription}]"
                result = result.replace(match.group(0), replacement)
                logger.debug(
                    "Replaced [{}] tag with transcription: {}...",
                    tag_type,
                    transcription[:50],
                )

        return result

    async def transcribe(self, file_path: Path) -> str | None:
        """
        Transcribe audio file using Groq Whisper API.

        Args:
            file_path: Path to audio file (supports various formats: ogg, mp3, wav, etc.)

        Returns:
            Transcribed text, or None if transcription fails
        """
        if not file_path.exists():
            logger.warning("Audio file not found: {}", file_path)
            return None

        if not self._api_key:
            logger.debug("No transcription API key, skipping transcription")
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0, proxy=self._proxy) as client:
                with open(file_path, "rb") as f:
                    # Groq uses OpenAI-compatible Whisper API
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
                    logger.warning(
                        "Transcription failed: HTTP {} - {}",
                        resp.status_code,
                        resp.text[:100],
                    )
                    return None

        except httpx.TimeoutException:
            logger.error("Transcription timeout for {}", file_path.name)
            return None
        except Exception as e:
            logger.error("Transcription error for {}: {}", file_path.name, e)
            return None
