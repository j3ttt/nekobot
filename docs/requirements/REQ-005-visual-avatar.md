# REQ-005: 可视化形象 / Visual Avatar

- Status: planned
- Author: JETTT
- Date: 2026-03-20
- Plan: PLAN-005-visual-avatar

## 背景

给 nekobot 一个可视化的形象，能根据 Claude client 的实时状态变化（思考、说话、空闲、出错等）。形象可以展示在桌面、外接显示设备等不同载体上。

## 目标

1. Gateway 暴露状态变更接口（WebSocket / SSE）
2. 显示端订阅状态事件，自主渲染
3. nekobot 核心与显示端完全解耦，显示端可独立开发和替换

## 架构

```
Gateway (StateEmitter)
  │  状态变更事件（JSON）
  │  WebSocket / SSE
  │
  ├→ macOS 菜单栏 app
  ├→ ESP32 + LCD 桌面摆件
  ├→ Web Dashboard
  ├→ 终端 status bar
  └→ 任何未来的显示端
```

## 状态定义（初步）

| 状态 | 来源 | 含义 |
|------|------|------|
| idle | Gateway 无活跃 client | 闲着/打盹 |
| thinking | ClaudeSDKClient 等待响应中 | 在思考 |
| speaking | OutboundMessage 发送中 | 在说话 |
| error | stderr 捕获到错误 | 出错/困惑 |
| cron_fire | CronService 触发任务 | 在工作 |
| ping | CuriosityPing 触发 | 主动搭话 |

## 显示端方案（调研）

**软件：**
- macOS 菜单栏（SwiftUI/AppKit）— 最轻量
- 桌面悬浮窗（Tauri/Electron）— 桌面宠物
- Web Dashboard（WebSocket）— 跨平台，可兼做监控
- 终端（ASCII art / tmux 状态栏）— 最 geek

**硬件：**
- ESP32 + 小 LCD（1.3"~2.4"）— 像素猫，成本 ~50 元
- ESP32 + 圆形 GC9A01（1.28"）— "猫眼"效果
- 树莓派 + E-ink — 低功耗摆件
- LED 点阵（8x8 / 16x16）— 极简像素表情

**形象风格：**
- 像素猫（16x16 / 32x32 sprite sheet）— 适合小屏/硬件
- Live2D — Vtuber 风格，表现力强
- Lottie 动画 — AE 导出，Web/客户端通用
- 简笔画/SVG — 最快出效果

## nekobot 侧改动（预估）

唯一侵入核心的部分：Gateway 新增 **StateEmitter**，在状态变更时广播事件。

- `nekobot/gateway/state.py`（新）: 状态机 + 事件广播
- `nekobot/gateway/router.py`（改）: 在 thinking/speaking/error 等节点发状态事件
- 暴露方式：WebSocket endpoint 或 Unix socket

## 约束

- 显示端不依赖 nekobot Python 包
- 状态接口协议简单（JSON 事件流），任何语言都能对接
- 不影响现有 Gateway 性能

## 验收标准

待进入 planned 状态后再细化。
