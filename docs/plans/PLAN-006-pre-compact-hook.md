# PLAN-006 PreCompact Hook + /memorizing Command

## Context

active.json 占 system prompt ~40%（~1156 tokens），只进不出，会持续膨胀。需要一个记忆整理机制。

但这不只是清理。每次 compaction 是 bot "消化经历"的机会——工作完成了可以归档，但生活中的点滴（闲聊、习惯、情绪、兴趣变化）应该沉淀为对用户的长期理解。/memorizing 是 Self-Evolution 的重要一环。

核心发现：
- SDK 的 PreCompact hook 支持 `continue_: False` 拦截默认 compaction
- auto trigger 的 `custom_instructions` 为空，但可以通过拦截 + 替换绕过
- CLAUDE.md 可以间接影响 compaction 行为，但不够可控

## 方案

PreCompact hook 拦截默认 compaction → 异步发送 `/memorizing` command → Claude 带着自定义 prompt 执行 compact，同时整理记忆。

### 流程

```
auto/manual compaction 触发
  → PreCompact hook
    1. 通知用户："正在整理记忆..."
    2. 返回 {continue_: False} 拦截默认 compaction
    3. asyncio.create_task → 延迟 ~0.5s 发送 /memorizing
  → /memorizing command 执行
    1. Claude 用自定义 prompt compact 对话
    2. Claude 审视当前 active memory，判断哪些条目过时
    3. 通过 memory_write 输出归档指令（category 改为 archive 类型）
    4. Gateway 的 extractor 捕获 memory_write → store.write_fact() 写入 archive/
    5. 从对话中沉淀生活细节、偏好变化 → 写入 core
    6. 提取对话中值得保留的信息写入 memory
  → active 清理
    - write_fact() 写入 archive 时，自动从 active.json 中删除同名条目（方案 A）
```

注意：先 compact 再清理 active（不是反过来）。让 LLM 在 compact 过程中判断哪些该归档，比规则引擎更准。

## 改动

### 1. `nekobot/gateway/hooks.py` — 新建

```python
class PreCompactHook:
    def __init__(self, memory: MemoryStore, bus: MessageBus, get_client: Callable):
        self.memory = memory
        self.bus = bus
        self._get_client = get_client

    async def __call__(self, input_data, tool_use_id, context):
        session_id = input_data["session_id"]
        trigger = input_data.get("trigger", "auto")

        # 通知用户
        # (需要从 session_id 反查 channel/chat_id)
        await self._notify_user(session_id, f"🧠 整理记忆中... (trigger: {trigger})")

        # 异步发送 /memorizing，避免在 hook 内直接调 client
        asyncio.create_task(self._deferred_memorize(session_id))

        return {"continue_": False}

    async def _deferred_memorize(self, session_id):
        await asyncio.sleep(0.5)
        client = self._get_client(session_id)
        await client.query("/memorizing")
```

### 2. `/memorizing` command — 可配置的记忆整理指令

源文件：`nekobot/data/defaults/prompts/MEMORIZING.md`（随项目分发）
运行时：`~/.nekobot/prompts/MEMORIZING.md`（bootstrap 时拷贝）

需要在 bootstrap 流程中加入 MEMORIZING.md 的拷贝逻辑。

prompt 模板（可配置）：
```markdown
你正在整理记忆。

## 压缩对话
总结当前对话的关键信息。保留未完成的任务、用户做出的决定、技术结论。
丢弃调试过程的来回试错、已解决问题的排查细节。
不要丢弃闲聊和生活细节——这些是成长的养分。

## 整理记忆
审视 Memory — Active 中的条目和当前对话，分三类处理：

### 归档（active → archive）
完成的工作、已解决的技术问题、过时的项目状态。
这些信息有参考价值但不需要每次对话都带着。

### 沉淀（→ core）
对话中观察到的用户习惯、偏好变化、情绪模式、生活细节。
这些是成长的养分，应该内化为对用户的长期理解。
例如：用户提到的新兴趣、吐槽中透露的价值观、聊天节奏的变化。

### 不动
仍在进行中的任务、近期会用到的上下文。

用 memory_write 输出变更：
<memory_write>
- core.preference.xxx: 沉淀到核心记忆的内容
- archive.xxx: 归档的工作内容摘要
</memory_write>

对于不需要变动的条目，不做任何操作。
```

### 3. `nekobot/gateway/router.py` — 接入 hook

```python
# Gateway.__init__
self._pre_compact = PreCompactHook(
    memory_store, message_bus,
    get_client=lambda sid: self._clients.get(self._session_id_to_key(sid))
)

# Gateway._build_options
opts["hooks"] = {
    "PreCompact": [HookMatcher(hooks=[self._pre_compact])],
}
```

需要新增 `_session_id_to_key()` 方法（session_id → session_key 反查）。

### 4. `nekobot/memory/store.py` — 仍保留 archive_active_items

作为 fallback，万一 LLM 的 memory_write 没覆盖所有过期条目，可以规则兜底。但不在 hook 中主动调用，留给后续手动清理或定期任务。

```python
def archive_active_items(self, keys: list[tuple[str, str]]) -> int:
    """Move (category, key) pairs from active.json → archive/."""
    active = self._load_json(self._active_path)
    count = 0
    for cat, key in keys:
        if cat in active and key in active[cat]:
            value = active[cat].pop(key)
            self._write_archive(cat, key, str(value))
            count += 1
            if not active[cat]:
                del active[cat]
    if count:
        self._save_json(self._active_path, active)
    return count
```

## 待确认

1. hook 内 `asyncio.create_task` 是否能正常工作（hook 的事件循环是否和 Gateway 共享）
2. `continue_: False` 是否真的能阻止默认 compaction（需实测）
3. session_id → session_key 反查：当前 `_sessions` 是 key→id，需要反向索引
4. auto compact 的 custom_instructions 调研结果（如果支持，可以简化方案，不需要 hook 拦截）— 仍在调研，有结论再调整

## 文件清单

| 文件 | 操作 |
|------|------|
| `nekobot/gateway/hooks.py` | 新建：PreCompactHook |
| `nekobot/gateway/router.py` | 改：__init__ + _build_options + _session_id_to_key |
| `nekobot/memory/store.py` | 改：+archive_active_items（fallback） |
| `nekobot/data/defaults/prompts/MEMORIZING.md` | 新建：记忆整理 prompt 模板（源文件） |
| bootstrap 流程 | 改：加入 MEMORIZING.md 拷贝 |

## 验证

1. 手动 `/compact` → 确认 hook 拦截 + `/memorizing` 被触发
2. 观察 memory_write 输出：active 条目是否被正确归档
3. 检查 active.json token 数在整理后是否下降
4. 压力测试：填满 context 触发 auto compact，确认时序无死锁
