# Plans

> Planner Agent 的产出目录。Dev Agent 从这里领取任务。

## 工作流

```
用户/PM                Planner Agent              Dev Agents
  │                        │                         │
  ├─ 提需求 ──────────────→│                         │
  │  docs/requirements/    │                         │
  │                        ├─ 读需求 + 读架构         │
  │                        ├─ 读 CLAUDE.md 接口契约   │
  │                        ├─ 设计 Plan               │
  │                        ├─ 写 docs/plans/PLAN-xxx  │
  │                        ├─ 更新需求 status=planned  │
  │                        │                         │
  │  review/approve ←──────┤                         │
  │                        │                         │
  │                        ├─ 广播到 HANDOFF.md ──────→│
  │                        │                         ├─ 读 Plan
  │                        │                         ├─ 按 Task 开发
  │                        │                         ├─ 更新 HANDOFF.md
  │                        │                         │
```

## 现有 Plan

| Plan | 标题 | Status | REQ |
|------|------|--------|-----|
| PLAN-001 | Skill 加载 | done | REQ-001 |
| PLAN-002 | 定时任务模块 | done | REQ-002 |
| PLAN-003 | Daily Digest Skill | draft | REQ-003 |
| PLAN-005 | Visual Avatar — StateEmitter + WebSocket | done | REQ-005 |

## 命名规则

`PLAN-{序号}-{短标题}.md`，例如 `PLAN-001-discord-channel.md`

序号与 REQ 序号一致（一个 REQ 可能拆成多个 PLAN，用 a/b 后缀）。

## 状态流转

```
draft → approved → in_progress → done
```

## 模板

```markdown
# PLAN-{序号}: {标题}

- Status: draft
- Planner: {agent identifier}
- Date: YYYY-MM-DD
- Requirement: REQ-{序号}

## 概述

一段话描述这个 Plan 做什么。

## 设计决策

| 决策点 | 选项 | 选定 | 理由 |
|--------|------|------|------|
| | A / B | A | ... |

## 接口变更

> 如果不涉及接口变更，写 "无"。
> 涉及变更时，必须同步更新 CLAUDE.md 的 Interface Contracts 段。

- `module.function()` 签名变化
- 新增的公共类型/函数

## 任务分解

每个 Task 对应一个可独立分配给 Dev Agent 的工作单元。

| # | Task | Module | Files | Depends On | Assignee |
|---|------|--------|-------|------------|----------|
| 1 | | | | — | |
| 2 | | | | 1 | |

## 文件清单

### 新建
- `path/to/file.py` — 用途

### 修改
- `path/to/file.py` — 改什么

### 删除
- `path/to/file.py` — 为什么删

## 测试策略

- 单元测试：覆盖哪些场景
- 集成测试：需要什么环境

## 风险

- 已知风险和缓解方案
```

## Planner Agent 检查清单

写 Plan 前必须确认：

1. [ ] 读了 `CLAUDE.md` 的 Module Boundaries 和 Interface Contracts
2. [ ] 读了 `HANDOFF.md` 确认没有冲突的在途工作
3. [ ] 读了 `docs/architecture.md` 确认设计不违背架构原则
4. [ ] Task 分解的粒度足够让 Dev Agent 独立工作（不需要跨模块协调）
5. [ ] 如果涉及接口变更，Plan 中明确标注了 old → new 签名
