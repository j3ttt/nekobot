# CLAUDE.md — NekoBot Agent Contract

> **This file is the single source of truth for all development agents.**
> Read this FIRST before touching any code. After completing work, update HANDOFF.md.

## Project

NekoBot wraps Claude Code via Claude Agent SDK, adding personality, long-term memory, and multi-channel IM gateway. ~2,930 lines Python, 65 tests passing.

## Commands

```bash
pytest tests/ -v          # run all tests
pytest tests/ -q          # quick summary
ruff check nekobot/       # lint
python -m compileall nekobot  # verify syntax
nekobot agent              # interactive CLI chat (needs Claude auth)
nekobot agent -m "hello"   # single message mode
nekobot gateway            # start IM channels + gateway server
```

## Conventions

- Python 3.11+, type hints everywhere
- `loguru` for logging (not stdlib `logging`)
- `pydantic` for config, `dataclass` for internal models
- No over-engineering: build what's needed now
- No docstrings/comments on code you didn't change
- Runtime  `~/.nekobot/` — version-controlled templates: `data/defaults/`

## Architecture

```
IM Channels (Telegram / DingTalk / Discord)
    ↓ InboundMessage
MessageBus (asyncio.Queue)
    ↓
Gateway
  ├─ PromptBuilder (SOUL/USER/AGENTS + memory + runtime)
  ├─ ClaudeSDKClient (persistent per session)
  ├─ MemoryStore (core/active/archive/journal)
  ├─ MCP Tools (recall_memory, send_message)
  ├─ MediaHandler (voice → text via Groq Whisper)
  ├─ CuriosityPing (proactive messaging)
  └─ UsageTracker (JSONL cost log)
    ↓ OutboundMessage
MessageBus → IM Channels
```

---

## Module Boundaries

> **Rule: Do NOT modify files outside your assigned module without coordinating via HANDOFF.md.**

| Module | Directory | Owner files | Public interface |
|--------|-----------|-------------|-----------------|
| **bus** | `nekobot/bus/` | `events.py`, `queue.py` | `InboundMessage`, `OutboundMessage`, `MessageBus` |
| **channels** | `nekobot/channels/` | `base.py`, `manager.py`, `telegram.py`, `dingtalk.py` | `BaseChannel`, `ChannelManager` |
| **config** | `nekobot/config/` | `schema.py`, `loader.py` | `Config`, `load_config()` |
| **gateway** | `nekobot/gateway/` | `router.py`, `prompt.py`, `tools.py`, `ping.py`, `media.py` | `Gateway` |
| **memory** | `nekobot/memory/` | `store.py`, `extractor.py`, `search.py` | `MemoryStore`, `extract_memory_writes()`, `search_archive()` |
| **usage** | `nekobot/usage/` | `tracker.py` | `UsageTracker` |
| **bootstrap** | `nekobot/` | `bootstrap.py` | `ensure_home()` |
| **cron** | `nekobot/cron/` | `types.py`, `store.py`, `service.py` | `CronJob`, `CronStore`, `CronService` |
| **cli** | `nekobot/` | `cli.py`, `__main__.py`, `main.py` | `app` (Typer), `gateway`, `agent` commands |

### Shared files (coordinate before modifying)

- `pyproject.toml` — dependencies, project metadata
- `nekobot/cli.py` — CLI commands + init logic, touches all modules
- `nekobot/main.py` — thin wrapper to `cli.app()`
- `data/defaults/` — bootstrap seed files
- `data/config.example.yaml` — user-facing config template

---

## Interface Contracts

### bus/events.py — Message types

```python
@dataclass
class InboundMessage:
    channel: str          # "telegram", "dingtalk", etc.
    sender_id: str
    chat_id: str
    content: str
    timestamp: datetime
    media: list[str]      # base64 or URL
    metadata: dict[str, Any]
    session_key_override: str | None  # override default "channel:chat_id"

    @property
    def session_key(self) -> str: ...  # "channel:chat_id"

@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    reply_to: str | None
    media: list[str]
    metadata: dict[str, Any]
```

### channels/base.py — Channel interface

```python
class BaseChannel(ABC):
    name: str
    def __init__(self, config: Any, bus: MessageBus) -> None: ...
    async def start(self) -> None: ...       # long-running listener
    async def stop(self) -> None: ...        # cleanup
    async def send(self, msg: OutboundMessage) -> None: ...
    def is_allowed(self, sender_id: str) -> bool: ...  # checks config.allow_from
```

### config/schema.py — Config hierarchy

```python
Config
  ├─ gateway: GatewayConfig
  │    workspace, data_dir, prompts_dir, memory_path,
  │    permission_mode, model, forward_thinking,
  │    max_turns, max_budget_usd, transcription_api_key
  │    (all have *_resolved -> Path properties)
  ├─ channels: ChannelsConfig
  │    telegram: TelegramConfig (enabled, token, allow_from, proxy)
  │    discord: DiscordConfig (enabled, token, allow_from)
  │    dingtalk: DingTalkConfig (enabled, client_id, client_secret, allow_from)
  └─ ping: PingConfig (enabled, min_hours, max_hours)
```

### gateway/router.py — Core gateway

```python
class Gateway:
    def __init__(self, config, bus, memory, prompt_builder, usage, media_handler, ping): ...
    async def run(self) -> None: ...          # main loop: consume inbound → Claude → outbound
    async def shutdown(self) -> None: ...     # clean disconnect all clients
    # Internal:
    #   _clients: dict[str, ClaudeSDKClient]  # per session_key
    #   _sessions: dict[str, str]             # session_key → session_id
    #   _session_errors: dict[str, str]       # error context for next resume
```

### Session Data Storage

Session 持久化分两层，迁移到其他机器时**两者都需要复制**：

| 数据 | 路径 | 说明 |
|------|------|------|
| session_id 映射 | `~/.nekobot/data/sessions.json` | nekobot 管理，session_key → session_id |
| 对话历史 | `~/.claude/projects/{cwd-path-hash}/*.jsonl` | Claude Code 内部管理，不可配置路径 |

- `{cwd-path-hash}` 由 cwd 路径生成（如 `-Users-alice--nekobot-workspace` 对应 `~/.nekobot/workspace`）
- 迁移步骤：复制 `sessions.json` + 对应的 `~/.claude/projects/` 目录 → 目标机器相同路径
- 目标机器的 `gateway.workspace` 配置必须与源机器一致，否则 cwd-path-hash 不同，Claude Code 找不到历史

### memory/store.py — Memory operations

```python
class MemoryStore:
    def __init__(self, memory_path: Path): ...
    def render_core(self) -> str: ...         # for prompt injection
    def render_active(self) -> str: ...       # for prompt injection
    def write_facts(self, facts: list[tuple[str, str, str]]) -> None: ...
    # facts = [(category, key, value), ...]
    # category routing: profile/preference/relationship → core.json
    #                   project/todo/recent_event → active.json
    #                   reference/learning/tech_detail → archive/{cat}/{key}.md
```

### memory/extractor.py

```python
def extract_memory_writes(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Returns (cleaned_text_without_tags, [(category, key, value), ...])"""
```

### gateway/tools.py — MCP tools

```python
def build_mcp_servers(memory_store: MemoryStore, message_bus: MessageBus, cron_service: CronService | None = None) -> dict[str, Any]:
    """Returns {"nekobot-memory": ..., "nekobot-im": ..., "nekobot-cron": ...} for ClaudeAgentOptions.mcp_servers"""
    # Tools: recall_memory(query: str), send_message(channel, chat_id, content),
    #        schedule_task(action, ...) — add/list/remove/enable/disable cron jobs
```

### gateway/prompt.py — Prompt assembly

```python
class PromptBuilder:
    PROMPT_FILES = ["SOUL.md", "USER.md", "AGENTS.md"]
    def __init__(self, prompts_dir: Path, memory_store: MemoryStore): ...
    def build(self, channel: str, chat_id: str) -> str: ...
    # Assembly: SOUL → USER → AGENTS → Memory Core → Memory Active → Runtime
    # Files re-read on every build() call (hot-reload)
```

---

## Current Status

### Done

- [x] Message bus (async queue decoupling)
- [x] Telegram channel (long polling, Markdown→HTML)
- [x] DingTalk channel (Stream Mode, images, groups)
- [x] Config system (Pydantic schema + YAML loader)
- [x] 4-layer memory store (core/active/archive/journal)
- [x] Memory extractor (`<memory_write>` tag parsing)
- [x] Archive search (keyword matching)
- [x] Prompt builder (3-layer SOUL/USER/AGENTS + memory + runtime injection)
- [x] Gateway router (ClaudeSDKClient, session persistence, typed error handling)
- [x] MCP tools (recall_memory, send_message, schedule_task)
- [x] Curiosity Ping (proactive messaging timer)
- [x] Media handler (voice transcription via Groq Whisper)
- [x] Usage tracker (JSONL cost/token log)
- [x] stderr capture + error context injection
- [x] Streaming intermediate replies during tool-call chains
- [x] Message timestamp injection (`[YYYY-MM-DD HH:MM]` prefix)
- [x] Unit tests (92 tests, all passing)
- [x] claude-code-sdk → claude-agent-sdk migration
- [x] SDD-08: Bootstrap system (`bootstrap.py`, `data/defaults/`, prompt layering SOUL/USER/AGENTS)
- [x] SDD-09: Typer CLI (`nekobot gateway` / `nekobot agent` subcommands)
- [x] PLAN-001: Agent Skills + Slash Commands (SDK 原生 `.claude/skills/` + `.claude/commands/`)
- [x] PLAN-002: Cron 定时任务模块 (`nekobot/cron/`, MCP tool `schedule_task`, `croniter`)
- [x] REQ-004: 消息时间戳注入
- [x] PLAN-005: Visual Avatar — StateEmitter + WebSocket (`gateway/state.py`, 6 状态优先级, ws://host:port 广播)

### Planned (REQ + PLAN written, awaiting implementation)

- [ ] PLAN-003: Daily Digest Skill (自包含 Skill: fetch.py + sources.yaml)

### Not Started (no REQ/PLAN yet)

- [ ] Discord channel (skeleton only)
- [ ] Visual Avatar 显示端（macOS app / ESP32 / Web，对接 StateEmitter WebSocket）
- [ ] E2E integration tests with real Claude
- [ ] Web dashboard / monitoring
- [ ] Embedding-based archive search (V2)

---

## Multi-Agent Coordination Protocol

### Before starting work

1. Read this file (CLAUDE.md)
2. Read HANDOFF.md for recent changes by other agents
3. Check which module/files you're assigned to work on
4. If your task touches shared files or another module's interface, note it in HANDOFF.md BEFORE starting

### While working

- Stay within your assigned module boundaries
- If you need to change an interface contract listed above, **stop and coordinate**
- Run `pytest tests/ -q` before considering your work done

### After completing work

Append to HANDOFF.md:

```markdown
## YYYY-MM-DD HH:MM CST

- Recorder: <your-identifier>
- Module: <which module you changed>
- Context: <1-line summary>

### Changes
- <file>: <what changed>

### Interface Changes (if any)
- <old signature> → <new signature>
- Impact: <which other modules are affected>

### New Tests
- <test file>: <what it tests>
```

### File lock convention

If you're about to modify a shared file, add a comment at the top of your HANDOFF entry:
```
### Lock: nekobot/main.py (in progress)
```
Remove it when done. Other agents should check for locks before modifying shared files.

---

## Agent Roles

| Role | Reads | Writes | 职责 |
|------|-------|--------|------|
| **Planner** | `docs/requirements/`, `CLAUDE.md`, `docs/architecture.md` | `docs/plans/PLAN-xxx.md`, `HANDOFF.md` | 接收需求，设计 Plan，分解 Task |
| **Dev** | `CLAUDE.md`, `HANDOFF.md`, assigned `PLAN-xxx.md` | code, tests, `HANDOFF.md` | 按 Plan 实现，不跨模块 |
| **Reviewer** | `CLAUDE.md`, `HANDOFF.md`, code diff | review comments | 对照 Plan 检查实现 |

### Planner Agent 工作流

```
1. 读 docs/requirements/REQ-xxx.md（需求）
2. 读 CLAUDE.md（模块边界 + 接口契约）
3. 读 docs/architecture.md（架构原则）
4. 读 HANDOFF.md（在途工作，避免冲突）
5. 产出 docs/plans/PLAN-xxx.md
   - 设计决策、接口变更、Task 分解、文件清单
6. 更新 REQ status → planned
7. 广播到 HANDOFF.md（新 Plan 可供领取）
```

详细模板见 `docs/plans/README.md` 和 `docs/requirements/README.md`。

---

## Docs Map

| File | Purpose | Audience |
|------|---------|----------|
| `CLAUDE.md` (this file) | Agent contract, module boundaries, interface specs | All agents |
| `HANDOFF.md` | Chronological change broadcast log | All agents |
| `README.md` | User-facing setup & overview | End users |
| `docs/architecture.md` | Full architecture design (Chinese) | Planner, architecture reference |
| `docs/research.md` | SDK research notes | Background reading |
| `docs/requirements/` | 需求收纳（REQ-xxx） | Planner agent |
| `docs/plans/` | Plan 产出（PLAN-xxx）→ Dev agent 消费 | Planner → Dev agents |
| `docs/sdd/` | Historical SDDs (archived, no longer maintained) | Reference only |
