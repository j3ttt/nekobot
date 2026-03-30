"""CLI entry point — Typer app with `gateway` and `agent` subcommands."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

app = typer.Typer(
    name="nekobot",
    help="NekoBot — Personal AI assistant: Claude Code + personality + long-term memory + IM gateway.",
    no_args_is_help=True,
)

console = Console()

_EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}
_LOGO = "🐈‍⬛"

# ---------------------------------------------------------------------------
# Terminal / prompt_toolkit helpers
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return
    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:
        pass


def _restore_terminal() -> None:
    """Restore terminal to its original state."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".nekobot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,
    )


async def _read_input_async() -> str:
    """Read user input via prompt_toolkit (handles paste, history, arrows)."""
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def _print_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with rich formatting."""
    content = response or ""
    console.print()
    console.print(f"[cyan]{_LOGO} nekobot[/cyan]")
    body = Markdown(content) if render_markdown else Text(content)
    console.print(body)
    console.print()


# ---------------------------------------------------------------------------
# Shared init
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "WARNING"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<7} | {message}")


def _init_gateway(
    config_path: str | None = None,
    *,
    no_mcp: bool = False,
):
    """Shared initialisation: bootstrap → load config → build components.

    Returns (config, bus, gateway, ping, cron_service).
    """
    from nekobot.bootstrap import ensure_home
    from nekobot.bus.queue import MessageBus
    from nekobot.config.loader import load_config
    from nekobot.cron.service import CronService
    from nekobot.cron.store import CronStore
    from nekobot.gateway.media import MediaHandler
    from nekobot.gateway.ping import CuriosityPing
    from nekobot.gateway.prompt import PromptBuilder
    from nekobot.gateway.router import Gateway
    from nekobot.gateway.state import StateEmitter
    from nekobot.gateway.tools import build_mcp_servers
    from nekobot.memory.store import MemoryStore
    from nekobot.usage.tracker import UsageTracker

    ensure_home()
    config = load_config(config_path)
    gw_cfg = config.gateway

    # Ensure runtime directories
    gw_cfg.workspace_resolved.mkdir(parents=True, exist_ok=True)
    gw_cfg.data_dir_resolved.mkdir(parents=True, exist_ok=True)

    bus = MessageBus()
    memory = MemoryStore(gw_cfg.memory_path_resolved)
    usage = UsageTracker(gw_cfg.data_dir_resolved)
    prompt_builder = PromptBuilder(gw_cfg.prompts_dir_resolved, memory)

    state_emitter = None
    if gw_cfg.state_ws_port > 0:
        state_emitter = StateEmitter(host=gw_cfg.state_ws_host, port=gw_cfg.state_ws_port)
        logger.info("StateEmitter configured on ws://{}:{}/", gw_cfg.state_ws_host, gw_cfg.state_ws_port)

    cron_store = CronStore(gw_cfg.data_dir_resolved / "cron" / "jobs.json")
    cron_service = CronService(cron_store, bus, state=state_emitter)

    mcp_servers = {} if no_mcp else build_mcp_servers(memory, bus, cron_service)

    media = None
    if gw_cfg.transcription_api_key:
        media = MediaHandler(
            transcription_api_key=gw_cfg.transcription_api_key,
            proxy=gw_cfg.transcription_proxy,
        )
        logger.info("MediaHandler initialized with transcription support")

    ping = None
    if config.ping.enabled:
        ping = CuriosityPing(config.ping, bus, state=state_emitter)
        logger.info(
            "CuriosityPing enabled (idle: {:.1f}-{:.1f}h)",
            config.ping.min_hours,
            config.ping.max_hours,
        )

    gw = Gateway(
        config=gw_cfg,
        message_bus=bus,
        memory_store=memory,
        prompt_builder=prompt_builder,
        usage_tracker=usage,
        mcp_servers=mcp_servers,
        media_handler=media,
        ping=ping,
        state=state_emitter,
    )

    return config, bus, gw, ping, cron_service, state_emitter


# ---------------------------------------------------------------------------
# gateway command
# ---------------------------------------------------------------------------


@app.command()
def gateway(
    config: Optional[str] = typer.Option(None, "--config", help="Path to config.yaml"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Start the NekoBot gateway server (channels + message loop)."""
    _setup_logging(verbose)
    logger.info("NekoBot gateway starting...")
    cfg, bus, gw, ping, cron, state = _init_gateway(config)
    asyncio.run(_run_gateway(cfg, bus, gw, ping, cron, state))


async def _run_gateway(config, bus, gw, ping, cron, state) -> None:
    """Run gateway + channels + cron + state emitter until KeyboardInterrupt."""
    from nekobot.channels.manager import ChannelManager

    channel_mgr = ChannelManager(config, bus)
    if channel_mgr.enabled_channels:
        logger.info("Enabled channels: {}", ", ".join(channel_mgr.enabled_channels))
    else:
        logger.warning("No channels enabled — gateway running in headless mode")

    tasks = [gw.run(), channel_mgr.start_all(), cron.start()]
    if state:
        tasks.append(state.run())

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        console.print(f"\n{_LOGO} Shutting down...")
        if ping:
            ping.cancel_all()
        if state:
            await state.stop()
        await cron.stop()
        await gw.shutdown()
        await channel_mgr.stop_all()


# ---------------------------------------------------------------------------
# agent command
# ---------------------------------------------------------------------------


@app.command()
def agent(
    config: Optional[str] = typer.Option(None, "--config", help="Path to config.yaml"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Single message (non-interactive)"),
    session: str = typer.Option("cli:local", "--session", "-s", help="Session key"),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Disable MCP tools"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render output as Markdown"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Interactive CLI chat with the agent (no IM channels)."""
    _setup_logging(verbose)
    _, bus, gw, _, _, _ = _init_gateway(config, no_mcp=no_mcp)
    asyncio.run(_run_agent(bus, gw, session, message, markdown))


async def _run_agent(
    bus, gw, session_key: str, single_message: str | None, render_markdown: bool
) -> None:
    """Agent interaction loop."""
    from nekobot.bus.events import InboundMessage

    channel, chat_id = session_key.split(":", 1) if ":" in session_key else ("cli", session_key)

    async def _process(text: str) -> None:
        """Send a message and print all outbound replies from the bus."""
        msg = InboundMessage(channel=channel, sender_id=chat_id, chat_id=chat_id, content=text)
        await bus.publish_inbound(msg)
        inbound = await bus.consume_inbound()
        await gw._handle(inbound)

        # Drain all outbound messages that were pushed during _handle
        got_any = False
        while not bus.outbound.empty():
            out = bus.outbound.get_nowait()
            if out.content:
                got_any = True
                _print_response(out.content, render_markdown)
        if not got_any:
            console.print("\n[dim](no response)[/dim]\n")

    # Single message mode
    if single_message:
        try:
            with console.status(f"[dim]{_LOGO} thinking...[/dim]", spinner="dots"):
                await _process(single_message)
        except Exception as e:
            console.print(f"[red]Error: {e!r}[/red]")
            raise typer.Exit(1)
        finally:
            await gw.shutdown()
        return

    # Interactive mode
    _init_prompt_session()
    console.print(
        f"\n{_LOGO} [bold]NekoBot[/bold] interactive mode "
        f"(type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n"
    )

    def _exit_on_sigint(signum, frame):
        _restore_terminal()
        console.print(f"\n{_LOGO} Goodbye!")
        os._exit(0)

    signal.signal(signal.SIGINT, _exit_on_sigint)

    try:
        while True:
            try:
                _flush_pending_tty_input()
                user_input = await _read_input_async()
                command = user_input.strip()
                if not command:
                    continue

                if command.lower() in _EXIT_COMMANDS:
                    break

                with console.status(f"[dim]{_LOGO} thinking...[/dim]", spinner="dots"):
                    await _process(command)

            except KeyboardInterrupt:
                break
            except EOFError:
                break
            except Exception as e:
                console.print(f"\n[red]Error: {e!r}[/red]")
                if isinstance(e, BaseExceptionGroup):
                    for idx, sub in enumerate(e.exceptions, start=1):
                        console.print(f"[red]  Sub-exception {idx}: {sub!r}[/red]")
    finally:
        _restore_terminal()
        console.print(f"\n{_LOGO} Goodbye!")
        await gw.shutdown()
