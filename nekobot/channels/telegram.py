"""Telegram channel implementation using python-telegram-bot."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from nekobot.bus.events import OutboundMessage
from nekobot.bus.queue import MessageBus
from nekobot.channels.base import BaseChannel
from nekobot.config.schema import TelegramConfig


def _markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram-safe HTML."""
    if not text:
        return ""

    # Protect code blocks
    code_blocks: list[str] = []

    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    # Protect inline code
    inline_codes: list[str] = []

    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # Strip markdown headers
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)
    # Strip blockquotes
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)
    # Escape HTML
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # Bullet lists
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # Restore inline code
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # Restore code blocks
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


class TelegramChannel(BaseChannel):
    """Telegram channel using long polling."""

    name = "telegram"

    def __init__(self, config: TelegramConfig, bus: MessageBus) -> None:
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self._app: Application | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}

    _START_RETRY_DELAY = 10  # seconds between connection retries

    async def start(self) -> None:
        if not self.config.token:
            logger.error("Telegram bot token not configured")
            return

        self._running = True

        # Retry loop: network/proxy may be temporarily unavailable
        while self._running:
            self._app = self._build_app()
            try:
                await self._app.initialize()
                await self._app.start()
                bot_info = await self._app.bot.get_me()
                logger.info("Telegram bot @{} connected", bot_info.username)
                await self._app.updater.start_polling(allowed_updates=["message"], drop_pending_updates=True)
                break
            except Exception as e:
                logger.warning("Telegram connect failed (retrying in {}s): {}", self._START_RETRY_DELAY, e)
                await self._shutdown_app()
                await asyncio.sleep(self._START_RETRY_DELAY)

        if not self._running:
            return

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        for cid in list(self._typing_tasks):
            self._stop_typing(cid)
        await self._shutdown_app()

    async def send(self, msg: OutboundMessage) -> None:
        if not self._app:
            return
        self._stop_typing(msg.chat_id)
        try:
            chat_id = int(msg.chat_id)
            html = _markdown_to_telegram_html(msg.content)
            await self._app.bot.send_message(chat_id=chat_id, text=html, parse_mode="HTML")
        except Exception as e:
            logger.warning("HTML send failed, falling back to plain text: {}", e)
            try:
                await self._app.bot.send_message(chat_id=int(msg.chat_id), text=msg.content)
            except Exception as e2:
                logger.error("Telegram send error: {}", e2)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        await update.message.reply_text(f"Hi {user.first_name}! Send me a message. 🐈‍⬛")

    async def _forward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        await self._handle_message(
            sender_id=str(update.effective_user.id),
            chat_id=str(update.message.chat_id),
            content=update.message.text or "",
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return

        message = update.message
        user = update.effective_user
        chat_id = str(message.chat_id)

        sender_id = str(user.id)
        if user.username:
            sender_id = f"{sender_id}|{user.username}"

        # Build content
        content_parts: list[str] = []
        media_paths: list[str] = []

        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)

        # Media handling (download to ~/.nekobot/media/)
        media_file = None
        media_type = None
        if message.photo:
            media_file = message.photo[-1]
            media_type = "image"
        elif message.voice:
            media_file = message.voice
            media_type = "voice"
        elif message.audio:
            media_file = message.audio
            media_type = "audio"
        elif message.document:
            media_file = message.document
            media_type = "file"

        if media_file and self._app:
            try:
                from pathlib import Path

                file = await self._app.bot.get_file(media_file.file_id)
                ext = self._get_extension(media_type, getattr(media_file, "mime_type", None))
                media_dir = Path.home() / ".nekobot" / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                file_path = media_dir / f"{media_file.file_id[:16]}{ext}"
                await file.download_to_drive(str(file_path))
                media_paths.append(str(file_path))
                content_parts.append(f"[{media_type}: {file_path}]")
            except Exception as e:
                logger.error("Failed to download media: {}", e)
                content_parts.append(f"[{media_type}: download failed]")

        content = "\n".join(content_parts) if content_parts else "[empty message]"

        self._start_typing(chat_id)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            media=media_paths,
            metadata={
                "message_id": message.message_id,
                "username": user.username,
                "first_name": user.first_name,
                "is_group": message.chat.type != "private",
            },
        )

    # ------------------------------------------------------------------
    # Typing indicator
    # ------------------------------------------------------------------

    def _start_typing(self, chat_id: str) -> None:
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    def _stop_typing(self, chat_id: str) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: str) -> None:
        try:
            while self._app:
                await self._app.bot.send_chat_action(chat_id=int(chat_id), action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Telegram error: {}", context.error)

    @staticmethod
    def _get_extension(media_type: str | None, mime_type: str | None) -> str:
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "audio/ogg": ".ogg",
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
            }
            if mime_type in ext_map:
                return ext_map[mime_type]
        return {"image": ".jpg", "voice": ".ogg", "audio": ".mp3", "file": ""}.get(media_type or "", "")

    def _build_request_kwargs(self) -> dict[str, Any]:
        """Build HTTPXRequest kwargs.

        Telegram channel should only use the proxy explicitly configured for it.
        Disable ambient HTTP(S)_PROXY inheritance to avoid accidental local
        proxy coupling.
        """
        req_kwargs: dict[str, Any] = {
            "connection_pool_size": 16,
            "pool_timeout": 5.0,
            "connect_timeout": 30.0,
            "read_timeout": 30.0,
            "httpx_kwargs": {"trust_env": False},
        }
        if self.config.proxy:
            req_kwargs["proxy"] = self.config.proxy
        return req_kwargs

    def _build_app(self) -> Application:
        req_kwargs = self._build_request_kwargs()
        req = HTTPXRequest(**req_kwargs)
        updates_req = HTTPXRequest(**req_kwargs)
        app = Application.builder().token(self.config.token).request(req).get_updates_request(updates_req).build()
        app.add_error_handler(self._on_error)
        app.add_handler(CommandHandler("start", self._on_start))
        app.add_handler(CommandHandler("new", self._forward_command))
        app.add_handler(CommandHandler("help", self._forward_command))
        app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL)
                & ~filters.COMMAND,
                self._on_message,
            )
        )
        return app

    async def _shutdown_app(self) -> None:
        app = self._app
        self._app = None
        if not app:
            return
        try:
            await app.updater.stop()
        except Exception:
            pass
        try:
            await app.stop()
        except Exception:
            pass
        try:
            await app.shutdown()
        except Exception:
            pass
