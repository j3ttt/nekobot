# PLAN: Patched Claude Code Architecture

Status: draft
Date: 2026-04-02

## Background

2026-03-31 Claude Code 源码因 npm source map 未剥离而泄漏。我们获得了完整的 TypeScript 源码（~1900 文件，512K+ 行），包括内部 feature flags、compact 算法、query engine 等。

当前 nekobot 通过 claude-agent-sdk 将 Claude Code CLI 作为 subprocess 运行，在此基础上叠加 IM gateway、bus、cron、memory 等能力。主要痛点：

1. **compact prompt 不可自定义** — autocompact 的 summarization prompt 写死在 Claude Code 内部，无法注入 MEMORIZING.md 的记忆归档指令。当前用 PreCompactHook 拦截并重定向，属于 hack。
2. **Feature flags 锁死** — Claude Code 有 89 个 feature flag，外部版本只开放约 30 个。REACTIVE_COMPACT、CONTEXT_COLLAPSE 等有价值的功能无法使用。
3. **SDK 层是黑盒** — claude-agent-sdk 封装了进程管理和通信协议，出错时（如 compact 命令被 timestamp 前缀破坏）难以排查。

## Proposal

基于泄漏源码作为参考，对 Claude Code binary 进行逆向 patch。Patched binary 作为 nekobot 项目的一部分，与用户本地正常安装的 Claude Code 隔离。

### Architecture

```
Telegram ←→ Python (IM gateway + bus + cron) ←→ Patched Claude Code binary
                                                   (nekobot 项目内)
```

### 模块划分

**新增：runtime manager（`nekobot/runtime/`）**
- 启动/停止 patched Claude Code 进程
- 实现 stdin/stdout JSON 协议通信（替代 claude-agent-sdk）
- 进程健康检查、crash recovery

**保留：Python 层**
- IM gateway（Telegram 长连接收发）
- MessageBus（async 事件总线）
- CronService（持久化调度）
- MemoryStore + MCP tools

**移除**
- claude-agent-sdk 依赖
- PreCompactHook（compact 逻辑在 patch 层解决）

### Key Changes

1. **砍掉 claude-agent-sdk** — 不再通过 SDK spawn subprocess
2. **直接管理 Claude Code 进程** — Python runtime manager 启动 patched binary，通过 stdin/stdout JSON 协议直接通信
3. **Compact prompt 自定义** — 在 binary patch 层修改 compact summarization prompt，注入 MEMORIZING.md 指令
4. **Feature flags 解锁** — 打开有价值的内部 feature：
   - REACTIVE_COMPACT — 响应式 compact
   - CONTEXT_COLLAPSE — 上下文折叠
   - CACHED_MICROCOMPACT — 缓存微压缩
   - 其他待评估
5. **Python 只做 Claude Code 做不到的事** — IM 长连接、bus 事件总线、cron 持久化调度

### Isolation

- patched binary 存放于 nekobot 项目内（如 `nekobot/runtime/bin/claude`）
- 用户本地的 `~/.local/share/claude/` 完全不动
- 两个 Claude Code 实例互不影响

### Communication Protocol

Claude Code SDK entrypoint（`src/entrypoints/sdk/`）定义了 stdin/stdout JSON 协议。Python runtime manager 直接实现该协议，替代 claude-agent-sdk。

协议格式（待从源码确认）：
- 输入：`{"type": "user", "message": {"role": "user", "content": "..."}}`
- 输出：streaming JSON lines（assistant messages、tool uses、results）

### Patch Scope

最小化修改原则 — 只改真正必须改的：

| 修改项 | 源码参考 | 目的 |
|--------|----------|------|
| Compact prompt | `src/services/compact/` | 注入自定义 summarization 指令 |
| Feature flags | feature() 函数 | 解锁 REACTIVE_COMPACT 等 |
| Employee check | USER_TYPE 判断 | 使 ant-only 功能对外部可用 |

其他所有能力继续走外部扩展点（MCP servers、skills、agents、hooks、CLAUDE.md）。

### Patch Method: Binary Reverse Patch

不从源码 build。参考源码逆向现有 binary，直接 patch。

**流程：**

1. **提取** — 从 Bun 单文件 binary 中提取嵌入的 JS bundle（已验证：12MB minified JS）
2. **定位** — 参考泄漏源码理解 minified 代码结构，定位 patch 点
3. **替换** — 在 minified JS 上做精准字符串/正则替换
4. **回写** — 将修改后的 JS bundle 写回 binary
5. **验证** — 运行 patched binary 确认功能正常

**定位策略：**

源码是"地图"，手术在 binary 上做。minified 代码的变量名每版都变，但以下锚点是稳定的：
- **字符串常量** — 如 compact prompt `"Your task is to create a detailed summary"`
- **feature flag 名称** — 如 `"REACTIVE_COMPACT"`, `"CONTEXT_COLLAPSE"`
- **逻辑结构** — 函数调用模式、参数顺序

Patch 脚本（`scripts/patch_claude.py`）应记录每个 patch 点的：
- 锚点字符串（用于在新版本中重新定位）
- 替换内容
- 验证方法

**Patch 开发笔记（`docs/patch-notes/`）：**

每次 patch 开发（包括首次和后续升级）应沉淀文档，作为下一次的经验参考：
- 版本号 + 日期
- 每个 patch 点的定位过程（怎么从源码映射到 minified 代码的）
- 遇到的坑和解决方案
- 锚点变化记录（如果是升级）
- binary 结构变化（如果有）

格式：`docs/patch-notes/v{版本号}.md`

### Update Strategy

升级是开发行为，不是自动化流程。patched binary 是 nekobot 项目的一部分，跟着项目版本走。

**升级流程（人工驱动）：**
1. 开发者本地 Claude Code 正常更新到新版本
2. 手动复制新版 binary 到 nekobot 项目
3. 提取 JS bundle，用锚点列表检查 patch 点是否仍然命中
4. 锚点命中 → 跑 patch 脚本自动完成
5. 锚点漂移 → 参考源码（如可用）或逆向分析 minified JS，更新锚点和 patch 脚本
6. 测试验证 patched binary
7. 提交到 nekobot 仓库

**不需要的东西：**
- 不需要自动版本检测
- 不需要包管理器
- 不需要自动升级
- 不需要运行时从用户本地复制

**降级方案：**如 patch 开发受阻，切回未修改 binary + PreCompactHook + claude-agent-sdk（现有架构）。

### Risks

1. **锚点漂移** — 新版本修改了 compact prompt 文本或 feature flag 命名，导致 patch 脚本定位失败。需要人工介入更新锚点。
2. **Binary 格式变更** — Bun 更新可能改变 JS 嵌入方式，导致提取/回写失败。
3. **协议变更** — Claude Code 更新可能改变 stdin/stdout 协议格式，导致 Python 通信层需要适配。
4. **API 兼容性** — patched binary 调用 Anthropic API 时，如果 API 端做了版本校验，可能拒绝旧版本请求。
5. **License** — 泄漏源码用于参考、binary patch 的法律风险。

### What Stays the Same

- Telegram channel adapter
- MessageBus (async in-process queue)
- CronService (persistent scheduler)
- MemoryStore (markdown files + archive)
- MCP tools (nekobot-memory, nekobot-im, nekobot-cron)
- Skills / agents / CLAUDE.md
- StateEmitter (WebSocket)
- CuriosityPing

### What Gets Removed

- claude-agent-sdk dependency（由 runtime manager 替代）
- PreCompactHook（compact 逻辑在 patch 层解决）
- Timestamp injection bug workaround（可以在 patch 层正确处理）

### What Gets Added

- `nekobot/runtime/` — runtime manager 模块（进程管理 + 协议通信）
- `scripts/patch_claude.py` — patch 脚本（提取 → 定位 → 替换 → 回写）
- `scripts/anchors.json` — 锚点定义（跨版本复用）

## Next Steps

1. [ ] 编写 patch 脚本原型 — 提取 JS bundle → 定位 compact prompt → 替换 → 回写
2. [ ] 读 `src/entrypoints/sdk/` 确认 stdin/stdout 协议格式
3. [ ] 梳理所有值得开启的 feature flags，确定锚点
4. [ ] 最小 patch 验证 — 只改 compact prompt，运行 patched binary 确认生效
5. [ ] Python 通信层原型 — 直接管理 patched 进程 + JSON 协议
6. [ ] 文档化锚点列表 — 方便新版本时快速重定位
