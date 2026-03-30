# PLAN-001: Skill 加载

- Status: done
- TODO: 准备有用的默认 seed skills/commands 放到 `data/defaults/workspace/.claude/` 并加入 bootstrap `_SEED_FILES`
- Planner: claude-opus
- Date: 2026-03-19
- Requirement: REQ-001

## 概述

让 NekoBot 支持 Claude Code 原生的 Agent Skills 和 Slash Commands。主要是配置层面的变更（打通 SDK 参数），加上一个 bootstrap 步骤确保 skills 目录存在。

## 背景分析

当前 `_build_options()` 已有：
```python
"setting_sources": ["project"],
"cwd": str(self.config.workspace_resolved),  # ~/.nekobot/workspace
```

但缺少两件事：
1. `setting_sources` 没有包含 `"user"`（无法加载 `~/.claude/skills/`）
2. `allowed_tools` 没有显式传（SDK 默认可能不包含 `"Skill"`）
3. workspace 下没有 `.claude/skills/` 目录
// 验证点：需要确认 tools={"type": "preset", "preset": "claude_code"} 已经 include 了 Skill？还是需要显式加

## 设计决策

| 决策点 | 选项 | 选定 | 理由 |
|--------|------|------|------|
| Skill 存放位置 | A: `~/.nekobot/workspace/.claude/skills/` / B: `~/.nekobot/skills/` 自定义 | A | 利用 SDK 原生发现机制，零代码 |
| Command 存放位置 | A: `~/.nekobot/workspace/.claude/commands/` / B: 自定义 | A | 同上 |
| setting_sources | `["project"]` / `["user", "project"]` | `["user", "project"]` | 同时加载项目级和用户级 Skills |
| 种子 Skills | 不提供 / 提供几个默认的 | 提供 1-2 个示例 | 降低上手门槛 |
// 是否要让 NekoBot 的 prompt 文件（AGENTS.md）里也提及 Skills 的存在？让 Claude 知道有 Skills 可用？
// 还是说 SDK 的 setting_sources 加载后 Claude 自动就知道了？

## 接口变更

### config/schema.py

```python
class GatewayConfig(Base):
    # 新增
    skills_enabled: bool = True  # 是否启用 Skills
    # setting_sources 策略已由 router.py 硬编码，不需要新字段
```
// 考虑是否需要 skills_dir 配置项？还是直接走 SDK 默认的 .claude/skills/ 目录就行

### gateway/router.py — `_build_options()`

```python
# before
"setting_sources": ["project"],

# after
"setting_sources": ["user", "project"],
```

`allowed_tools` 不需要额外处理——当前用的 `tools={"type": "preset", "preset": "claude_code"}` 应该已包含 Skill 工具。
// 需要验证这一点。如果 preset 不含 Skill，就需要加：
// "allowed_tools": ["Skill", ...其他]
// 或者换成不传 allowed_tools 让 SDK 用默认的

### bootstrap.py

在 `ensure_home()` 中增加 workspace 下的 `.claude/skills/` 和 `.claude/commands/` 目录创建：

```python
_DIRS = [
    "prompts", "memory", "memory/archive", "data", "workspace",
    "workspace/.claude",           # NEW
    "workspace/.claude/skills",    # NEW
    "workspace/.claude/commands",  # NEW
]
```

## 任务分解

| # | Task | Module | Files | Depends On | Assignee |
|---|------|--------|-------|------------|----------|
| 1 | 验证 SDK Skill 加载行为 | — | 无代码 | — | |
| 2 | 修改 bootstrap 创建 skills 目录 | bootstrap | `bootstrap.py` | — | |
| 3 | 修改 Gateway options 启用 Skills | gateway | `router.py` | 1 | |
| 4 | 提供示例 Skill 和 Command | data | `data/defaults/workspace/.claude/skills/`, `data/defaults/workspace/.claude/commands/` | 2 | |
| 5 | 更新文档和测试 | docs, tests | `CLAUDE.md`, `README.md`, `test_bootstrap.py` | 2, 3, 4 | |

### Task 1: 验证 SDK Skill 加载行为

用 `nekobot agent` 做最小验证：

```bash
# 1. 手动创建一个测试 Skill
mkdir -p ~/.nekobot/workspace/.claude/skills/test-skill
cat > ~/.nekobot/workspace/.claude/skills/test-skill/SKILL.md << 'EOF'
---
name: test-greeting
description: Use when user says hello or asks for a greeting
---
# Test Greeting Skill
When the user greets you, respond with "Skill loaded successfully! 🐈‍⬛"
EOF

# 2. 临时修改 router.py 的 setting_sources 为 ["user", "project"]
# 3. 运行 nekobot agent -m "hello"
# 4. 观察是否触发 Skill
# 5. 验证 allowed_tools 是否需要显式包含 "Skill"
```

验证清单：
- [ ] `setting_sources=["user", "project"]` 是否能发现 `cwd/.claude/skills/` 下的 SKILL.md
- [ ] `tools={"type": "preset", "preset": "claude_code"}` 是否已包含 Skill 工具
- [ ] 如果不包含，测试添加 `allowed_tools=["Skill"]` 后是否生效
- [ ] init message 的 `slash_commands` 列表是否包含自定义 commands
- [ ] Skill 文件修改后，新的 client 是否能发现更新（不需要重启进程）

### Task 2: 修改 bootstrap

在 `nekobot/bootstrap.py` 的 `_DIRS` 列表中追加：

```python
_DIRS = [
    "prompts", "memory", "memory/archive", "data", "workspace",
    "workspace/.claude",
    "workspace/.claude/skills",
    "workspace/.claude/commands",
]
```

### Task 3: 修改 Gateway options

`nekobot/gateway/router.py` `_build_options()` 中：

```python
# 改 setting_sources
"setting_sources": ["user", "project"],
```

如果 Task 1 验证需要显式 `allowed_tools`，则额外添加。

### Task 4: 示例 Skill 和 Command

#### 示例 Skill: `data/defaults/workspace/.claude/skills/nekobot-memory/SKILL.md`

```markdown
---
name: nekobot-memory
description: Use when the user asks to recall, search, or find something from long-term memory, past conversations, or archived knowledge
---

# NekoBot Memory Recall

## When to Use

When the user asks about something that might be stored in long-term memory:
- "还记得...吗"
- "之前说过..."
- "上次提到的..."
- Past conversations, saved notes, archived knowledge

## What to Do

1. First check Memory — Core and Memory — Active sections in your context
2. If not found, use the `recall_memory` tool to search archived knowledge
3. If still not found, browse `~/.nekobot/memory/archive/` directly with Read/Glob
4. Report what you found or that the information isn't stored
```
// 这个 skill 会不会和已有的 recall_memory MCP tool 重复？
// 还是说 Skill 是行为指南，tool 是实际执行？

#### 示例 Command: `data/defaults/workspace/.claude/commands/usage.md`

```markdown
---
description: Show recent usage statistics
allowed-tools: [Read, Bash]
---

Read the usage log at ~/.nekobot/data/usage.jsonl and summarize:
- Total cost today
- Number of conversations
- Average cost per conversation
- Top channels by usage

Output in a compact table format.
```

Bootstrap 时 seed 到 `~/.nekobot/workspace/.claude/`（同样不覆盖已有文件）。

### Task 5: 文档和测试

- `CLAUDE.md`: Module Boundaries 表中添加 skills 说明
- `README.md`: 添加 Skills 段落说明如何创建自定义 Skill
- `test_bootstrap.py`: 验证 `.claude/skills/` 和 `.claude/commands/` 目录被创建
- `data/config.example.yaml`: 如果加了 `skills_enabled` 字段则更新

## 文件清单

### 新建
- `data/defaults/workspace/.claude/skills/nekobot-memory/SKILL.md` — 示例 Skill
- `data/defaults/workspace/.claude/commands/usage.md` — 示例 Command

### 修改
- `nekobot/bootstrap.py` — `_DIRS` 追加 `.claude/skills`, `.claude/commands`；`_SEED_FILES` 追加示例文件
- `nekobot/gateway/router.py` — `_build_options()` 中 `setting_sources` 改为 `["user", "project"]`
- `nekobot/config/schema.py` — 可选：新增 `skills_enabled` 字段
- `CLAUDE.md` — 更新模块边界和接口说明
- `README.md` — 添加 Skills 用法说明
- `tests/test_bootstrap.py` — 新增目录验证用例

### 删除
- 无

## 测试策略

- **单元测试**: `test_bootstrap.py` 验证目录和 seed 文件创建
- **手动验证**: Task 1 的验证清单（需要 Claude auth）
- 不需要 mock SDK 的 Skill 行为——这是 SDK 内部功能

## 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| `tools preset` 不含 Skill 工具 | Skills 不生效 | Task 1 先验证，必要时加 `allowed_tools` |
| `system_prompt` 完全替换模式下 Skill 描述不注入 | Claude 不知道有 Skills | 需验证 `setting_sources` 是否独立于 system_prompt 注入 Skill 信息 |
| Skill 目录在 workspace 下，用户可能不知道位置 | 上手难 | 示例 Skill + README 说明 |
| `setting_sources: ["user"]` 加载全局 `~/.claude/` 下的 settings 可能带来副作用 | 可能加载不想要的全局设置 | 如有问题可退回 `["project"]` only |
// 最大风险：我们用的是 system_prompt 完全替换模式（mode 1）
// SDK 在完全替换模式下，是否还会注入 Skill 的描述信息？
// 如果不会，Skill 就是废的——Claude 根本不知道有这些 Skills 存在
// 这是 Task 1 必须验证的核心问题
