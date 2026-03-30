# Requirements

> 需求收纳目录。Planner Agent 从这里读取需求，产出 Plan 到 `docs/plans/`。

## 命名规则

`REQ-{序号}-{短标题}.md`，例如 `REQ-001-discord-channel.md`

## 现有需求

| REQ | 标题 | Status | Plan |
|-----|------|--------|------|
| REQ-001 | Skill 加载 | done | PLAN-001 |
| REQ-002 | 定时任务 / Cron | done | PLAN-002 |
| REQ-003 | 日报 / Daily Digest | planned | PLAN-003 |
| REQ-004 | 消息时间戳注入 | done | 无需独立 Plan |
| REQ-005 | 可视化形象 / Visual Avatar | done | PLAN-005 |
| REQ-006 | Skills/Commands 目录可见化 | done | 无需独立 Plan |
| REQ-007 | Cron Tool Description 优化 | done | 无需独立 Plan |

## 状态流转

```
draft → accepted → planned → done
                      ↓
               docs/plans/PLAN-xxx.md
```

## 模板

新建需求时复制以下模板：

```markdown
# REQ-{序号}: {标题}

- Status: draft
- Author: {谁提的}
- Date: YYYY-MM-DD
- Plan: (planner agent 填写，指向 PLAN-xxx)

## 背景

为什么需要这个功能？解决什么问题？

## 目标

用 1-3 句话描述期望的结果。

## 约束

- 性能/兼容性/安全要求
- 不能破坏哪些已有功能

## 验收标准

- [ ] 具体可检查的条件 1
- [ ] 具体可检查的条件 2

## 参考

- 相关 issue / 讨论 / 外部链接
```
