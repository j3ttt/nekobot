# PLAN-005: Visual Avatar — StateEmitter + WebSocket

- Status: **done**
- REQ: REQ-005
- Author: Dev Agent
- Date: 2026-03-20

---

## 概述

本模块为 nekobot 添加了可视化形象的后端基础设施：**StateEmitter**。它追踪 bot 的实时状态（思考、说话、空闲、出错等），并通过 WebSocket 广播给任意数量的显示端客户端。

显示端（macOS app、ESP32 硬件、Web Dashboard、终端等）只需连接 WebSocket 即可订阅状态变更，与 nekobot 核心完全解耦。

---

## 架构

```
Gateway._handle()          →  emit(thinking)  →  emit(speaking)  →  emit(idle)
                                                                     ↑ or emit(error)
CuriosityPing._fire()      →  emit(ping)
CronService._fire()        →  emit(working)
                                    │
                                    ▼
                            StateEmitter
                            ├── 内存状态表（per-session）
                            ├── 全局状态 = max(所有 session 的优先级)
                            └── WebSocket server (ws://host:port/)
                                    │
                                    ▼
                            显示端 clients（任意数量）
```

---

## 状态模型

### BotState 枚举

6 个状态，用 `IntEnum` 定义，数值同时表示优先级（越大越高）：

| 值 | 状态 | 含义 | 触发来源 |
|----|------|------|----------|
| 0 | `idle` | 无活跃请求，bot 空闲 | 请求完成 / session 清除 |
| 1 | `ping` | 主动搭话（curiosity ping） | `CuriosityPing._fire()` |
| 2 | `speaking` | 收到 Claude 响应文本，正在输出 | `_query_claude()` 收到首个 TextBlock |
| 3 | `thinking` | 等待 Claude 响应中 | `_handle()` 入口 |
| 4 | `working` | cron 定时任务执行中 | `CronService._fire()` |
| 5 | `error` | 出错 | 任何异常捕获路径 |

### 全局状态优先级

当多个 session 同时活跃时，全局状态取**最高优先级**：

```
error > working > thinking > speaking > ping > idle
```

例如：session A 在 `speaking`，session B 在 `thinking` → 全局状态为 `thinking`。

当 session 变为 `idle` 时，该 session 从状态表中移除。如果所有 session 都被移除，全局状态回到 `idle`。

---

## WebSocket 协议

### 连接

```
ws://<host>:<port>/
```

默认: `ws://127.0.0.1:9100/`

无需任何路径或认证。连接后立即收到当前全局状态。

### 事件格式

每条消息都是一个 JSON 对象：

```json
{
  "type": "state",
  "state": "thinking",
  "session": "telegram:12345",
  "ts": 1710900000
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `string` | 固定 `"state"` |
| `state` | `string` | 状态名：`idle` / `ping` / `speaking` / `thinking` / `working` / `error` |
| `session` | `string \| null` | 触发状态变更的 session key（如 `"telegram:12345"`），全局事件为 `null` |
| `ts` | `int` | Unix 时间戳（秒） |

### 连接生命周期

1. 客户端连接 → 立即收到一条当前全局状态
2. 每次状态变更 → 广播给所有已连接客户端
3. 客户端断开 → 自动清理，不影响其他客户端
4. 服务端不读取客户端发送的消息（单向推送）

### 典型事件序列

用户发送一条消息的完整生命周期：

```
→ {"type":"state", "state":"thinking", "session":"telegram:12345", "ts":1710900000}
→ {"type":"state", "state":"speaking", "session":"telegram:12345", "ts":1710900003}
→ {"type":"state", "state":"idle",     "session":"telegram:12345", "ts":1710900005}
```

出错时：

```
→ {"type":"state", "state":"thinking", "session":"telegram:12345", "ts":1710900000}
→ {"type":"state", "state":"error",    "session":"telegram:12345", "ts":1710900002}
```

Cron 任务触发：

```
→ {"type":"state", "state":"working", "session":"job-abc123", "ts":1710900000}
```

---

## 配置

在 `config.yaml` 的 `gateway` 段：

```yaml
gateway:
  state_ws_port: 9100        # 显式启用，指定端口
  state_ws_host: "127.0.0.1" # 监听地址，默认只本地
```

- **默认值 `state_ws_port: 0`** → StateEmitter 不会创建，不占用端口（默认不启动）
- 用户需在 `config.yaml` 中显式设置 `state_ws_port: 9100`（或其他端口）才启用
- 修改 `state_ws_host: "0.0.0.0"` → 可从局域网访问（ESP32 等硬件设备需要）
- `data/config.example.yaml` 中应包含注释掉的示例配置

---

## 文件清单

### 新建

| 文件 | 行数 | 说明 |
|------|------|------|
| `nekobot/gateway/state.py` | ~130 | BotState 枚举 + StateEmitter 类（状态追踪 + WS 广播） |
| `tests/test_state.py` | ~160 | 15 个测试（枚举、状态逻辑、WebSocket 集成） |

### 修改

| 文件 | 变更 |
|------|------|
| `nekobot/gateway/router.py` | `Gateway.__init__()` 新增 `state` 参数；`_emit()` helper；`_handle()` 中 emit thinking/idle/error；`_query_claude()` 中 emit speaking |
| `nekobot/gateway/ping.py` | `CuriosityPing.__init__()` 新增 `state` 参数；`_fire()` emit ping |
| `nekobot/cron/service.py` | `CronService.__init__()` 新增 `state` 参数；`_fire()` emit working |
| `nekobot/cli.py` | `_init_gateway()` 创建 StateEmitter 并注入各组件；`_run_gateway()` 的 gather 中启动 WS server |
| `nekobot/config/schema.py` | `GatewayConfig` 新增 `state_ws_port`、`state_ws_host` |
| `pyproject.toml` | 新增 `websockets>=12.0` 依赖 |

---

## 接口变更

```python
# Gateway — 新增 state 参数（可选，向后兼容）
Gateway.__init__(self, ..., state: StateEmitter | None = None)

# CuriosityPing — 新增 state 参数
CuriosityPing.__init__(self, config, bus, state: StateEmitter | None = None)

# CronService — 新增 state 参数
CronService.__init__(self, store, bus, state: StateEmitter | None = None)

# _init_gateway() — 返回值从 5-tuple 变为 6-tuple
(config, bus, gw, ping, cron_service, state_emitter)
```

所有新增参数都是可选的，传 `None` 时 emit 调用为 no-op，不影响现有行为。

---

## StateEmitter API

```python
from nekobot.gateway.state import BotState, StateEmitter

emitter = StateEmitter(host="127.0.0.1", port=9100)

# 属性：当前全局状态
emitter.state  # → BotState.idle

# 更新状态并广播
await emitter.emit(BotState.thinking, session="telegram:12345")

# 启动 WebSocket server（阻塞，通常放在 asyncio.gather 中）
await emitter.run()

# 停止
await emitter.stop()
```

---

## 显示端开发指南

### 最简客户端（Python）

```python
import asyncio
import websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:9100/") as ws:
        async for msg in ws:
            print(msg)

asyncio.run(main())
```

### JavaScript (Web / Node.js)

```javascript
const ws = new WebSocket("ws://127.0.0.1:9100/");
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`State: ${data.state}, Session: ${data.session}`);
  // 根据 data.state 切换动画/表情
};
```

### Swift (macOS 菜单栏 app)

```swift
import Foundation

let task = URLSession.shared.webSocketTask(with: URL(string: "ws://127.0.0.1:9100/")!)
task.resume()

func receive() {
    task.receive { result in
        if case .success(let message) = result,
           case .string(let text) = message,
           let data = text.data(using: .utf8),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            let state = json["state"] as? String ?? "idle"
            // 更新菜单栏图标/动画
            DispatchQueue.main.async { updateIcon(state) }
        }
        receive() // 继续监听
    }
}
receive()
```

### ESP32 (Arduino / MicroPython)

```python
# MicroPython
import ujson
import uwebsocket

ws = uwebsocket.connect("ws://192.168.1.x:9100/")  # 需要 host 设为 0.0.0.0
while True:
    msg = ws.recv()
    data = ujson.loads(msg)
    state = data["state"]
    # 根据 state 显示不同的像素猫表情
    display_sprite(state)
```

### 状态到显示的映射建议

| 状态 | 像素猫 | 菜单栏 | LED 点阵 | Web |
|------|--------|--------|----------|-----|
| idle | 打盹/眨眼 | 😺 灰色 | 静态笑脸 | 缓慢呼吸动画 |
| thinking | 挠头/转圈 | 🤔 黄色 | 旋转图案 | 加载 spinner |
| speaking | 张嘴/蹦跳 | 💬 蓝色 | 波浪图案 | 打字动画 |
| error | 惊讶/炸毛 | ❌ 红色 | 叉号 | 红色闪烁 |
| working | 搬砖/锤子 | ⚙️ 绿色 | 齿轮图案 | 进度条 |
| ping | 招手/凑近 | 👋 青色 | 心跳图案 | 弹出气泡 |

---

## 测试

```bash
# 运行 StateEmitter 测试
pytest tests/test_state.py -v

# 全部测试（92 个，含 15 个新增）
pytest tests/ -q
```

测试覆盖：

| 类别 | 测试数 | 内容 |
|------|--------|------|
| BotState 枚举 | 5 | 状态存在性、优先级排序、str 转换、max 取最高 |
| StateEmitter 逻辑 | 7 | 初始状态、emit 更新、idle 移除 session、全局优先级、多 session 交互 |
| WebSocket 集成 | 3 | 连接即收当前状态、状态变更推送、多客户端广播 |

---

## 验证方法

```bash
# 1. 启动 gateway（会自动在 9100 端口启动 WS server）
nekobot gateway

# 2. 另一终端连接 WebSocket
python -c "
import asyncio, websockets
async def main():
    async with websockets.connect('ws://127.0.0.1:9100/') as ws:
        async for msg in ws:
            print(msg)
asyncio.run(main())
"

# 3. 通过 Telegram/DingTalk 发消息，观察 WebSocket 输出
# 预期序列: idle → thinking → speaking → idle

# 4. 禁用 StateEmitter
# config.yaml 中设置 state_ws_port: 0
```

---

## Patch: 默认禁用 WebSocket 监听

> Status: **done** (commit `0aca7bc`)

- `state_ws_port` 默认值 `9100` → `0`（默认不启动）
- 删除死代码 `_PRIORITY` dict
- CronService session key `job.id` → `cron:{job.id}`（与 Gateway 一致）
- `config.example.yaml` 新增注释掉的示例配置

---

## 后续扩展点

- **心跳事件**: 定期发送 `{"type":"heartbeat","ts":...}` 让显示端判断连接健康
- **更多事件类型**: 如 `{"type":"usage","cost":0.03}` 推送费用信息
- **状态持续时间**: 在事件中附带 `duration_ms` 字段
- **认证**: 如需公网暴露，可加 token query param: `ws://host:port/?token=xxx`
- **多路复用**: 让显示端订阅特定 session 的事件（目前是全量广播）
