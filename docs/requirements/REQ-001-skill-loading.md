# REQ-001: Skill 加载

- Status: done
- Author: JETTT
- Date: 2026-03-19
- Plan: PLAN-001-skill-loading

## 背景

NekoBot 通过 Claude Agent SDK 调用 Claude Code，但目前没有加载 Skill 的能力。Claude Code 原生支持 Agent Skills 系统（SKILL.md 文件），NekoBot 应该能利用这套机制，让 Claude 在对话中自动发现和调用预定义的 Skill。

## 目标

让 NekoBot 的 Gateway 能够加载和使用 Claude Code 的 Agent Skills，使 Claude 在处理用户消息时能自动调用相关 Skill。

## 约束

- 兼容 Claude Code 原生的 SKILL.md 格式，不发明新格式
- 不破坏现有的 PromptBuilder、Gateway、Session 管理
- Skill 文件应支持热加载（编辑后无需重启）

## 验收标准

- [ ] NekoBot 能从指定目录发现和加载 SKILL.md 文件
- [ ] Claude 在对话中能自动使用已加载的 Skill
- [ ] 用户可以通过 slash command 方式手动触发 Skill
- [ ] 新增/修改 Skill 文件后无需重启

## 调研结论

### Claude Code Agent Skills 机制

#### 核心概念

- **Skill** = 一个 `SKILL.md` 文件，包含 YAML frontmatter + Markdown 内容
- **Claude 自动判断何时使用**，基于 description 字段匹配用户意图
- **Skill 是文件系统产物**，SDK 没有编程式注册 API

#### SKILL.md 格式

```markdown
---
name: skill-identifier
description: Use when user asks about X, mentions Y, or discusses Z
version: 1.0.0
---

# Skill Title

## When to Use
When the user asks about X, Y, or Z.

## What to Do
Step-by-step instructions for Claude to follow...

## Common Mistakes
- Mistake 1: ...
```

`description` 字段是触发关键——Claude 根据它判断是否自动调用。

#### Slash Command 格式

```markdown
---
description: Short description shown in /help
argument-hint: [arg1] [arg2]
allowed-tools: [Bash, Read, Write]
model: haiku  # 可选：覆盖默认模型
---

## Task
What the command should do...

## Context
- Current status: !`git status`  # !` ` 语法会执行命令并内联输出
```

#### 目录结构

```
.claude/skills/          # 项目级 Skill（随 git 分发）
    my-skill/
        SKILL.md
~/.claude/skills/        # 用户级 Skill（个人全局）
    my-personal-skill/
        SKILL.md
.claude/commands/        # Slash Commands（用户手动 /command 触发）
    my-command.md
```

#### SDK 集成方式

```python
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(
    cwd="/path/to/project",              # 必须包含 .claude/skills/
    setting_sources=["user", "project"], # 从文件系统加载 Skill
    allowed_tools=["Skill", "Read", "Write", "Bash"],  # 必须包含 "Skill"
)

async for message in query(prompt="...", options=options):
    print(message)
```

**三个必要条件：**
1. `cwd` 指向包含 `.claude/skills/` 的目录
2. `setting_sources` 包含 `"user"` 和/或 `"project"`
3. `allowed_tools` 包含 `"Skill"`

#### 发现和加载流程

1. 启动时从 `cwd/.claude/skills/` 和 `~/.claude/skills/` 扫描
2. 读取每个 `SKILL.md` 的 frontmatter（元数据）
3. Skill 描述注入到模型上下文
4. 对话中 Claude 根据 description 自动判断是否调用
5. 触发时加载完整 Skill 内容

#### Slash Command 的 SDK 用法

```python
# 直接作为 prompt 发送
async for message in query(prompt="/refactor src/auth/login.py", options=options):
    if message.type == "assistant":
        print(message.message)

# 查看可用命令
async for message in query(prompt="Hello", options={"max_turns": 1}):
    if message.type == "system" and message.subtype == "init":
        print("Available commands:", message.slash_commands)
```

### 对 NekoBot 的影响

#### 方案 A：原生 SDK Skills（推荐）

直接利用 Claude Code 原生机制，不自己发明：

1. 在 `~/.nekobot/workspace/.claude/skills/` 放 SKILL.md
2. Gateway 的 `ClaudeAgentOptions` 加上 `setting_sources=["user", "project"]`
3. `allowed_tools` 加上 `"Skill"`
4. 完成。Claude 自动发现和使用。

**优点**：零代码，完全兼容 Claude Code 生态
**缺点**：Skill 目录耦合在 workspace 下

#### 方案 B：自定义 Skills 目录 + PromptBuilder 注入

1. 在 `~/.nekobot/skills/` 放 SKILL.md（独立于 workspace）
2. 写一个 SkillsLoader 扫描目录、解析 frontmatter
3. PromptBuilder 把 Skill 描述注入 system prompt
4. 用户发 `/command` 时，Gateway 识别并转发

**优点**：目录位置灵活，不依赖 `.claude/` 约定
**缺点**：需要自己实现发现/加载逻辑，且绕过了 SDK 原生 Skill 工具

#### 方案 C：混合（A + 少量 B）

1. 原生 SDK Skills 用于 Claude 自动调用（方案 A）
2. NekoBot 自己的 skills 目录用于 IM 特有的能力（如转发规则、定时任务模板）
3. PromptBuilder 注入 NekoBot 特有 Skill 的描述

## 参考

- Claude Agent SDK Skills 文档：https://platform.claude.com/docs/en/agent-sdk/skills
- Claude Agent SDK Slash Commands：https://platform.claude.com/docs/en/agent-sdk/slash-commands
- Agent Skills Overview：https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Agent Skills Best Practices：https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
