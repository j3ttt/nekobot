# NekoBot — 架构设计

> Claude Code as personal AI assistant: personality + long-term memory + IM gateway.

## 1. 设计原则

- **Claude Code-native**: 直接使用 Claude Agent SDK 作为后端，继承 Claude Code 的全部能力（文件操作、代码执行、Bash、WebSearch、deep thinking、subagent 调度、自动模型切换）。
- **薄网关**: IM 层只做消息收发和格式转换，不参与 LLM 逻辑。
- **人格化**: 通过分层 prompt 文件（SOUL.md / USER.md / AGENTS.md）注入人格 + 记忆 + 能力说明，完全替换 Claude Code 默认 prompt。用户在 `~/.nekobot/prompts/` 手工维护。
- **记忆外置**: 长期记忆独立于 Claude session，由网关层管理，通过 MCP tool 和 system prompt 注入。
- **永续对话**: 每个 channel:chat_id 映射到一个 Claude session_id，使用 `resume` 延续对话。
- **共享记忆，独立 session**: 多 channel 共用同一份长期记忆，但各自维护独立的 Claude session。

---

## 2. 为什么用 Claude Agent SDK

### 2.1 核心发现

Claude Agent SDK (`pip install claude-agent-sdk`) 是 Claude Code 的官方 Python 编程接口。它不是简化版——它就是 Claude Code 本身，只是通过 Python API 而非交互式终端来调用。

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for message in query(
    prompt="帮我分析一下这个项目的架构",
    options=ClaudeAgentOptions(
        system_prompt=load_system_prompt(),  # 完全自定义 prompt
        permission_mode="bypassPermissions",
        cwd="/path/to/workspace",
    ),
):
    print(message)
```

### 2.2 关键能力对比

| 能力 | Anthropic SDK (raw API) | Claude Agent SDK | 本项目需要 |
|------|------------------------|-----------------|-----------|
| 文件读写/编辑 | 需自己实现 tool | **内置** (Read/Write/Edit) | ✅ |
| Bash 执行 | 需自己实现 + 沙箱 | **内置** (Bash, 沙箱可关) | ✅ |
| 代码搜索 | 需自己实现 | **内置** (Glob/Grep) | ✅ |
| Web 搜索 | 需自己实现或用 server tool | **内置** (WebSearch/WebFetch) | ✅ |
| Deep Thinking | 手动开启 extended thinking | **自动管理** | ✅ |
| Prompt Caching | 手动标记 cache_control | **自动管理** | ✅ |
| 模型切换 | 手动指定 | **自动** haiku/sonnet/opus | ✅ |
| Agentic Loop | 自己写 (~50行) | **内置**, 含 subagent | ✅ |
| Session 持久化 | 自己管理 messages list | **内置** session_id + resume | ✅ |
| 自定义 Tool | 自己定义 + 执行循环 | **MCP tool** (`@tool` 装饰器) | ✅ |
| System Prompt | 完全自由 | 完全替换 / preset / preset+append | ✅ |
| Hooks | 无 | PreToolUse/PostToolUse/Stop | ✅ (记忆提取) |
| CLAUDE.md | 无 | 自动加载 (setting_sources) | ✅ |

### 2.3 System Prompt 策略

Claude Agent SDK 的 `system_prompt` 支持三种模式：

```python
# 1. 完全替换 ✅（选定方案）
system_prompt = "You are my-bot..."

# 2. 使用 Claude Code 默认 prompt
system_prompt = {"type": "preset", "preset": "claude_code"}

# 3. 在 Claude Code 默认 prompt 后追加
system_prompt = {
    "type": "preset",
    "preset": "claude_code",
    "append": "...",
}
```

**选择模式 1（完全自定义）**：用户在 `~/.nekobot/prompts/` 下维护分层 prompt 文件（SOUL.md / USER.md / AGENTS.md），由 PromptBuilder 拼装后完全替换 Claude Code 默认 prompt。

**优点**：
- 完全控制 prompt 的每一个字，没有不可见的默认行为
- 可以精确裁剪不需要的指令（如 Claude Code 默认的 professional objectivity、过于正式的语气）
- 人格融合更自然，不是在"追加人设"而是"就是这个人设"

**代价**：
- 需要手动维护工具使用指南（Claude Code 更新新工具时不会自动同步到你的 prompt）
- 初始工作量较大（需要参考 Claude Code 默认 prompt 结构写一份自己的）
- Claude Code 更新行为规范时不会自动继承

> **注意**: Agent SDK 的工具**参数定义**（JSON schema）是自动注入的，不需要在 system prompt 中描述。你的 prompt 只需要写**行为指南**——什么时候用什么工具、怎么用、注意事项。

> **CLAUDE.md 仍然有效**: `setting_sources=["project"]` 会加载 workspace 下的 CLAUDE.md，它会被追加到你的 system prompt 之后。可以用来放项目级规则。

### 2.4 之前设计的问题

上一版 plan 假设"个人助手不需要 Claude Code 的大部分工具"，因此选了 raw Anthropic SDK。这是错误的。用户明确表示想要 Claude Code 的全部能力——文件操作、代码执行、深度思考、Web 搜索——再加上人格和记忆。

Agent SDK 让这变成可能，而且工程量更小：

| 对比 | Raw SDK 方案 | Agent SDK 方案 |
|------|------------|---------------|
| Agentic loop | 自己写 ~50 行 | 内置 |
| Tool 实现 | web_search 外全部自己写 | 内置 ~15 个 |
| Session 管理 | 自己管理 messages + JSONL | 内置 session + resume |
| Prompt cache | 手动标记 4 个 breakpoint | 自动管理 |
| 代码量 | backend ~150 行 + gateway ~300 行 | gateway ~100 行 |

---

## 3. 整体架构

```
┌─────────────────────────────────────────────────┐
│              IM Channels (复用 nanobot)            │
│  Telegram  │  Discord  │  DingTalk  │  ...       │
└──────────────────┬──────────────────────────────┘
                   │ InboundMessage / OutboundMessage
                   ▼
┌──────────────────────────────────────────────────┐
│                  MessageBus                       │
│            (asyncio.Queue 双向队列)                │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────┐
│                    Gateway (核心协调层)                        │
│                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Session      │  │ Memory       │  │ Media Handler      │  │
│  │ Router       │  │ Manager      │  │ (图片/语音)         │  │
│  │              │  │              │  │                      │  │
│  │ channel:cid  │  │ read: 注入   │  │ 语音 → 文字          │  │
│  │ → session_id │  │ write: 提取  │  │ 图片 → base64       │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬─────────────┘  │
│         │                 │                  │                 │
│         ▼                 ▼                  ▼                 │
│  ┌───────────────────────────────────────────────────────┐    │
│  │           ClaudeSDKClient / query()                    │    │
│  │                                                         │    │
│  │  system_prompt: 自定义 prompt (人格+记忆+工具指南)       │    │
│  │  resume: session_id (永续对话)                           │    │
│  │  permission_mode: bypassPermissions                      │    │
│  │  mcp_servers: {memory: recall_memory, message: ...}     │    │
│  │  hooks: {Stop: memory_extract}                          │    │
│  │  hooks: {Stop: memory, PreCompact: archive}              │    │
│  └───────────────────────────────────────────────────────┘    │
│                                                                │
│  ┌──────────────────┐  ┌────────────────────────────────┐    │
│  │ Usage Tracker     │  │ Curiosity Ping Timer           │    │
│  │ (from ResultMsg)  │  │ (2-8h random)                  │    │
│  └──────────────────┘  └────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────┐
│         Claude Code (via Agent SDK)               │
│                                                    │
│  全部内置工具:                                      │
│    Read, Write, Edit, Bash, Glob, Grep,           │
│    WebSearch, WebFetch, AskUserQuestion            │
│  + 自动 prompt caching                             │
│  + 自动 model routing (haiku/sonnet/opus)         │
│  + 内置 agentic loop + subagent 调度               │
│  + session 持久化 + resume                         │
│  + sandbox (macOS/Linux)                           │
└──────────────────────────────────────────────────┘
```

---

## 4. Gateway 核心逻辑

不再需要自定义 Backend Interface。Claude Agent SDK 的 `query()` 和 `ClaudeSDKClient` 就是 backend。

### 4.1 消息处理流程

```python
from claude_agent_sdk import query, ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage

class Gateway:
    async def handle_inbound(self, msg: InboundMessage) -> str:
        """收到 IM 消息 → 调用 Claude → 返回回复文本"""

        # 1. 加载自定义 system prompt（人格 + 记忆 + 工具指南）
        system_prompt = self.prompt_builder.build(msg.channel, msg.chat_id)

        # 2. 构建 options
        session_id = self.sessions.get_session_id(msg.channel, msg.chat_id)
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,       # 完全自定义 prompt
            resume=session_id,                # 永续对话
            # 不传 allowed_tools → 由 permission_mode 统一管理
            # Claude Code 新增工具时自动可用，不需要维护列表
            permission_mode="bypassPermissions",
            mcp_servers={
                "memory": self.memory_mcp_server,   # recall_memory tool
                "im": self.im_mcp_server,           # send_message tool
            },
            hooks={
                "Stop": [HookMatcher(hooks=[self.on_stop])],        # 记忆提取
                "PreCompact": [HookMatcher(hooks=[self.on_compact])], # 压缩前归档
            },
            cwd=str(self.workspace_path),
            setting_sources=["project"],       # 加载 CLAUDE.md
        )

        # 3. 调用 Claude
        response_text = ""
        async for message in query(prompt=msg.content, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
            elif isinstance(message, ResultMessage):
                # 提取 usage 统计
                self.usage.record(message)
                # 保存 session_id 映射
                self.sessions.save_session_id(
                    msg.channel, msg.chat_id, message.session_id
                )

        # 4. 提取 memory_write 标记（如果有）
        cleaned, facts = extract_memory_writes(response_text)
        if facts:
            await self.memory.write_facts(facts)

        return cleaned
```

### 4.2 使用 ClaudeSDKClient 的替代方案

如果需要更精细的控制（中间状态推送、打断等），可以用 `ClaudeSDKClient`：

```python
async with ClaudeSDKClient(options=options) as client:
    await client.query(msg.content)

    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            # 可以在这里实时推送 typing indicator
            for block in message.content:
                if isinstance(block, TextBlock):
                    await channel.send_typing_indicator()
        elif isinstance(message, ResultMessage):
            # 对话结束
            break

    # 如果用户发了新消息想打断
    # await client.interrupt()
```

### 4.3 关键设计决策

**不需要自己写 Agentic Loop**: Claude Agent SDK 内部处理 tool call → execute → continue 循环。所有内置工具（Read/Write/Edit/Bash/etc.）的执行都由 SDK 管理。

**不需要自己管理 messages list**: SDK 通过 `resume=session_id` 自动恢复完整对话历史。Gateway 只需要维护 `channel:chat_id → session_id` 的映射。

**不需要手动 prompt caching**: Claude Code 内部自动优化 cache breakpoints。

**思考过程**: SDK 的消息流中包含 `ThinkingBlock`，Gateway 根据配置 `forward_thinking`（默认 `true`）决定是否转发给用户。

---

## 5. 自定义 System Prompt

> **模式 1（完全替换）**：分层 prompt 文件，存放在 `~/.nekobot/prompts/`。
> PromptBuilder 按顺序加载 SOUL.md → USER.md → AGENTS.md，拼装后注入 Memory 和 Runtime。
> Claude Code 更新时**不会**自动同步 prompt 内容，需要手动更新 AGENTS.md。

### Prompt 分层结构

```
~/.nekobot/prompts/（用户手工维护，首次运行从 data/defaults/ 复制）

┌─────────────────────────────────────────────────────────────────┐
│ SOUL.md — 人设性格（极低频编辑）                                  │
│   身份定义、性格特征、行为准则、签名                                │
├─────────────────────────────────────────────────────────────────┤
│ USER.md — 用户信息（低频编辑）                                    │
│   用户名字、偏好、时区、语言                                       │
├─────────────────────────────────────────────────────────────────┤
│ AGENTS.md — 行为指令（中频编辑，Claude Code 更新时同步）           │
│   工具使用指南、记忆管理规则、<memory_write> 标注说明               │
│   注：工具参数 schema 由 SDK 自动注入，不需要在这里写               │
├─────────────────────────────────────────────────────────────────┤
│ ## Memory — Core（PromptBuilder 动态注入）                       │
│   从 core.json 生成                                              │
├─────────────────────────────────────────────────────────────────┤
│ ## Memory — Active（PromptBuilder 动态注入）                     │
│   从 active.json + journal.jsonl 生成                            │
├─────────────────────────────────────────────────────────────────┤
│ ## Runtime（PromptBuilder 动态注入）                              │
│   当前时间、Channel、Chat ID                                     │
└─────────────────────────────────────────────────────────────────┘
```

### Prompt 加载逻辑

PromptBuilder 从 `prompts_dir` 按顺序读取文件，拼装后注入动态段：

```python
class PromptBuilder:
    PROMPT_FILES = ["SOUL.md", "USER.md", "AGENTS.md"]

    def __init__(self, prompts_dir: Path, memory_store: MemoryStore):
        self._dir = prompts_dir
        self._memory = memory_store

    def build(self, channel: str, chat_id: str) -> str:
        """Load prompt files, append memory + runtime."""
        parts = []
        for filename in self.PROMPT_FILES:
            path = self._dir / filename
            if path.exists():
                parts.append(path.read_text().strip())

        parts.append(f"## Memory — Core\n\n{self._memory.render_core()}")
        parts.append(f"## Memory — Active\n\n{self._memory.render_active()}")

        runtime = f"- Time: {now()}\n- Channel: {channel}\n- Chat: {chat_id}"
        parts.append(f"## Runtime\n{runtime}")

        return "\n\n---\n\n".join(parts)
```

每次 `build()` 都重新读取文件，编辑后无需重启即生效。

### 默认模板内容

首次运行时从 `data/defaults/prompts/` 复制到 `~/.nekobot/prompts/`。

**SOUL.md**（人设性格）：
```markdown
# My Bot

You are my-bot, a personal AI assistant.

## Personality
- (customize your bot's personality here)

## Rules
- Be concise and direct
- Never fabricate information
- When unsure, say so
```

**USER.md**（用户信息）：
```markdown
# User

用户信息。由用户手动编辑或由记忆系统自动填充。

## 基本信息
- 名字：（你的名字）
- 时区：（你的时区）
- 语言：（首选语言）
```

**AGENTS.md**（工具使用 + 记忆规则）：
```markdown
# Agent Instructions

## 工具使用

你有完整的文件系统和代码执行能力。

### 文件操作
- Read / Write / Edit / Glob / Grep

### 代码执行
- Bash: git、npm、docker 等终端操作

### Web
- WebSearch / WebFetch

### 记忆工具
- recall_memory: 搜索归档长期记忆
- send_message: 通过其他 IM 渠道发消息

## 记忆管理

对话中出现值得记住的新信息时，在回复末尾标注：
<memory_write>
- category.key: value
</memory_write>
category: profile / preference / relationship → core
           project / todo / recent_event → active
           reference / learning / tech_detail → archive
```

> **关键区别**: AGENTS.md 的"工具使用"部分是**行为指南**（什么时候用、怎么用、注意什么），不是工具参数定义。参数 schema 由 Agent SDK 自动注入。

### CLAUDE.md 作为补充

workspace 下的 CLAUDE.md 通过 `setting_sources=["project"]` 被 Agent SDK 自动加载，追加到 system prompt 之后。

CLAUDE.md 适合放：
- 工作区路径约定
- 特定项目的编码规范
- diary.md 的更新规则
- 不适合放在 prompt 文件中的低频规则

### 维护策略

| 文件 | 内容 | 更新频率 | 方式 |
|------|------|---------|------|
| `SOUL.md` | 人设、性格、行为准则 | 极低 | 手动编辑 |
| `USER.md` | 用户名字、偏好 | 低 | 手动编辑 / 记忆系统填充 |
| `AGENTS.md` | 工具指南、记忆规则 | Claude Code 更新时 | 手动检查 changelog |
| Memory | core/active/journal | 每次对话 | PromptBuilder 自动注入 |
| Runtime | time/channel/chat_id | 每次对话 | PromptBuilder 自动注入 |
| CLAUDE.md | 项目级规则 | 按需 | 手动编辑 |

---

## 6. 长期记忆层设计

> 人设内容已迁移到 `~/.nekobot/prompts/SOUL.md`，不再放在 memory 目录。记忆层只存事实和知识。

### 6.1 存储模型：分层

```
~/.nekobot/memory/
├── core.json            # 核心事实（低频变化）
│                        #   categories: profile, preference, relationship
├── active.json          # 活跃上下文（中频变化）
│                        #   categories: project, todo, recent_event
├── archive/             # 归档知识（不注入 prompt，通过 MCP tool 或 Read/Glob 检索）
│   ├── learning/        #   学习笔记（每条一个 .md 文件）
│   ├── tech_detail/     #   技术细节
│   └── reference/       #   参考资料
├── journal.jsonl        # 对话摘要日志（append-only，最近 5 条注入 prompt）
└── diary.md             # 日记（由 bot 更新）
```

**注入方式**：

| 层级 | 注入方式 | 示例 |
|------|---------|------|
| `SOUL.md` | PromptBuilder 从 `~/.nekobot/prompts/` 加载（手动维护） | 人格、规则、形象 |
| `core.json` | PromptBuilder 动态注入 Memory — Core 段 | 用户姓名、偏好、猫 |
| `active.json` | PromptBuilder 动态注入 Memory — Active 段 | 当前项目、近期事件 |
| `archive/` | **不注入**，通过 `recall_memory` MCP tool 或 Read/Glob 直接浏览 | Bug Bounty 清单、RSSHub 配置 |
| `journal.jsonl` | 最近 5 条注入 Memory — Active 段 | 对话摘要 |

### 6.2 记忆写入

**方式 1 — LLM 自标注（主要路径）**:

AGENTS.md 中要求 Claude 在回复末尾标注：

```
<memory_write>
- profile.cat_health: 陆小凤最近食欲下降，需要关注
- project.nekobot: 完成架构设计，进入实现阶段
</memory_write>
```

Gateway 在收到最终回复后提取标记，根据 category 前缀分发：
- `profile.*`, `preference.*`, `relationship.*` → `core.json`
- `project.*`, `todo.*`, `recent_event.*` → `active.json`
- `reference.*`, `learning.*`, `tech_detail.*` → `archive/{category}/{key}.md`

**方式 2 — Stop Hook 归档（补充）**:

通过 Agent SDK 的 `Stop` hook，在每次对话结束时检查是否需要写入 journal：

```python
async def on_stop(input_data, tool_use_id, context):
    """Stop hook: 对话结束时更新 journal"""
    # 从 context 获取本次对话摘要
    # 追加到 journal.jsonl
    return {}
```

**方式 3 — 手动维护**:

`SOUL.md`（`~/.nekobot/prompts/`）和 `diary.md`（`~/.nekobot/memory/`）由用户或 bot 手动维护。

### 6.3 记忆检索（recall_memory MCP Tool）

通过 Agent SDK 的 `@tool` 装饰器定义自定义 MCP tool：

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool(
    "recall_memory",
    "Search archived long-term knowledge: learning notes, tech details, "
    "reference materials. Use when the user asks about something that might "
    "be in long-term memory but not in the active context.",
    {"query": str}
)
async def recall_memory(args):
    results = memory_store.search_archive(args["query"])
    return {"content": [{"type": "text", "text": format_results(results)}]}

memory_server = create_sdk_mcp_server(
    name="memory",
    tools=[recall_memory],
)
```

**检索策略（渐进式）**：
1. **初版**: keyword match + Claude 直接用 Read/Glob 浏览 `archive/` 目录（Claude Code 天然有文件能力，只需在 AGENTS.md 说明目录位置）
2. **V2**: 加 embedding search（ChromaDB / LanceDB），recall_memory 做 semantic search
3. **长期**: archive 目录结构本身就是索引，Claude 自行决定读什么文件。recall_memory 可能变得不必要

### 6.4 跨 Channel 消息发送（send_message MCP Tool）

```python
@tool(
    "send_message",
    "Send a message to a specific IM channel. Use this to proactively reach "
    "the user on a different platform or send scheduled messages.",
    {"channel": str, "chat_id": str, "content": str}
)
async def send_message(args):
    await message_bus.publish_outbound(OutboundMessage(
        channel=args["channel"],
        chat_id=args["chat_id"],
        content=args["content"],
    ))
    return {"content": [{"type": "text", "text": "Message sent."}]}
```

### 6.5 从 MEMORY.md 迁移映射

| MEMORY.md 中的内容 | 迁移到 | 理由 |
|-------------------|--------|------|
| User Information | `core.json` profile | 基本不变 |
| Preferences | `core.json` preference | 基本不变 |
| Bot 形象设定 | `~/.nekobot/prompts/SOUL.md` | 固定人设 |
| Known Issues / Lessons | `SOUL.md` 行为规则部分 | 硬性规则应始终可见 |
| 可用梗 / 口癖 | `SOUL.md` | 人格的一部分 |
| Host Machine | `core.json` profile | 低频参考 |
| Project Context | `active.json` project | 活跃项目 |
| Bug Bounty / Web3 学习清单 | `archive.json` learning | 按需检索 |
| RSSHub / DingTalk 技术细节 | `archive.json` tech_detail | 按需检索 |
| Info Radar Skill | `archive.json` reference | 按需检索 |
| 陆小凤 | `core.json` relationship | 重要关系 |
| 上下文架构调研 | 不迁移 | 已过时 |

---

## 7. Session 管理

### 永续对话模式

Claude Agent SDK 有内置的 session 管理。每个 `channel:chat_id` 映射到一个 Claude `session_id`。

```python
# Gateway 维护的映射（持久化为 JSON）
session_map = {
    "telegram:12345": "a1b2c3d4-...",    # Claude session UUID
    "discord:67890": "e5f6g7h8-...",
    "dingtalk:abcde": "i9j0k1l2-...",
}
```

**创建**: 第一次收到某 channel:chat_id 的消息时，不传 `resume`，SDK 自动创建新 session。从 `ResultMessage.session_id` 获取 ID 并保存。

**恢复**: 后续消息传 `resume=session_id`，SDK 自动恢复完整对话上下文。

**Session 存储**: Claude Code 自行管理 session 持久化（`~/.claude/projects/` 目录下的 JSONL 文件）。Gateway 不需要自己存对话历史。

### Context Window 管理

Claude Code 内部有**自动 context compaction**（等同于 `/compact` 命令）。当 session 接近 context limit 时自动压缩。这是 Claude Code 的内置行为，不需要我们干预。

我们额外做的是**记忆提取**：通过 `PreCompact` hook，在 compaction 发生前将重要 facts 归档到 memory store：

```python
hooks={
    "PreCompact": [HookMatcher(hooks=[self.on_pre_compact])],
}

async def on_pre_compact(input_data, tool_use_id, context):
    """Compaction 前提取记忆"""
    # 1. 从即将被压缩的对话中提取 facts → core.json / active.json
    # 2. 写摘要 → journal.jsonl
    return {}
```

这样即使 compaction 丢弃了对话细节，重要信息已经持久化到 memory store 中，下次对话通过 system prompt 注入。

**不主动开新 session**。让 Claude Code 自己管理 context window。分析长文档、开发任务等重度场景下，Claude Code 的自动 compaction 会处理。

### 多 Channel 共享

```
Telegram  session_id="a1b2..." ──┐
                                  ├── 共享 memory/ (core.json, active.json, archive.json)
Discord   session_id="e5f6..." ──┤
                                  ├── 各自独立的 Claude session
DingTalk  session_id="i9j0..." ──┘
```

### 关于 cwd (工作目录)

所有 session 共享同一个 `cwd`（workspace 目录）。这意味着 Claude 在 Telegram 对话中创建的文件，在 Discord 对话中也能看到。这是特性不是 bug —— 它们是同一个 bot，只是通过不同渠道交流。

---

## 8. Agent SDK 集成细节

### 8.1 Options 构建

每次收到 IM 消息时，构建 `ClaudeAgentOptions`：

```python
def build_options(self, channel: str, chat_id: str) -> ClaudeAgentOptions:
    session_id = self.session_map.get(f"{channel}:{chat_id}")
    system_prompt = self.prompt_builder.build(channel, chat_id)

    return ClaudeAgentOptions(
        # 完全自定义 system prompt（人格 + 工具指南 + 记忆）
        system_prompt=system_prompt,

        # 永续对话
        resume=session_id,  # None = 新 session

        # 自定义 MCP 工具
        mcp_servers={
            "memory": self.memory_mcp_server,    # recall_memory
            "im": self.im_mcp_server,            # send_message
        },

        # 全部放行，不维护 allowed_tools 列表
        # Claude Code 新增工具时自动可用
        permission_mode="bypassPermissions",

        # Hooks
        hooks={
            "Stop": [HookMatcher(hooks=[self.on_stop_hook])],
            "PreCompact": [HookMatcher(hooks=[self.on_pre_compact])],
        },

        # 工作目录
        cwd=str(self.workspace_path),

        # 加载 CLAUDE.md
        setting_sources=["project"],
    )
```

### 8.2 消息流处理

```python
async def process_message(self, msg: InboundMessage) -> str:
    options = self.build_options(msg.channel, msg.chat_id)
    response_parts = []

    async for message in query(prompt=msg.content, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_parts.append(block.text)
                # ThinkingBlock: 可选择转发或忽略
                # ToolUseBlock: SDK 自动处理，不需要干预

        elif isinstance(message, ResultMessage):
            # 保存 session mapping
            self.session_map[f"{msg.channel}:{msg.chat_id}"] = message.session_id

            # 记录 usage
            self.usage_tracker.record(
                session_id=message.session_id,
                cost_usd=message.total_cost_usd,
                usage=message.usage,
                num_turns=message.num_turns,
            )

    # 拼接回复并提取 memory_write
    full_response = "".join(response_parts)
    cleaned, facts = extract_memory_writes(full_response)
    if facts:
        await self.memory.write_facts(facts)

    return cleaned
```

### 8.3 Memory Write 提取

```python
import re

MEMORY_WRITE_RE = re.compile(r"<memory_write>\n(.*?)\n</memory_write>", re.DOTALL)

def extract_memory_writes(response: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Return (cleaned_response, [(category, key, value), ...])."""
    facts = []
    for match in MEMORY_WRITE_RE.finditer(response):
        for line in match.group(1).strip().split("\n"):
            line = line.strip().lstrip("- ")
            if ":" in line:
                full_key, value = line.split(":", 1)
                if "." in full_key:
                    category, key = full_key.split(".", 1)
                else:
                    category, key = "active", full_key
                facts.append((category.strip(), key.strip(), value.strip()))

    cleaned = MEMORY_WRITE_RE.sub("", response).strip()
    return cleaned, facts
```

### 8.4 不需要自己实现的东西

| 功能 | 为什么不需要 |
|------|------------|
| Agentic loop | SDK 内置，自动处理 tool call → execute → continue |
| Tool 执行 | 内置工具由 SDK 执行；自定义工具通过 MCP server callback |
| Prompt caching | Claude Code 自动管理 cache breakpoints |
| Model routing | Claude Code 自动根据任务复杂度切换 haiku/sonnet/opus |
| Session 持久化 | Claude Code 自动存储在 `~/.claude/projects/` |
| Sandbox | Claude Code 内置 Bash sandbox（可通过 bypassPermissions 关闭） |
| Subagent | Claude Code 内置 subagent 调度 |

### 8.5 扩展能力：Codex / Claude Code 作为 Tool

> 调研结论：**可以做到**，通过 MCP stdio server 包装外部 CLI。

**方案 A — MCP stdio server 包装（推荐）**：

创建一个 MCP server 进程，内部调用 `codex` 或 `claude` CLI，暴露为 MCP tool：

```python
@tool(
    "delegate_codex",
    "Delegate a coding task to OpenAI Codex agent. Use for tasks that "
    "benefit from a different model's perspective or parallel execution.",
    {"task": str, "cwd": str}
)
async def delegate_codex(args):
    proc = await asyncio.create_subprocess_exec(
        "codex", "--quiet", "--approval-mode", "full-auto",
        "-m", args["task"],
        cwd=args["cwd"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return {"content": [{"type": "text", "text": stdout.decode()}]}
```

**方案 B — Bash 直接调用**：

Claude Code 本身有 Bash 工具，可以直接 `bash("codex -p '...' ")`。不需要额外包装，但缺少结构化的输入输出。

**限制**：
- Claude Code **阻止递归调用自身**（检测嵌套 session 并拒绝交互式调用）
- 但 `claude -p "..." --output-format json` 非交互模式可能可行，需实测
- 已有开源项目 [claude-code-mcp](https://github.com/steipete/claude-code-mcp) 做了类似的事

**结论**：初版不需要。如果后续想要多模型协作（比如用 Codex 做特定编码任务），通过 MCP tool 或 Bash 调用即可。架构上已经预留了这个扩展点。

---

## 9. Usage Tracking

Agent SDK 的 `ResultMessage` 直接提供 cost 和 usage 信息，不需要自己算：

```python
@dataclass
class UsageRecord:
    timestamp: datetime
    session_id: str
    channel: str
    cost_usd: float | None           # ResultMessage.total_cost_usd
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    num_turns: int                    # ResultMessage.num_turns
    duration_ms: int                  # ResultMessage.duration_ms
```

存储为 JSONL（`data/usage.jsonl`），可以用 `/usage` 命令查看日/周/月统计。

---

## 10. Curiosity Ping（主动发消息）

保留 nanobot 的设计，简化实现：

- 触发条件：用户最后一条消息后随机 2-8h，且没有睡觉意图
- 实现：Gateway 内一个简单的 per-session timer
- 触发时：构造一条系统消息（"用户已经 {N} 小时没说话了，你可以主动发消息关心一下"），发给 Claude，Claude 生成一条符合人设的主动消息
- 单次触发，不循环
- 睡觉检测：从 Claude 的 response metadata 中提取（类似 nanobot 的 signals 机制，但更简单）

---

## 11. 项目结构

### 代码库（git repo）

```
nekobot/
├── pyproject.toml
├── nekobot/
│   ├── config/
│   │   ├── schema.py          # Pydantic 配置 (channels + memory + gateway)
│   │   └── loader.py          # config 搜索: ~/.nekobot/ → ./ → defaults
│   ├── channels/              # 从 nanobot 复制，最小改动
│   │   ├── base.py
│   │   ├── manager.py
│   │   ├── telegram.py
│   │   ├── discord.py
│   │   └── dingtalk.py
│   ├── bus/                   # 直接复用
│   │   ├── events.py
│   │   └── queue.py
│   ├── gateway/               # 核心网关层
│   │   ├── router.py          # 消息路由：InboundMessage → query() → OutboundMessage
│   │   ├── prompt.py          # 分层 prompt 加载 (SOUL/USER/AGENTS) + 注入 memory/runtime
│   │   ├── media.py           # 媒体处理 (语音转文字、图片)
│   │   ├── ping.py            # Curiosity Ping timer
│   │   └── tools.py           # MCP tool 定义 (recall_memory, send_message)
│   ├── memory/                # 长期记忆层
│   │   ├── store.py           # MemoryStore: 分层读写 (core/active/archive)
│   │   ├── extractor.py       # 从 response 提取 <memory_write>
│   │   └── search.py          # archive keyword search
│   ├── usage/
│   │   └── tracker.py         # Usage tracking (from ResultMessage)
│   ├── bootstrap.py           # 首次运行：创建 ~/.nekobot + 写入默认文件
│   └── main.py                # 入口
├── data/
│   └── defaults/              # 默认模板（随代码分发，bootstrap 时复制到 ~/.nekobot）
│       ├── config.yaml
│       └── prompts/
│           ├── SOUL.md
│           ├── USER.md
│           └── AGENTS.md
├── tests/
└── docs/
```

### 运行时目录（~/.nekobot，git 无关）

```
~/.nekobot/
├── config.yaml                # 主配置（含 bot token 等敏感信息）
├── prompts/                   # 分层 system prompt（用户手工维护）
│   ├── SOUL.md                #   人设性格（极低频编辑）
│   ├── USER.md                #   用户信息（低频编辑）
│   └── AGENTS.md              #   工具指南、记忆规则（中频编辑）
├── memory/                    # 长期记忆
│   ├── core.json              #   核心事实 (profile, preference, relationship)
│   ├── active.json            #   活跃上下文 (project, todo, recent_event)
│   ├── archive/               #   归档知识（每条一个 .md 文件）
│   │   ├── learning/
│   │   ├── tech_detail/
│   │   └── reference/
│   ├── journal.jsonl          #   对话摘要
│   └── diary.md               #   日记
├── data/                      # 运行时数据
│   ├── sessions.json          #   channel:chat_id → session_id 映射
│   └── usage.jsonl            #   使用量追踪
└── workspace/                 # Claude Code cwd + CLAUDE.md
```

**注意**：不再需要 `backend/` 目录和 `session/` 目录。
- Backend 由 Agent SDK (`query()`) 直接提供
- Session 持久化由 Claude Code 内部管理（`~/.claude/projects/`），我们只维护映射表
- 配置和 prompt 文件不在代码库中，首次运行由 `bootstrap.py` 从 `data/defaults/` 复制

---

## 12. 从 nanobot 迁移清单

### 直接复用（最小改动）
- [ ] `bus/events.py` — InboundMessage, OutboundMessage
- [ ] `bus/queue.py` — MessageBus
- [ ] `channels/base.py` — BaseChannel
- [ ] `channels/manager.py` — ChannelManager
- [ ] `channels/telegram.py` — TelegramChannel
- [ ] `channels/discord.py` — DiscordChannel
- [ ] `channels/dingtalk.py` — DingTalkChannel
- [ ] `config/schema.py` — 只保留 channels 部分 + 新增 gateway/memory 配置

### 内容迁移
- [ ] 编写 `data/defaults/prompts/SOUL.md`（人设性格）
- [ ] 编写 `data/defaults/prompts/USER.md`（用户信息模板）
- [ ] 编写 `data/defaults/prompts/AGENTS.md`（工具指南 + 记忆规则）
- [ ] `MEMORY.md` → 按 6.5 映射表拆分到 core.json / active.json / archive/
- [ ] `HISTORY.md` → `~/.nekobot/memory/journal.jsonl`
- [ ] `DIARY.md` → `~/.nekobot/memory/diary.md`

### 新写
- [ ] `gateway/router.py` — 消息路由：InboundMessage → query() → OutboundMessage
- [ ] `gateway/prompt.py` — System prompt 加载 + 动态注入 (memory + runtime)
- [ ] `gateway/tools.py` — MCP tool 定义 (recall_memory, send_message)
- [ ] `gateway/ping.py` — Curiosity Ping
- [ ] `gateway/media.py` — 媒体处理
- [ ] `memory/store.py` — 分层记忆读写
- [ ] `memory/extractor.py` — memory_write 提取 + 分发
- [ ] `memory/search.py` — archive keyword search
- [ ] `usage/tracker.py` — Usage tracking (from ResultMessage)
- [ ] `main.py` — 入口 + 启动编排

### 不再需要
- ~~`agent/loop.py`~~ — SDK 内置 agentic loop
- ~~`agent/context.py`~~ — 自定义 system prompt 替代
- ~~`agent/tools/*`~~ — SDK 内置全部工具 + MCP 自定义
- ~~`agent/skills.py`~~ — 不需要
- ~~`agent/subagent.py`~~ — SDK 内置
- ~~`agent/memory.py`~~ — memory/store.py 替代
- ~~`agent/memory_worker.py`~~ — Stop hook 替代
- ~~`providers/*`~~ — SDK 是 backend
- ~~`mcp/*`~~ — SDK 内置 MCP 支持
- ~~`session/manager.py`~~ — SDK 内置 session 管理，只需映射表

---

## 13. 配置示例

```yaml
# config.yaml
gateway:
  workspace: ~/.nekobot/workspace
  data_dir: ~/.nekobot/data               # 运行时数据目录
  system_prompt_path: data/system_prompt.md # 相对于源码目录（版本控制）
  memory_path: ~/.nekobot/data/memory/
  permission_mode: bypassPermissions  # assistant 拥有服务器全部权限
  model: null                         # null = Claude Code 自动选择
  forward_thinking: true              # 将 ThinkingBlock 转发给用户

channels:
  telegram:
    enabled: true
    token: "BOT_TOKEN"
    allow_from: ["*"]
    proxy: null
  discord:
    enabled: false
    token: "..."
  dingtalk:
    enabled: false
    client_id: "..."
    client_secret: "..."
```

**注意**: 不需要 API key 配置。Agent SDK 使用 Claude Code 的认证（`~/.claude/` 中的登录状态），不需要单独的 API key。

---

## 14. 已确定的决策

| 问题 | 决策 |
|------|------|
| Backend 选择 | **Claude Agent SDK** (`claude-agent-sdk`)，继承 Claude Code 全部能力 |
| 对话重置 | 永续对话，通过 `resume=session_id`。Claude Code 自动 compaction，PreCompact hook 做记忆归档 |
| Curiosity Ping | 保留，Gateway 内 timer 实现 |
| 多 channel 同一用户 | 共享记忆（memory/），独立 Claude session |
| Tool | 全部 Claude Code 内置工具 + MCP 自定义 (recall_memory, send_message) |
| Token 预算 | ResultMessage 提供 cost/usage，存 JSONL，支持 /usage 查询 |
| Voice | 后续设计，复用 nanobot transcription |
| System Prompt | **模式 1（完全替换）**：`data/system_prompt.md` 手工维护，含人格 + 工具指南 + 记忆模板。Gateway 动态注入 memory + runtime |
| Model Routing | Claude Code 自动管理 (haiku/sonnet/opus) |
| Prompt Caching | Claude Code 自动管理 |

---

## 15. 待验证 / 风险点

| 问题 | 说明 | 影响 |
|------|------|------|
| 自定义 system prompt 的 cache 行为 | 完全替换模式下，system prompt 变化（每次对话动态注入 memory）是否会导致 cache miss？ | 需要实测；如果 cache miss 严重，可以将变化频繁的部分移到 user message 而非 system prompt |
| session resume 跨重启 | Gateway 进程重启后，resume 旧 session 是否正常？session 文件是否会被自动清理？ | 需要测试 session 持久性 |
| 并发 session | 同时有多个 channel 对话时，多个 `query()` 并发调用是否安全？ | 需要测试并发行为 |
| system prompt 长度 | 自定义 prompt 含工具指南 + 记忆，体积比 append 模式更大（可能 3-5K tokens）。是否影响性能？ | 需要实测；可通过精简工具指南控制体积 |
| Claude Code 更新同步 | 自定义 prompt 不自动继承 Claude Code 的新工具说明和行为规范更新 | 需要定期关注 Claude Code changelog，手动更新 system_prompt.md |
| MCP tool 注册时机 | `create_sdk_mcp_server()` 返回的 server 是否在每次 `query()` 都需要重新创建？还是可以复用？ | 影响架构：是否可以将 MCP server 作为 singleton |
| permission_mode 安全性 | `acceptEdits` / `bypassPermissions` 在无人值守场景是否足够安全？ | IM 消息来自外部用户，需要评估风险 |
| 费用 | Agent SDK 调用与直接 API 调用的费用差异？是否有额外 markup？ | 需要确认定价 |
