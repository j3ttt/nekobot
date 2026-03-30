# Handoff Notes — Agent Broadcast Log

> **Protocol**: Every agent MUST read this file before starting work, and append an entry after completing work.
> This is the chronological broadcast channel for cross-agent coordination.
>
> **Format**: See CLAUDE.md "Multi-Agent Coordination Protocol" for the entry template.
>
> **Quick scan**: Search for `### Interface Changes` and `### Lock:` to find breaking changes and active locks.
>
> **Note**: Old entries (before 2026-03-19 16:30) archived at bottom. New agents only need to read from "Recent Entries" down.

---

## Recent Entries

---

## 2026-03-22 — Async Dialogue: Message Batching

- Recorder: claude-opus
- Module: gateway
- Context: 实现消息批量合并，解决用户快速连发消息时"回复旧消息"的延迟体感问题。

### Changes

- `nekobot/gateway/router.py`: 新增 `BATCH_WINDOW = 2.5` 常量
- `nekobot/gateway/router.py`: `__init__` 新增 `_batch_queues` 和 `_batch_tasks` 字典（per-session 批量状态）
- `nekobot/gateway/router.py`: `run()` 改为批量调度——消息入队后创建延迟处理 task，不再直接调用 `_handle_with_retry`
- `nekobot/gateway/router.py`: 新增 `_process_batch(session_key)` 方法——等待窗口后合并并处理
- `nekobot/gateway/router.py`: 新增 `_merge_batch(batch)` 静态方法——单条消息直接透传，多条合并为带 `[HH:MM]` 时间戳的组合消息，设置 `_batched` 元数据标记
- `nekobot/gateway/router.py`: `_handle()` 跳过已批量合并消息的时间戳注入（避免双重时间戳）

### Interface Changes

- `run()` 行为变更：消息不再立即处理，延迟 2.5s 后批量处理（外部接口不变）
- 无公共接口签名变更

### New Tests

- `tests/test_gateway.py`: 新增 6 个测试（`TestMessageBatching`）
  - `test_batch_merges_rapid_messages`: 3 条消息合并为 1 次 `_handle` 调用
  - `test_single_message_passes_after_window`: 单条消息延迟后正常处理
  - `test_different_sessions_batch_independently`: 不同 session 独立批量
  - `test_merge_batch_single`: 单条直接透传
  - `test_merge_batch_multiple`: 多条合并内容和 `_batched` 标记
  - `test_batched_skips_timestamp_injection`: 批量消息跳过时间戳注入
- 114 tests passing

---

## 2026-03-20 (session 2) — 实时推送重构 + PLAN-005 修补 + REQ-006 + Daily Digest Skill

- Recorder: claude-opus
- Module: gateway, cli, bootstrap, cron, config, docs, skills
- Context: 重构消息推送为即时模式，修复 thinking 标签和 memory_write 泄露，完成 PLAN-005 补丁，实现 REQ-006 skills 可见化，创建 daily-digest skill。

### Changes

**Gateway 实时推送重构**
- `nekobot/gateway/router.py`: `_query_claude` 重写为即时推送模式——每条 `AssistantMessage` 到达立即通过 bus 推送，不再攒到最后批量发送。返回 `None`，外层 `run()` 不再重复发送
- `nekobot/gateway/router.py`: memory 提取改为每条消息推送前执行（修复 `<memory_write>` 标签泄露到用户的 bug）
- `nekobot/gateway/router.py`: thinking 标签从 `<thinking>` 改为 `[thinking]`（方括号不会被 HTML/Markdown 渲染器吞掉）
- `nekobot/cli.py`: agent 模式 `_process()` 改为从 `bus.outbound` 队列 drain 所有回复消息

**PLAN-005 补丁**
- `nekobot/config/schema.py`: `state_ws_port` 默认值 `9100` → `0`（默认不启动 WebSocket）
- `nekobot/gateway/state.py`: 删除死代码 `_PRIORITY` dict
- `nekobot/cron/service.py`: `_fire()` 中 state emit session key 从 `job.id` → `f"cron:{job.id}"`（与 Gateway 的 session key 一致）
- `data/config.example.yaml`: 新增 `state_ws_port` / `state_ws_host` 注释示例

**REQ-006: Skills/Commands 目录可见化**
- `nekobot/bootstrap.py`: skills/commands 改为可见的真实目录（`workspace/skills/`, `workspace/commands/`），`.claude/skills` 和 `.claude/commands` 创建为 symlink 指向 `../skills` 和 `../commands`
- 兼容迁移：已有真实目录自动迁移内容后替换为 symlink

**Daily Digest Skill**（部署到 `~/.nekobot/workspace/skills/daily-digest/`，不在项目代码中）
- `SKILL.md`: 按 writing-skills 规范编写，CSO 优化的 frontmatter
- `radar.py`: RSS + GitHub 双类型抓取，去重，JSON 输出（基于 nanobot info-radar）
- `feeds.json`: 复用 nanobot 订阅源配置

**文档**
- `CLAUDE.md`: 新增 Session Data Storage 段（两层持久化 + 迁移说明）；cron 模块加入 Module Boundaries；`build_mcp_servers` 签名更新；Done 列表更新
- 各 REQ/PLAN README 状态同步

### Interface Changes

- `_query_claude()` 返回值语义变更：不再返回最终文本，所有消息通过 bus 即时推送，返回 `None`
- `_init_gateway()` 返回值：5-tuple → 6-tuple（新增 `state_emitter`）
- `bootstrap._DIRS`: `workspace/.claude/skills` → `workspace/skills`（真实目录），`.claude/skills` 改为 symlink
- 新增 `bootstrap._SYMLINKS` 和 `_ensure_symlink()`

### New Tests

- `tests/test_bootstrap.py`: 新增 3 个测试（symlink 创建、symlink 解析、旧目录迁移）
- 95 tests passing

---

## TODO — 待实现改进

### ~~Async Dialogue（消息阻塞问题）~~ ✅ 已实现 (2026-03-22)

- 实现为 `BATCH_WINDOW = 2.5s` 的 per-session 消息批量合并，见上方 handoff 条目

### ~~Circuit Breaker（熔断机制）~~ ✅ 已实现 (c1de708)

- `CircuitBreaker` class in `gateway/router.py`，3 次连续失败 → open，60s 后 half-open 探测

### 并发测试方案

- **状态**: 已有基础覆盖（`TestGatewayConcurrency` + `TestMessageBatching`），E2E 集成测试待实现

---

## 2026-03-19 16:30 CST

- Recorder: JETTT
- Module: docs (project-wide)
- Context: 建立多 Agent 并发开发的文档体系，支持 Planner → Dev → Reviewer 工作流。

### Changes

- `CLAUDE.md` (new): 中央 Agent 契约文件（模块边界、接口契约、协调协议）
- `HANDOFF.md` (modified): 增加头部广播协议说明
- `README.md` (rewritten): 更新至当前状态
- `docs/plan.md` → `docs/architecture.md` (renamed)
- `docs/requirements/README.md` (new): 需求收纳目录
- `docs/plans/README.md` (new): Plan 产出目录

### Interface Changes

无代码接口变更，仅文档结构调整。

---

## 2026-03-19 — PLAN-001: Agent Skills + Slash Commands

- Recorder: JETTT
- Module: gateway, bootstrap, data
- Context: 启用 Claude Code 原生 Agent Skills 和 Slash Commands 支持。

### Changes

- `nekobot/gateway/router.py`: `setting_sources` 从 `["project"]` → `["user", "project"]`
- `nekobot/bootstrap.py`: `_DIRS` 新增 `workspace/.claude`, `workspace/.claude/skills`, `workspace/.claude/commands`；`_SEED_FILES` 新增 3 个种子文件
- `data/defaults/workspace/.claude/skills/nekobot-memory/SKILL.md` (new)
- `data/defaults/workspace/.claude/commands/usage.md` (new)
- `data/defaults/workspace/.claude/commands/skills.md` (new)

### Interface Changes

- `_build_options()` 中 `setting_sources`: `["project"]` → `["user", "project"]`

### 验证结论

- `preset: claude_code` 已包含 Skill 工具，不需要额外 `allowed_tools`
- `system_prompt` 完全替换模式下，SDK 仍会独立注入 Skill 描述信息
- Skills 在新 client 创建时发现，文件修改后不需要重启进程

---

## 2026-03-20 — DingTalk 修复 + Gateway 改进 + PLAN-002 实现 + REQ-004

- Recorder: claude-opus
- Module: channels/dingtalk, gateway, cron (new), cli, bootstrap
- Context: 修复 DingTalk 路由问题，改进 Gateway（流式回复、时间戳注入、Ctrl+C 退出），实现定时任务模块。

### Changes

**DingTalk 修复**
- `nekobot/channels/dingtalk.py`: 移除 `"group:"` 前缀，统一 chat_id 为原始值；`_send_payload` 改为接收 `OutboundMessage` 而非 `raw_chat_id`
- `nekobot/gateway/router.py`: OutboundMessage 传递 `metadata=msg.metadata`（群消息路由依赖 metadata）

**Gateway 改进**
- `nekobot/gateway/router.py`: `_query_claude` 重写为流式回复，工具链中间结果即时推送
- `nekobot/gateway/router.py`: `_handle()` 中注入消息时间戳 `[YYYY-MM-DD HH:MM]` 前缀 (REQ-004)
- `nekobot/cli.py`: Ctrl+C 退出修复，替换 `loop.add_signal_handler` 为 `try/except KeyboardInterrupt`

**PLAN-002: 定时任务模块**
- `nekobot/cron/types.py` (new): `CronSchedule`, `CronJob` 数据类
- `nekobot/cron/store.py` (new): `CronStore` JSON 文件持久化
- `nekobot/cron/service.py` (new): `CronService` asyncio 定时器 + 30s watchdog
- `nekobot/gateway/tools.py`: 新增 `nekobot-cron` MCP server，`schedule_task` tool
- `nekobot/cli.py`: `_init_gateway` 返回 5-tuple，集成 CronService
- `pyproject.toml`: 添加 `croniter>=1.0`

**PLAN-001: Skills**
- `nekobot/gateway/router.py`: `setting_sources` → `["user", "project"]`
- `nekobot/bootstrap.py`: 新增 workspace/.claude 目录结构
- `data/defaults/workspace/.claude/` 下新增示例 skills/commands

**测试**
- `tests/test_dingtalk.py`: 更新群消息测试用例
- `tests/test_cron.py` (new): 12 个测试（序列化、Store CRUD、调度计算、消息发布）

### Interface Changes

- `build_mcp_servers(memory, bus)` → `build_mcp_servers(memory, bus, cron_service=None)`
- `_init_gateway()` 返回值: 3-tuple → 5-tuple `(config, bus, gw, ping, cron_service)`
- DingTalk `_send_payload(raw_chat_id, ...)` → `_send_payload(msg: OutboundMessage, ...)`

---

## 2026-03-20 — 文档体系精简：废弃 SDD，统一 REQ → PLAN 流程

- Recorder: claude-opus
- Module: docs
- Context: SDD 与 PLAN 功能重叠，浪费 agent context。归档 SDD，统一三层文档链路。

### 决策

- **不再新建 SDD**，PLAN 是给 Dev agent 的唯一设计输入
- 文档链路：`REQ → PLAN → Dev agent 实现`
- 已有 SDD-01~09 保留做历史参考，不要求 agent 读

---

## 2026-03-20 — PLAN-002 approved + PLAN-003 + REQ-004

- Recorder: claude-opus
- Module: docs
- Context: 三个新需求/计划的状态汇总。

### PLAN-002: 定时任务模块 (approved)

- `docs/plans/PLAN-002-scheduled-tasks.md`: status → approved
- 新模块 `nekobot/cron/` — types.py / store.py / service.py
- MCP tool `schedule_task`（add/list/remove/enable/disable）
- 新依赖：`croniter`
- 6 个 Task，详见 PLAN-002

### PLAN-003: Daily Digest Skill (draft)

- `docs/plans/PLAN-003-daily-digest.md`: 自包含 Skill 实现日报
- 不新增 nekobot 模块，打包为 `daily-digest/` Skill 目录（SKILL.md + fetch.py + sources.yaml）
- Python 抓取数据 + Claude 总结，配合 Cron 定时推送
- 5 个 Task，详见 PLAN-003

### REQ-004: 消息时间戳注入 (planned)

- `docs/requirements/REQ-004-message-timestamp.md`
- router.py 的 `_handle()` 中在 content 前拼 `[YYYY-MM-DD HH:MM]`
- 改动量小，无需独立 Plan，Dev agent 可顺手实现

### Interface Changes

- PLAN-002: `build_mcp_servers(memory, bus)` → `build_mcp_servers(memory, bus, cron_service=None)`
- REQ-004: 无接口变更

---

## Archive (before 2026-03-19 16:30)

<details>
<summary>点击展开历史条目</summary>

## 2026-03-18 22:30:37 CST

- Recorder: codex
- Context: Initial deep repository research and implementation-vs-design review.

### Open Deviations (all resolved)

1. **allow_from**: Fixed in `1bca214` — `["*"] = allow all, [] = deny all`
2. **setting_sources**: Resolved by `claude-agent-sdk` migration — `["project"]` now wired
3. **max_budget_usd**: Resolved by migration — now passed through in `_build_options()`
4. **sender_id format**: Fixed in `1bca214` — `is_allowed()` matches both `id|username` and bare `id`

## 2026-03-18 23:15:32 CST

- Recorder: codex
- Context: Runtime investigation — SDK failure-path observability is weak. Original API errors (e.g. 402 quota_exceeded) get masked by transport/control errors. Gateway needs its own error handling layer.

## 2026-03-19 00:58:44 CST

- Recorder: codex
- Context: Migration from `claude-code-sdk` to `claude-agent-sdk`. Dependency changed in pyproject.toml. Imports migrated. 29 tests passed.

## 2026-03-19 10:40 CST

- Recorder: claude
- Context: Migrated gateway from `query()` to `ClaudeSDKClient`, added typed error handling + stderr capture.

### Key Changes
- Persistent `ClaudeSDKClient` per session (replaces per-message `query()`)
- Typed SDK error handling: `CLINotFoundError` (fatal) / `CLIConnectionError` / `ProcessError`
- stderr callback captures per-session CLI output for error extraction
- sessions.json format: `{"key": "id"}` → `{"key": {"id": "...", "last_error": "..."}}`

## 2026-03-19 (session 2) — CLI interactive mode + SDD-08 + SDD-09

- Recorder: claude-opus
- Context: CLI upgrade (rich + prompt_toolkit), bootstrap system, Typer CLI subcommands.

### Interface Changes
- `PromptBuilder(template_path)` → `PromptBuilder(prompts_dir)`
- `GatewayConfig.system_prompt_path` → `GatewayConfig.prompts_dir`
- `load_config(path="config.yaml")` → `load_config(path=None)` with search order
- New CLI: `nekobot gateway`, `nekobot agent`

</details>
