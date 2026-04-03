"""Configuration schema for nekobot."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Base(BaseModel):
    """Base model accepting both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class GatewayConfig(Base):
    """Core gateway configuration."""

    workspace: str = "~/.nekobot/workspace"
    data_dir: str = "~/.nekobot/data"
    prompts_dir: str = "~/.nekobot/prompts"
    memory_path: str = "~/.nekobot/memory"
    permission_mode: str = "bypassPermissions"
    cli_path: str | None = None  # Path to Claude Code binary (None = auto-detect)
    model: str | None = None  # None = Claude Code auto-selects
    forward_thinking: bool = True
    max_turns: int | None = None
    max_budget_usd: float | None = None
    transcription_api_key: str = ""  # Groq API key for Whisper transcription
    transcription_proxy: str | None = None
    state_ws_port: int = 0  # WebSocket port for StateEmitter (0 = disabled)
    state_ws_host: str = "127.0.0.1"

    @property
    def workspace_resolved(self) -> Path:
        return Path(self.workspace).expanduser()

    @property
    def data_dir_resolved(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def prompts_dir_resolved(self) -> Path:
        return Path(self.prompts_dir).expanduser()

    @property
    def memory_path_resolved(self) -> Path:
        return Path(self.memory_path).expanduser()


# ---------------------------------------------------------------------------
# Channels  (ported from nanobot, keeping only what we need initially)
# ---------------------------------------------------------------------------


class TelegramConfig(Base):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None
    reply_to_message: bool = False


class DiscordConfig(Base):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377
    group_policy: Literal["mention", "open"] = "mention"


class DingTalkConfig(Base):
    """DingTalk channel configuration."""

    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    allow_from: list[str] = Field(default_factory=list)


class ChannelsConfig(Base):
    """Chat channel configurations."""

    send_progress: bool = True
    send_tool_hints: bool = False
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)


# ---------------------------------------------------------------------------
# Curiosity Ping
# ---------------------------------------------------------------------------


class PingConfig(Base):
    """Curiosity ping (proactive messaging) configuration."""

    enabled: bool = True
    min_hours: float = 2.0
    max_hours: float = 8.0


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class Config(Base):
    """Root configuration for nekobot."""

    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    ping: PingConfig = Field(default_factory=PingConfig)
