"""
MCP tool definitions for Claude Agent SDK.

Provides SDK-embedded MCP servers:
- memory: recall_memory tool for searching archived long-term knowledge
- im: send_message tool for cross-channel messaging
- cron: schedule_task tool for managing scheduled tasks

Usage:
    servers = build_mcp_servers(memory_store, message_bus, cron_service)
    gateway = Gateway(..., mcp_servers=servers)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from pathlib import Path

from nekobot.bus.events import InboundMessage, OutboundMessage
from nekobot.bus.queue import MessageBus
from nekobot.memory.search import search_archive
from nekobot.memory.store import MemoryStore

MEMORIZING_PROMPT_PATH = Path("~/.nekobot/prompts/MEMORIZING.md").expanduser()

if TYPE_CHECKING:
    from nekobot.cron.service import CronService


def build_mcp_servers(
    memory_store: MemoryStore,
    message_bus: MessageBus,
    cron_service: CronService | None = None,
) -> dict[str, Any]:
    """
    Build SDK-embedded MCP servers for memory, IM, and cron.

    Returns a dict suitable for ClaudeAgentOptions.mcp_servers.
    """
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ImportError:
        logger.warning("claude-agent-sdk not installed, MCP tools unavailable")
        return {}

    # ---- Memory server: recall_memory ----

    @tool(
        "recall_memory",
        "Search archived long-term knowledge by keywords. Use this to find past learnings, technical notes, and saved knowledge.",
        {"type": "object", "properties": {"query": {"type": "string", "description": "Search keywords"}}, "required": ["query"]},
    )
    async def recall_memory(args: dict[str, Any]) -> dict[str, Any]:
        query_text = args.get("query", "")
        results = search_archive(memory_store._archive_path, query_text)
        if not results:
            return {"content": [{"type": "text", "text": "No matching archived memories found."}]}
        lines = []
        for r in results:
            lines.append(f"**{r['title']}** ({r['path']})")
            lines.append(f"  {r['snippet']}")
            lines.append("")
        text = "\n".join(lines).strip()
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "memorize",
        "Compact conversation and tidy memory. Reads MEMORIZING.md instructions and triggers /compact with them. Call this when the user sends /memorizing.",
        {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Current channel (from Runtime section)"},
                "chat_id": {"type": "string", "description": "Current chat_id (from Runtime section)"},
            },
            "required": ["channel", "chat_id"],
        },
    )
    async def memorize(args: dict[str, Any]) -> dict[str, Any]:
        channel = args.get("channel", "")
        chat_id = args.get("chat_id", "")
        if not channel or not chat_id:
            return _text("Error: channel and chat_id are required.")

        try:
            instructions = MEMORIZING_PROMPT_PATH.read_text()
        except FileNotFoundError:
            return _text(f"Error: MEMORIZING.md not found at {MEMORIZING_PROMPT_PATH}")

        session_key = f"{channel}:{chat_id}"
        await message_bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="system",
                chat_id="memorize",
                content=f"/compact {instructions}",
                session_key_override=session_key,
            )
        )
        logger.info("Memorize tool: scheduled /compact for session {}", session_key)
        return _text("🧠 Memorizing scheduled. /compact will run after this exchange completes.")

    def _text(msg: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": msg}]}

    memory_server = create_sdk_mcp_server(
        name="nekobot-memory",
        version="1.0.0",
        tools=[recall_memory, memorize],
    )

    # ---- IM server: send_message ----

    @tool(
        "send_message",
        "Send a message to a specific IM channel and chat. Use for cross-channel messaging or proactive outreach.",
        {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name (telegram, discord, dingtalk)"},
                "chat_id": {"type": "string", "description": "Target chat/group ID"},
                "content": {"type": "string", "description": "Message content to send"},
            },
            "required": ["channel", "chat_id", "content"],
        },
    )
    async def send_message(args: dict[str, Any]) -> dict[str, Any]:
        channel = args.get("channel", "")
        chat_id = args.get("chat_id", "")
        content = args.get("content", "")
        await message_bus.publish_outbound(
            OutboundMessage(channel=channel, chat_id=chat_id, content=content)
        )
        logger.info("Tool send_message: {}:{}", channel, chat_id)
        return {"content": [{"type": "text", "text": "Message sent."}]}

    im_server = create_sdk_mcp_server(
        name="nekobot-im",
        version="1.0.0",
        tools=[send_message],
    )

    servers = {
        "nekobot-memory": memory_server,
        "nekobot-im": im_server,
    }

    # ---- Cron server: schedule_task ----

    if cron_service is not None:
        from nekobot.cron.types import CronJob, CronSchedule

        @tool(
            "schedule_task",
            "Create, list, or remove scheduled tasks. Jobs are persisted to disk and survive restarts — a cron job runs forever until explicitly removed. Use cron_expr for recurring schedules (e.g. daily, hourly). Each trigger creates a fresh Claude session.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "remove", "enable", "disable"],
                        "description": "Action to perform",
                    },
                    "name": {"type": "string", "description": "Task name (for add)"},
                    "message": {"type": "string", "description": "Prompt to execute when task fires (for add)"},
                    "cron_expr": {"type": "string", "description": "Cron expression, e.g. '0 9 * * *' (for add)"},
                    "every_seconds": {"type": "integer", "description": "Repeat interval in seconds (for add)"},
                    "at": {"type": "string", "description": "ISO datetime for one-shot, e.g. '2026-03-20T15:00' (for add)"},
                    "tz": {"type": "string", "description": "IANA timezone, e.g. 'Asia/Shanghai' (for add with cron)"},
                    "channel": {"type": "string", "description": "Channel to deliver response (telegram, dingtalk, etc.)"},
                    "chat_id": {"type": "string", "description": "Chat ID to deliver response to"},
                    "job_id": {"type": "string", "description": "Job ID (for remove/enable/disable)"},
                },
                "required": ["action"],
            },
        )
        async def schedule_task(args: dict[str, Any]) -> dict[str, Any]:
            action = args.get("action", "")

            if action == "list":
                jobs = cron_service.list_jobs()
                if not jobs:
                    return _text("No scheduled tasks.")
                lines = []
                for j in jobs:
                    status = "enabled" if j.enabled else "disabled"
                    sched = _format_schedule(j)
                    lines.append(f"- **{j.name}** (`{j.id}`) [{status}] {sched}")
                    if j.message:
                        lines.append(f"  Prompt: {j.message[:80]}{'...' if len(j.message) > 80 else ''}")
                return _text("\n".join(lines))

            elif action == "add":
                name = args.get("name", "Unnamed task")
                message = args.get("message", "")
                if not message:
                    return _text("Error: 'message' is required for add.")
                schedule = _parse_schedule(args)
                if schedule is None:
                    return _text("Error: provide one of cron_expr, every_seconds, or at.")
                job = CronJob(
                    name=name,
                    message=message,
                    schedule=schedule,
                    channel=args.get("channel"),
                    chat_id=args.get("chat_id"),
                    delete_after_run=schedule.kind == "at",
                )
                cron_service.add_job(job)
                return _text(f"Task '{name}' created (id: {job.id}, {_format_schedule(job)}).")

            elif action == "remove":
                job_id = args.get("job_id", "")
                if not job_id:
                    return _text("Error: 'job_id' is required for remove.")
                ok = cron_service.remove_job(job_id)
                return _text(f"Removed {job_id}." if ok else f"Job {job_id} not found.")

            elif action == "enable":
                job_id = args.get("job_id", "")
                ok = cron_service.enable_job(job_id)
                return _text(f"Enabled {job_id}." if ok else f"Job {job_id} not found.")

            elif action == "disable":
                job_id = args.get("job_id", "")
                ok = cron_service.disable_job(job_id)
                return _text(f"Disabled {job_id}." if ok else f"Job {job_id} not found.")

            else:
                return _text(f"Unknown action: {action}")

        def _text(msg: str) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": msg}]}

        def _parse_schedule(args: dict[str, Any]) -> CronSchedule | None:
            if args.get("cron_expr"):
                return CronSchedule(kind="cron", expr=args["cron_expr"], tz=args.get("tz"))
            if args.get("every_seconds"):
                return CronSchedule(kind="every", every_seconds=int(args["every_seconds"]))
            if args.get("at"):
                import datetime
                try:
                    dt = datetime.datetime.fromisoformat(args["at"])
                    return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
                except ValueError:
                    return None
            return None

        def _format_schedule(job: CronJob) -> str:
            s = job.schedule
            match s.kind:
                case "cron":
                    tz_str = f" ({s.tz})" if s.tz else ""
                    return f"cron: `{s.expr}`{tz_str}"
                case "every":
                    return f"every {s.every_seconds}s"
                case "at":
                    import datetime
                    dt = datetime.datetime.fromtimestamp(s.at_ms / 1000)
                    return f"at {dt.isoformat()}"
            return s.kind

        cron_server = create_sdk_mcp_server(
            name="nekobot-cron",
            version="1.0.0",
            tools=[schedule_task],
        )
        servers["nekobot-cron"] = cron_server

    return servers
