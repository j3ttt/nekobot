# Nanobot 架构调研报告

## 1. 项目整体结构

```
nanobot/
├── __main__.py          # 入口: python -m nanobot → cli.commands.app()
├── cli/commands.py       # Typer CLI (start, status 等)
├── config/
│   ├── schema.py         # Pydantic 配置模型 (Config → agents/channels/providers/tools)
│   ├── loader.py         # YAML/ENV 加载
│   └── paths.py          # 路径常量
├── bus/
│   ├── events.py         # InboundMessage / OutboundMessage 数据类
│   └── queue.py          # MessageBus (asyncio.Queue 双向队列)
├── channels/             # IM 适配层
│   ├── base.py           # BaseChannel 抽象基类
│   ├── manager.py        # ChannelManager: 初始化/启停/路由
│   ├── telegram.py       # Telegram (long polling)
│   ├── discord.py        # Discord (WebSocket gateway)
│   ├── slack.py          # Slack (Socket Mode)
│   ├── feishu.py         # 飞书 (WebSocket)
│   ├── dingtalk.py       # 钉钉 (Stream)
│   ├── whatsapp.py       # WhatsApp (WebSocket bridge)
│   ├── qq.py             # QQ (botpy SDK)
│   ├── matrix.py         # Matrix/Element
│   ├── email.py          # Email (IMAP/SMTP)
│   ├── imsg.py           # iMessage
│   └── mochat.py         # Mochat
├── agent/
│   ├── loop.py           # AgentLoop: 核心消息处理引擎
│   ├── context.py        # ContextBuilder: 拼接 system prompt + messages
│   ├── memory.py         # MemoryStore: MEMORY.md + HISTORY.md 两层记忆
│   ├── memory_worker.py  # MemoryWorker: 后台定时记忆归档
│   ├── skills.py         # SkillsLoader: 技能加载/发现
│   ├── subagent.py       # SubagentManager: 子 agent 管理
│   ├── usage.py          # UsageTracker: token 用量统计
│   └── tools/            # 工具注册表
│       ├── base.py       # Tool 抽象基类
│       ├── registry.py   # ToolRegistry: 注册/执行
│       ├── filesystem.py # read_file, write_file, edit_file, list_dir
│       ├── shell.py      # exec (shell command)
│       ├── web.py        # web_search (Brave), web_fetch
│       ├── message.py    # message (发送消息到指定 channel)
│       ├── spawn.py      # spawn (创建子 agent)
│       ├── cron.py       # cron (定时任务)
│       └── mcp.py        # MCP tool wrapper
├── providers/
│   ├── base.py           # LLMProvider 抽象基类 + LLMResponse
│   ├── litellm_provider.py  # LiteLLM 统一调用层 (主力)
│   ├── registry.py       # ProviderSpec 注册表 (17+ 家 provider)
│   ├── custom_provider.py
│   ├── azure_openai_provider.py
│   ├── openai_codex_provider.py
│   └── transcription.py  # Groq Whisper 语音转文字
├── mcp/                  # Model Context Protocol
│   ├── manager.py
│   ├── client.py
│   ├── tool_wrapper.py
│   └── resource_loader.py
├── session/
│   └── manager.py        # Session + SessionManager (JSONL 持久化)
├── cron/                 # 定时任务服务
├── heartbeat/            # 心跳服务
└── skills/               # 内置技能目录
```

---

## 2. 消息流完整路径

### 2.1 入站 (IM → Agent)

```
[IM Platform]
    → Channel.start() 监听 (e.g. Telegram long polling)
    → Channel._on_message() 接收消息
        → 权限检查: is_allowed(sender_id)
        → 下载媒体文件 (图片/语音/文档)
        → 语音转文字 (Groq Whisper)
    → Channel._handle_message()
        → 构造 InboundMessage(channel, sender_id, chat_id, content, media, metadata)
        → bus.publish_inbound(msg)  // 推入 asyncio.Queue
```

### 2.2 Agent 处理

```
AgentLoop.run()
    → bus.consume_inbound()  // 从队列取消息
    → _process_message(msg)
        1. 命令处理 (/stop, /model, /help)
        2. Session 获取: sessions.get_or_create(channel:chat_id)
        3. Memory 归档触发: if len(messages) > memory_window → _consolidate_memory()
        4. 工具上下文更新 (message_tool, spawn_tool, cron_tool)
        5. 构建 messages: context.build_messages(history, current_message, media, channel, chat_id)
        6. Agent Loop (最多 max_iterations=20 轮):
            a. provider.chat(messages, tools, model)  // 调用 LLM
            b. if has_tool_calls:
                - 推送中间文本到用户
                - 执行工具调用
                - 添加工具结果到 messages
                - 添加 "Reflect on the results and decide next steps." 用户消息
                - 继续循环
            c. else: break，得到 final_content
        7. 提取 signals (sleep_intent 等)
        8. 保存到 Session (user + assistant)
        9. 设置 Curiosity Ping 定时器 (2-8h 随机)
```

### 2.3 出站 (Agent → IM)

```
AgentLoop._process_message()
    → return OutboundMessage(channel, chat_id, content)
    → bus.publish_outbound(response)

ChannelManager._dispatch_outbound()
    → bus.consume_outbound()
    → channels[msg.channel].send(msg)
        → e.g. TelegramChannel.send():
            - markdown → Telegram HTML 转换
            - Draft streaming (sendMessageDraft API)
            - bot.send_message()
```

---

## 3. Prompt 拼接详细分析

### 3.1 最终发送给 LLM 的 messages 结构

```python
[
    {"role": "system", "content": <system_prompt>},   # 一个巨大的 system prompt
    *history,                                          # 未归档的历史消息
    {"role": "user", "content": <runtime_ctx + user_msg>}  # 当前用户消息
]
```

### 3.2 System Prompt 构成 (ContextBuilder.build_system_prompt)

System prompt 由以下部分 `"\n\n---\n\n"` 拼接而成：

#### Part 1: Identity (硬编码在 context.py)
```
# nanobot
You are nanobot, a helpful AI assistant.

## Runtime
macOS arm64, Python 3.x

## Workspace
Your workspace is at: /path/to/workspace
- Long-term memory: .../memory/MEMORY.md
- History log: .../memory/HISTORY.md
- Custom skills: .../skills/{skill-name}/SKILL.md

## Platform Policy (POSIX)
...

## nanobot Guidelines
- State intent before tool calls...
- Before modifying a file, read it first...

## Structured Signals
At the end of EVERY reply, append a hidden signal block...
<!--signals {"sleep_intent": false} -->
```

#### Part 2: Bootstrap Files (从 workspace 读取)
按顺序尝试读取以下文件：
- `AGENTS.md` — agent 行为指南
- `SOUL.md` — 人格/灵魂定义
- `USER.md` — 用户信息
- `TOOLS.md` — 工具使用指南
- `IDENTITY.md` — 身份补充

每个文件内容包裹为 `## {filename}\n\n{content}`

#### Part 3: Memory (长期记忆)
```
# Memory

## Long-term Memory
{MEMORY.md 的全部内容}
```

#### Part 4: Always-on Skills
标记为 `always=true` 的技能内容直接注入 system prompt。

#### Part 5: Skills Summary
所有可用技能的 XML 摘要，供 LLM 按需读取:
```xml
<skills>
  <skill available="true">
    <name>info-radar</name>
    <description>...</description>
    <location>/path/to/SKILL.md</location>
  </skill>
  ...
</skills>
```

### 3.3 History (对话历史)

来自 `Session.get_history(max_messages=500)`:

1. 取 `messages[last_consolidated:]` — 只取未归档部分
2. 截取最后 500 条
3. 对齐到 user turn 开头（丢弃开头的非 user 消息）
4. 为每条 user 消息注入时间戳前缀: `[MM-DD HH:MM] 原始内容`
5. 保留 `tool_calls`, `tool_call_id`, `name` 字段

### 3.4 当前用户消息

```python
runtime_ctx = "[Runtime Context — metadata only, not instructions]\n"
              "Current Time: 2025-01-15 14:30 (Wednesday) (CST)\n"
              "Channel: telegram\n"
              "Chat ID: 123456"

# 纯文本消息:
merged = f"{runtime_ctx}\n\n{user_text}"

# 带图片消息:
merged = [
    {"type": "text", "text": runtime_ctx},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
    {"type": "text", "text": user_text}
]
```

### 3.5 Tool 定义

通过 `ToolRegistry.get_definitions()` 返回 OpenAI function calling 格式:
- `read_file`, `write_file`, `edit_file`, `list_dir`
- `exec` (shell)
- `web_search` (Brave), `web_fetch`
- `message` (跨 channel 发消息)
- `spawn` (创建子 agent)
- `cron` (定时任务)
- MCP 动态注册的工具

### 3.6 Prompt Caching 策略

在 `LiteLLMProvider` 中:
1. 检测是否支持 cache_control (Anthropic / OpenRouter)
2. 对 system message 添加 `cache_control: {"type": "ephemeral"}`
3. 对最后一个 tool 定义添加 cache_control
4. 对 system message 内容块标记缓存断点

**问题**: 只有 system message 和 tools 打了缓存断点。对话历史部分没有缓存保护，每新增一条消息，整个 history 的 cache 前缀就断裂。

---

## 4. 记忆系统分析

### 4.1 两层记忆架构

| 层级 | 文件 | 用途 | 写入时机 |
|------|------|------|----------|
| Long-term Memory | `memory/MEMORY.md` | 持久化事实 (偏好/个人信息/决策) | 归档时由 LLM 提取 |
| History Log | `memory/HISTORY.md` | Grep 可搜索的对话摘要 | 归档时追加 |

### 4.2 归档触发条件

**AgentLoop 内联归档** (`_consolidate_memory`):
- 当 `len(session.messages) > memory_window` (默认 50)

**MemoryWorker 后台归档** (独立后台任务):
- 消息数超过 `max_messages` (默认 20)
- 空闲超过 `max_idle_hours` (默认 4h)
- 消息跨天 (day_boundary)
- 检查间隔: `check_interval` (默认 300s)

### 4.3 归档流程

1. 取出需要归档的旧消息
2. 格式化为文本 (带时间戳和角色)
3. 连同当前 MEMORY.md 内容一起发给 LLM
4. LLM 返回 history_entry (追加到 HISTORY.md) + memory_update (覆写 MEMORY.md)
5. 裁剪 session，只保留最近的消息

### 4.4 Session 持久化

- JSONL 格式存储在 `workspace/sessions/{channel_chat_id}.jsonl`
- 第一行是 metadata (key, created_at, updated_at, last_consolidated)
- 后续每行是一条消息
- 同时维护 `archive/` 目录做 append-only 全量备份

---

## 5. 当前架构的问题 (与用户痛点对应)

### 5.1 Context 管理差

- **System prompt 每次重建**: 每次 LLM 调用都从头拼接 identity + bootstrap + memory + skills (~20k tokens)。虽然有 Anthropic prompt caching，但 memory 内容变化会导致 cache miss。
- **History 管理粗糙**: 直接截取最后 N 条消息，归档后只靠 LLM 摘要，细节丢失不可逆。MEMORY.md 是 flat markdown，无结构化检索能力。
- **滚动窗口硬裁剪**: session 消息被硬性截断 (memory_window=50~100)，没有按相关性选择上下文。
- **Interleaved CoT hack**: 每次 tool call 后注入 `"Reflect on the results and decide next steps."` 作为 user message，污染对话历史且浪费 tokens。
- **多 provider 差异**: 不同 provider 对 messages 格式要求不同 (e.g. Anthropic 不允许连续同角色消息)，context.py 需要处理这些边界情况。

### 5.2 Tools 兼容性烂

- **自定义 Tool 抽象层**: nanobot 自己定义了一套 Tool 类体系 (base.py → registry.py)，通过 `to_schema()` 转成 OpenAI 格式。每个 provider 对 tool calling 的支持程度和格式不同。
- **tool_call_id 兼容问题**: Mistral 要求 9 char alphanumeric ID，需要 hash 标准化。
- **arguments 类型不稳定**: 有的 provider 返回 str，有的返回 dict，有的返回 list。
- **Server-side tools 特殊处理**: Anthropic web_search 是 server-side 执行的，需要在 client 端过滤掉。
- **LiteLLM 中间层**: 额外的兼容层引入额外的 quirks 和 debug 难度。

### 5.3 Cache 命中率低

- **System prompt 不稳定**: memory 内容随归档更新，skills summary 随工具注册变化，导致 system prompt cache prefix 频繁失效。
- **History 无缓存保护**: 对话历史没有 cache breakpoint，每新增一条消息整个历史的 cache 就断了。
- **Tool definitions 动态变化**: MCP 工具注册/注销改变 tools 列表，打断 cache。
- **仅两家支持**: `supports_prompt_caching` 只有 Anthropic 和 OpenRouter，其他 provider 无 cache。
- **Agent loop 内多次调用**: 一次用户消息可能触发 20 轮 LLM 调用 (tool loop)，每轮 messages 都在增长，cache 收益递减。

---

## 6. IM 适配层评估 (可复用性)

### 6.1 架构优点

- **完全解耦**: Channel ↔ MessageBus ↔ AgentLoop，channel 完全不关心后端是什么 LLM。
- **统一接口**: BaseChannel 的 `start()`/`stop()`/`send()` 三方法足够简洁。
- **11 个 channel**: Telegram / Discord / Slack / 飞书 / 钉钉 / WhatsApp / QQ / Matrix / Email / iMessage / Mochat。
- **权限控制**: allow_from 白名单，per-channel 配置。
- **消息总线**: asyncio.Queue 双队列，简单高效。

### 6.2 各 Channel 特殊能力

| Channel | 特殊能力 |
|---------|---------|
| Telegram | 打字指示器循环、Draft streaming (sendMessageDraft)、markdown→HTML、语音转文字 (Groq)、proxy 支持 |
| Slack | Thread 回复、emoji reaction (eyes)、Socket Mode、group_policy (mention/open/allowlist)、DM 策略 |
| Discord | WebSocket gateway、mention/open 群组策略、intents 配置 |
| 飞书 | WebSocket 长连接、emoji reaction (THUMBSUP 等) |
| 钉钉 | Stream mode |
| Matrix | E2EE 加密、group_policy、room mentions |
| Email | IMAP poll + SMTP send、auto_reply 开关、subject_prefix |
| QQ | botpy SDK |
| WhatsApp | WebSocket bridge + auth token |

### 6.3 关键接口 (改造时必须保持兼容)

```python
# 入站消息
@dataclass
class InboundMessage:
    channel: str          # "telegram", "slack", etc.
    sender_id: str        # 用户 ID
    chat_id: str          # 聊天/频道 ID
    content: str          # 消息文本
    media: list[str]      # 媒体文件路径
    metadata: dict        # Channel 特定元数据
    session_key_override: str | None  # 可选 session key 覆盖 (e.g. thread)

# 出站消息
@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    reply_to: str | None
    media: list[str]
    metadata: dict

# 消息总线
class MessageBus:
    async publish_inbound(msg)   # Channel → Agent
    async consume_inbound()      # Agent ← Channel
    async publish_outbound(msg)  # Agent → Channel
    async consume_outbound()     # Channel ← Agent
```

---

## 7. 改造方向分析

### 7.1 可以完全去掉的模块

| 模块 | 原因 |
|------|------|
| `agent/loop.py` 的 tool call 循环 | Claude API 原生处理 agentic loop |
| `agent/tools/` 全部自定义工具 | 直接用 Claude 的 tool use，或通过 MCP server |
| `agent/context.py` 的大部分拼接逻辑 | Claude 自己管理 200k context window |
| `providers/litellm_provider.py` | 不需要 LiteLLM 中间层，直接用 Anthropic SDK |
| `providers/registry.py` 的多 provider 支持 | 只用 Anthropic |
| `agent/subagent.py` | 不需要自建子 agent 机制 |
| `agent/skills.py` | 可以大幅简化或去掉 |

### 7.2 必须保留的模块

| 模块 | 原因 |
|------|------|
| `channels/*` 全部 | IM 适配层是核心价值，与后端完全解耦 |
| `bus/*` | 消息总线路由 |
| `config/schema.py` channels 部分 | Channel 配置不变 |
| `session/manager.py` | 仍需 session 持久化（但可大幅简化） |

### 7.3 需要重新设计的部分

| 功能 | 现状 | 改造方向 |
|------|------|----------|
| **System Prompt** | 每次从 identity + bootstrap + memory + skills 拼接 ~20k tokens | 精简为固定人格 prompt + 动态记忆注入。人格部分设计为稳定不变以最大化 cache |
| **Context 管理** | 手动维护 messages list + 硬裁剪 | 直接利用 Claude 200k window。Session 只做 conversation_id mapping，不做消息裁剪 |
| **Tool Use** | 自定义 Tool 类 + ToolRegistry + 手动 20 轮循环 | 直接使用 Claude Messages API 的 tool_use。工具通过 MCP server 接入 |
| **Memory** | MEMORY.md + HISTORY.md + LLM 归档 | 外置长期记忆层。设计: 写入时机 / 存储格式 / 检索方式 / 注入位置 |
| **Prompt Cache** | 手动 cache_control 标记 | 设计 cache-friendly 的 prompt 结构 (固定 system → 记忆 → 对话历史) |
| **Curiosity Ping** | cron 定时系统内消息 | 可保留但简化，不需要走 agent loop |

### 7.4 新架构概念图（已更新为 Agent SDK 方案）

```
[IM Platforms: Telegram, Slack, Discord, 飞书, ...]
    ↓ (BaseChannel + MessageBus — 完全复用)
[Thin Gateway Layer]
    ├── Session Router
    │   └── channel:chat_id → Claude session_id (resume)
    ├── Append Prompt Builder
    │   ├── 固定人格 (soul.md)
    │   ├── 动态记忆 (core.json + active.json + journal)
    │   └── Runtime context (time, channel, chat_id)
    ├── Memory Store
    │   ├── Write: <memory_write> 标注提取 + Stop hook
    │   ├── Read: 每次构建 append prompt 时注入
    │   └── Search: recall_memory MCP tool (archive.json)
    ├── MCP Tools
    │   ├── recall_memory — 归档记忆检索
    │   └── send_message — 跨 channel 发消息
    └── Media Handler (语音转文字、图片处理)
    ↓
[Claude Agent SDK — query() / ClaudeSDKClient]
    ├── Claude Code 全部内置工具 (Read/Write/Edit/Bash/Glob/Grep/WebSearch/WebFetch)
    ├── 内置 agentic loop + subagent 调度
    ├── 内置 session 持久化 + resume
    ├── 自动 prompt caching
    ├── 自动 model routing (haiku/sonnet/opus)
    ├── MCP 自定义工具支持
    ├── Hooks (PreToolUse/PostToolUse/Stop)
    └── sandbox (Bash 沙箱)
```

---

## 8. Claude Agent SDK 调研

> 2026-03-18 补充。确定使用 Agent SDK 作为 backend 而非 raw Anthropic SDK。

### 8.1 基本信息

- **Python 包**: `pip install claude-agent-sdk`
- **TypeScript 包**: `npm install @anthropic-ai/claude-agent-sdk`
- **本质**: Claude Code CLI 的编程接口。不是简化版，是完整的 Claude Code 能力
- **认证**: 使用 Claude Code 登录态 (`~/.claude/`)，不需要单独 API key
- **源码仓库**: 官方 plugins/examples 仓库，CLI 本身是编译后的二进制（`~/.local/bin/claude`）

### 8.2 核心 API

#### `query()` — 单次交互

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for message in query(
    prompt="分析这个项目架构",
    options=ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": "..."},
        resume="session-uuid",
        allowed_tools=["Read", "Write", "Bash", ...],
        permission_mode="acceptEdits",
        mcp_servers={"memory": memory_server},
        hooks={"Stop": [HookMatcher(hooks=[on_stop])]},
        cwd="/path/to/workspace",
        setting_sources=["project"],  # 加载 CLAUDE.md
    ),
):
    # message 类型: AssistantMessage | UserMessage | SystemMessage | ResultMessage
    pass
```

#### `ClaudeSDKClient` — 持续对话

```python
from claude_agent_sdk import ClaudeSDKClient

async with ClaudeSDKClient(options=options) as client:
    await client.query("第一个问题")
    async for msg in client.receive_response():
        print(msg)  # context 自动保持

    await client.query("追问")  # 上下文延续
    async for msg in client.receive_response():
        print(msg)

    # 可中断
    await client.interrupt()
```

#### 自定义 MCP Tool

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("recall_memory", "Search archived knowledge", {"query": str})
async def recall_memory(args):
    results = search_archive(args["query"])
    return {"content": [{"type": "text", "text": results}]}

server = create_sdk_mcp_server(name="memory", tools=[recall_memory])
# 传入 ClaudeAgentOptions(mcp_servers={"memory": server})
```

### 8.3 ClaudeAgentOptions 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `system_prompt` | `str \| {"type": "preset", "preset": "claude_code", "append": str}` | 自定义或追加 system prompt |
| `resume` | `str \| None` | session_id，恢复之前的对话 |
| `continue_conversation` | `bool` | 恢复最近的 session |
| `allowed_tools` | `list[str]` | 自动批准的工具列表 |
| `disallowed_tools` | `list[str]` | 禁用的工具 |
| `permission_mode` | `"default" \| "acceptEdits" \| "plan" \| "bypassPermissions"` | 权限模式 |
| `can_use_tool` | `Callable` | 自定义权限回调 |
| `mcp_servers` | `dict[str, McpServerConfig]` | 自定义 MCP 服务器 |
| `hooks` | `dict[HookEvent, list[HookMatcher]]` | 事件 hooks |
| `cwd` | `str \| Path` | 工作目录 |
| `setting_sources` | `list["user" \| "project" \| "local"]` | 加载 CLAUDE.md 等配置 |
| `model` | `str \| None` | 指定模型（None = 自动选择） |
| `max_turns` | `int \| None` | 最大轮次限制 |
| `max_budget_usd` | `float \| None` | 费用上限 |
| `agents` | `dict[str, AgentDefinition]` | 自定义 subagent |
| `sandbox` | `SandboxSettings` | 沙箱配置 |
| `env` | `dict[str, str]` | 环境变量 |

### 8.4 消息类型

```python
# 返回的 message 类型
AssistantMessage    # Claude 的回复，包含 TextBlock / ThinkingBlock / ToolUseBlock
UserMessage         # 用户消息（包括 tool result）
SystemMessage       # 系统消息
ResultMessage       # 对话结束标记，包含：
    .session_id     # session UUID
    .total_cost_usd # 总费用
    .usage          # {input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}
    .num_turns      # 轮次数
    .duration_ms    # 耗时
    .is_error       # 是否出错
```

### 8.5 Hooks 系统

| Event | 触发时机 | 用途 |
|-------|---------|------|
| `PreToolUse` | 工具执行前 | 拦截危险操作 |
| `PostToolUse` | 工具执行后 | 日志、监控 |
| `Stop` | 对话结束 | 记忆归档 |
| `UserPromptSubmit` | 用户提交消息 | 输入验证 |
| `SubagentStop` | 子 agent 结束 | 监控 |
| `PreCompact` | context 压缩前 | 记忆提取 |

Hook 返回值可以：
- 允许继续（空 dict）
- 阻止执行（`{"decision": "block", "reason": "..."}`)
- 修改输入（`PermissionResultAllow(updated_input=...)`)

### 8.6 Session 管理

- **创建**: 不传 `resume`，SDK 自动创建新 session
- **恢复**: 传 `resume=session_id`，自动恢复完整对话
- **Fork**: `resume + fork_session=True`，基于旧 session 创建新分支
- **持久化**: Claude Code 自动存储在 `~/.claude/projects/{path}/` 目录下的 JSONL 文件
- **查询**: `list_sessions()` 列出历史 session，`get_session_messages()` 获取消息

### 8.7 Plugin 系统

Claude Code 有完整的 plugin 系统：

- **Commands**: `.claude/commands/*.md`，YAML frontmatter + markdown 指令
- **Agents**: `agents/*.md`，frontmatter 定义 model/tools/description
- **Skills**: `skills/*/SKILL.md`，渐进式披露的文档
- **Hooks**: `hooks/hooks.json` + 脚本（Python/Bash）
- **MCP Servers**: `.mcp.json` 配置外部工具服务器

Plugin 通过 `--plugin-dir` 或 `setting_sources` 加载。

### 8.8 与 raw Anthropic SDK 对比

| 维度 | Raw Anthropic SDK | Claude Agent SDK |
|------|-------------------|-----------------|
| 工具 | 需自己实现每个 tool | 内置 ~15 个 + MCP 自定义 |
| Agentic Loop | 自己写 ~50 行 | 内置，含错误恢复 |
| Session | 自己管理 messages list | 内置持久化 + resume |
| Prompt Cache | 手动标记 4 个 breakpoint | 自动管理 |
| Model Routing | 手动指定 | 自动 haiku/sonnet/opus |
| System Prompt | 完全自由 | preset + append（保留默认能力） |
| 人格化 | 完全自由 | 通过 append 追加（可行但有限制） |
| 沙箱 | 无 | 内置 Bash sandbox |
| 代码量 | backend ~150 行 + tools ~200 行 | gateway ~100 行 |
| 依赖 | `anthropic` 包 | `claude-agent-sdk`（依赖 Claude Code CLI 二进制） |

### 8.9 注意事项 / 风险

1. **依赖 Claude Code CLI**: Agent SDK 底层启动 Claude Code CLI 进程通信。需要 CLI 已安装且已登录
2. **append prompt 的 cache 影响**: 未知 append 内容变化是否破坏 Claude Code 内部的 prompt cache
3. **并发**: 多个 `query()` 并发调用的行为需要实测
4. **Session 持久性**: 进程重启后 resume 旧 session 是否可靠
5. **安全**: `acceptEdits` / `bypassPermissions` 在无人值守 IM 场景的安全风险
6. **费用**: 是否与直接 API 调用有费用差异

---

## 9. Codex / Claude Code 作为 Tool 调研

> 2026-03-18 补充。需求：能否让 bot 将 Codex 或另一个 Claude Code 实例作为 tool 调用。

### 9.1 结论：可以做到

通过 MCP stdio server 包装外部 CLI，或直接用 Bash 工具调用。

### 9.2 方案对比

| 方案 | 实现方式 | 优点 | 缺点 |
|------|---------|------|------|
| MCP stdio server | `@tool` 装饰器包装 CLI 调用 | 结构化输入输出，权限可控 | 需要写 wrapper |
| Bash 直接调用 | Claude 自己执行 `codex -p "..."` | 零开发量 | 输出不结构化 |
| claude-code-mcp | 开源项目，Claude Code 暴露为 MCP server | 现成方案 | 需要 `--dangerously-skip-permissions` |

### 9.3 关键发现

1. **Claude Code 阻止递归调用**: 检测嵌套 session 并拒绝交互式调用。但 `claude -p "..." --output-format json` 非交互模式可能可行
2. **Codex CLI 完全可调用**: `codex --quiet --approval-mode full-auto -m "task"` 可无交互运行
3. **开源参考**: [steipete/claude-code-mcp](https://github.com/steipete/claude-code-mcp) 已实现将 Claude Code 暴露为 MCP server
4. **Agent SDK `agents` 选项**: 只支持定义 Claude 内部 subagent（同一后端），不支持外部模型

### 9.4 推荐

初版不需要。架构上已经通过 MCP tool 预留了扩展点。后续如果需要多模型协作：
- Codex: `@tool("delegate_codex", ...)` + `asyncio.create_subprocess_exec("codex", ...)`
- Claude Code: 用 `claude -p` 非交互模式，或接入 claude-code-mcp
