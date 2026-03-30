# PLAN-002: 定时任务 / Cron 模块

- Status: done
- Planner: claude-opus
- Date: 2026-03-19
- Requirement: REQ-002

## 概述

新增 `nekobot/cron/` 模块，提供三种调度（cron/interval/one-shot），通过 MCP tool 让 Claude 自主管理任务，触发时作为 prompt 送入 Gateway 执行。复用 nanobot 的核心设计，适配 nekobot 的 MessageBus + ClaudeSDKClient 架构。

## 从 nanobot 复用的设计

| nanobot 设计 | nekobot 适配 |
|-------------|-------------|
| `CronService` + asyncio timer + watchdog | 直接复用模式，适配 nekobot 路径 |
| `CronJob` / `CronSchedule` / `CronPayload` 数据结构 | 简化后复用 |
| `on_job` 回调 → `agent.process_direct()` | 改为发布 `InboundMessage` 到 MessageBus（与 CuriosityPing 一致） |
| `cron` agent tool | 改为 MCP tool（与 recall_memory / send_message 一致） |
| JSON 文件持久化 | 直接复用，存 `~/.nekobot/data/cron/jobs.json` |
| CLI `nanobot cron` 子命令 | Typer 子命令 `nekobot cron list/add/remove` |

## 设计决策

| 决策点 | 选项 | 选定 | 理由 |
|--------|------|------|------|
| 调度引擎 | A: asyncio timer + watchdog / B: APScheduler / C: 系统 crontab | A | 零依赖，与现有 asyncio 架构一致，nanobot 已验证 |
| 任务触发方式 | A: 发 InboundMessage 到 bus / B: 直接调 Gateway._handle | A | 与 CuriosityPing 模式一致，解耦 |
| Claude 管理接口 | A: MCP tool / B: 注入 system prompt 指令 | A | 与现有 recall_memory/send_message 一致 |
| 持久化 | A: JSON 文件 / B: SQLite | A | 简单，与 nanobot 一致，可人工编辑 |
| cron 解析库 | `croniter` | — | nanobot 已用，轻量，纯 Python |
| 与 CuriosityPing 关系 | A: 合并 / B: 共存 | B | Ping 是特化的单次空闲检测，Cron 是通用调度，职责不同 |

## 架构

```
                    ┌─────────────────────────────────┐
                    │         CronService              │
                    │                                   │
                    │  jobs.json ←→ [CronJob, ...]      │
                    │                                   │
                    │  Timer ──→ _fire(job) ──→ bus     │
                    │  Watchdog (30s) ──→ catch missed  │
                    └───────────────┬──────────────────┘
                                    │ InboundMessage
                                    │ (sender_id="cron", content=job.message)
                                    ▼
┌──────────┐     ┌──────────┐     ┌──────────┐
│ Channels │ ──→ │ Message  │ ──→ │ Gateway  │ ──→ Claude ──→ OutboundMessage
│          │     │   Bus    │     │          │
└──────────┘     └──────────┘     └──────────┘
                       ↑                              │
                       │                              │
                 CuriosityPing                   push to channel
                 (sender_id="system")            (job.channel:job.chat_id)
```

## 数据结构

### CronJob

```python
@dataclass
class CronJob:
    id: str                    # 8 字符 UUID
    name: str                  # 用户可读名称
    enabled: bool = True
    schedule: CronSchedule     # 调度配置
    message: str = ""          # 触发时作为 prompt 发给 Claude
    channel: str | None = None # 响应推送到哪个渠道
    chat_id: str | None = None # 推送给谁
    # 运行时状态
    next_run_ms: int = 0
    last_run_ms: int = 0
    last_status: str = ""      # "ok" | "error"
    last_error: str = ""
    created_ms: int = 0
    delete_after_run: bool = False  # 一次性任务执行后自动删除

@dataclass
class CronSchedule:
    kind: Literal["cron", "every", "at"]
    expr: str = ""             # cron 表达式，kind="cron" 时用
    every_seconds: int = 0     # 间隔秒数，kind="every" 时用
    at_ms: int = 0             # 时间戳毫秒，kind="at" 时用
    tz: str | None = None      # IANA 时区，kind="cron" 时可选
```

### 持久化格式

```json
// ~/.nekobot/data/cron/jobs.json
{
  "version": 1,
  "jobs": [
    {
      "id": "a1b2c3d4",
      "name": "早报",
      "enabled": true,
      "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "Asia/Shanghai"},
      "message": "生成今日早报：天气、新闻摘要、我的日程",
      "channel": "telegram",
      "chat_id": "12345",
      "next_run_ms": 1710900000000,
      "last_run_ms": 0,
      "last_status": "",
      "delete_after_run": false
    }
  ]
}
```

## MCP Tool: `schedule_task`

```python
@tool(
    "schedule_task",
    "Create, list, or remove scheduled tasks. Tasks execute as Claude prompts at scheduled times.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "remove", "enable", "disable"]},
            "name": {"type": "string", "description": "Task name (for add)"},
            "message": {"type": "string", "description": "Prompt to execute when task fires (for add)"},
            "cron_expr": {"type": "string", "description": "Cron expression, e.g. '0 9 * * *' (for add)"},
            "every_seconds": {"type": "integer", "description": "Repeat interval in seconds (for add)"},
            "at": {"type": "string", "description": "ISO datetime for one-shot, e.g. '2026-03-20T15:00' (for add)"},
            "tz": {"type": "string", "description": "IANA timezone, e.g. 'Asia/Shanghai' (for add with cron)"},
            "job_id": {"type": "string", "description": "Job ID (for remove/enable/disable)"},
        },
        "required": ["action"],
    },
)
```

上下文自动捕获：Claude 调用 tool 时，Gateway 知道当前的 `channel` 和 `chat_id`，自动填入 job。

## Slash Command: `/cron`

```markdown
# data/defaults/workspace/.claude/commands/cron.md
---
description: Manage scheduled tasks (list, add, remove)
argument-hint: [list|add|remove] [options]
allowed-tools: [Bash, Read]
---

List and manage cron jobs. Examples:
- `/cron list` — show all scheduled tasks
- `/cron add "morning report" --cron "0 8 * * *"` — daily 8am task
- `/cron remove abc123` — remove a task

Read ~/.nekobot/data/cron/jobs.json and display in a table.
For add/remove, use the schedule_task MCP tool.
```

## 任务分解

| # | Task | Module | Files | Depends On |
|---|------|--------|-------|------------|
| 1 | 数据结构 + 持久化 | cron | `cron/types.py`, `cron/store.py` | — |
| 2 | 调度引擎 (timer + watchdog) | cron | `cron/service.py` | 1 |
| 3 | MCP tool (`schedule_task`) | gateway | `gateway/tools.py` | 1, 2 |
| 4 | Gateway 集成 (消息触发 + 上下文传递) | gateway, cli | `cli.py` | 2, 3 |
| 5 | Slash command + 示例 | data | `data/defaults/workspace/.claude/commands/cron.md` | 3 |
| 6 | Bootstrap + 文档 + 测试 | bootstrap, docs, tests | `bootstrap.py`, `CLAUDE.md`, `test_cron.py` | 1-5 |

### Task 1: 数据结构 + 持久化

**新建 `nekobot/cron/types.py`** (~60 行)

```python
from dataclasses import dataclass, field, asdict
from typing import Literal
import json, uuid, time
from pathlib import Path

@dataclass
class CronSchedule:
    kind: Literal["cron", "every", "at"]
    expr: str = ""
    every_seconds: int = 0
    at_ms: int = 0
    tz: str | None = None

@dataclass
class CronJob:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    message: str = ""
    channel: str | None = None
    chat_id: str | None = None
    next_run_ms: int = 0
    last_run_ms: int = 0
    last_status: str = ""
    last_error: str = ""
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    delete_after_run: bool = False
```

**新建 `nekobot/cron/store.py`** (~80 行)

```python
class CronStore:
    """JSON file persistence for cron jobs."""
    def __init__(self, path: Path): ...
    def load(self) -> list[CronJob]: ...
    def save(self, jobs: list[CronJob]) -> None: ...
    def add(self, job: CronJob) -> None: ...
    def remove(self, job_id: str) -> bool: ...
    def get(self, job_id: str) -> CronJob | None: ...
    def update(self, job: CronJob) -> None: ...
```

### Task 2: 调度引擎

**新建 `nekobot/cron/service.py`** (~200 行)

```python
class CronService:
    """Asyncio-based task scheduler with timer + watchdog."""

    def __init__(self, store: CronStore, bus: MessageBus):
        self._store = store
        self._bus = bus
        self._timer_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Load jobs, compute next runs, start timer + watchdog."""
        jobs = self._store.load()
        for job in jobs:
            if job.enabled:
                self._compute_next_run(job)
        self._store.save(jobs)
        self._arm_timer()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def stop(self) -> None: ...

    def add_job(self, job: CronJob) -> None:
        """Add and arm."""
        self._compute_next_run(job)
        self._store.add(job)
        self._arm_timer()  # re-arm for new soonest job

    def remove_job(self, job_id: str) -> bool: ...
    def list_jobs(self) -> list[CronJob]: ...

    def _compute_next_run(self, job: CronJob) -> None:
        """Compute next_run_ms based on schedule kind."""
        match job.schedule.kind:
            case "cron":
                # croniter
            case "every":
                # now + every_seconds * 1000
            case "at":
                # at_ms directly

    def _arm_timer(self) -> None:
        """Cancel old timer, find soonest job, sleep until then."""

    async def _watchdog(self) -> None:
        """Every 30s, check for overdue jobs (handles system sleep)."""

    async def _fire(self, job: CronJob) -> None:
        """Publish synthetic InboundMessage to bus."""
        await self._bus.publish_inbound(
            InboundMessage(
                channel=job.channel or "cron",
                sender_id="cron",
                chat_id=job.chat_id or job.id,
                content=job.message,
                metadata={"is_cron": True, "cron_job_id": job.id},
                # 响应要发到 job.channel:job.chat_id
                session_key_override=f"cron:{job.id}",
            )
        )
```

关键设计：`_fire()` 发的是 `InboundMessage`，Gateway 当普通消息处理，Claude 执行 prompt，OutboundMessage 通过 `channel:chat_id` 路由回用户。

### Task 3: MCP tool

在 `nekobot/gateway/tools.py` 的 `build_mcp_servers()` 中新增 `schedule_task` tool，注册到一个新的 MCP server `nekobot-cron`。

Tool 需要知道"当前消息的 channel/chat_id"来自动填入 job。方案：Gateway 在调用 Claude 前把 `channel`/`chat_id` 存到一个 context 变量里，tool 从中读取。

### Task 4: Gateway 集成

在 `cli.py` 的 `_init_gateway()` 中：

```python
from nekobot.cron.store import CronStore
from nekobot.cron.service import CronService

cron_store = CronStore(gw_cfg.data_dir_resolved / "cron" / "jobs.json")
cron_service = CronService(cron_store, bus)

# MCP tool 需要 cron_service 引用
mcp_servers = build_mcp_servers(memory, bus, cron_service)  # 新增参数
```

在 `_run_gateway()` 中启动 CronService：
```python
await asyncio.gather(
    gw.run(),
    channel_mgr.start_all(),
    cron_service.start(),  # NEW
)
```

Gateway 路由中处理 cron 消息的响应路由：当 `msg.sender_id == "cron"` 时，响应的 OutboundMessage 需要用 job 的 `channel:chat_id` 而不是 `"cron:job_id"`。

### Task 5: Slash command

新建 `data/defaults/workspace/.claude/commands/cron.md`。

### Task 6: Bootstrap + 文档 + 测试

- `bootstrap.py`: `_DIRS` 新增 `data/cron`
- `CLAUDE.md`: Module Boundaries 新增 cron 模块
- `test_cron.py`: 数据结构、store CRUD、schedule 计算

## 文件清单

### 新建
- `nekobot/cron/__init__.py`
- `nekobot/cron/types.py` — CronJob, CronSchedule 数据结构
- `nekobot/cron/store.py` — JSON 持久化
- `nekobot/cron/service.py` — 调度引擎
- `data/defaults/workspace/.claude/commands/cron.md` — Slash command
- `tests/test_cron.py` — 单元测试

### 修改
- `nekobot/gateway/tools.py` — 新增 `schedule_task` MCP tool + `nekobot-cron` server
- `nekobot/cli.py` — `_init_gateway()` 创建 CronService，`_run_gateway()` 启动
- `nekobot/bootstrap.py` — `_DIRS` 新增 `data/cron`
- `CLAUDE.md` — 模块边界 + 接口契约新增 cron
- `pyproject.toml` — 新增 `croniter` 依赖

### 删除
- 无

## 接口变更

- `build_mcp_servers(memory, bus)` → `build_mcp_servers(memory, bus, cron_service=None)`
  - Impact: `cli.py` 调用处需要传入 cron_service
- Module Boundaries 新增：

```
| **cron** | `nekobot/cron/` | `types.py`, `store.py`, `service.py` | `CronService`, `CronStore`, `CronJob` |
```

## 测试策略

- `test_cron.py`:
  - CronJob / CronSchedule 序列化/反序列化
  - CronStore CRUD（tmp_path）
  - `_compute_next_run()` 各种 schedule kind
  - `_fire()` 发布 InboundMessage（mock bus）
- 手动验证：`nekobot agent` 中用 `schedule_task` tool 创建任务

## 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| Cron 消息的 OutboundMessage 路由 | 响应可能发到错误渠道 | `session_key_override` + Gateway 用 job 的 channel/chat_id |
| MCP tool 不知道当前 channel/chat_id | job 无法自动关联渠道 | Gateway 在上下文中传递，或 tool 要求显式指定 |
| 系统休眠导致错过任务 | 定时不准 | watchdog 30s 轮询补偿（nanobot 已验证） |
| `croniter` 依赖 | 新依赖 | 纯 Python，无 C 扩展，安装简单 |
| CuriosityPing 冲突 | Ping 和 Cron 都发 InboundMessage | sender_id 不同（"system" vs "cron"），互不影响 |

## 使用示例

```
用户: 每天早上 8 点给我发一份今日天气和日程摘要
Claude: 好的，我来创建一个定时任务。

[Claude 调用 schedule_task tool]
{
  "action": "add",
  "name": "早报",
  "message": "查询今天的天气和我的日程，生成简洁的早报摘要推送给用户",
  "cron_expr": "0 8 * * *",
  "tz": "Asia/Shanghai"
}

→ 每天 8:00，CronService 触发，将 message 作为 prompt 发给 Claude
→ Claude 执行（可能调用 WebSearch 查天气、读日历文件等）
→ 生成的回复推送到用户的 Telegram
```

```
用户: 3 小时后提醒我吃药
Claude: 收到。

[Claude 调用 schedule_task tool]
{
  "action": "add",
  "name": "吃药提醒",
  "message": "提醒用户：该吃药了！",
  "at": "2026-03-19T22:30:00"
}

→ 3 小时后触发，Claude 生成提醒，推送给用户
→ delete_after_run=True，执行后自动删除
```

```
用户: 帮我设置每天早晚各一次日报
Claude: 好的，我来创建两个定时任务。

[Claude 调用 schedule_task tool × 2]
{"action": "add", "name": "早报", "message": "生成今日早报，汇总各数据源最新内容", "cron_expr": "0 8 * * *", "tz": "Asia/Shanghai"}
{"action": "add", "name": "晚报", "message": "生成今日晚报，汇总下午以来的更新", "cron_expr": "0 20 * * *", "tz": "Asia/Shanghai"}

→ 8:00 / 20:00 触发，prompt 发给 Claude
→ Claude 执行 prompt，生成日报推送（可配合 daily-digest Skill，见 PLAN-003）
```
