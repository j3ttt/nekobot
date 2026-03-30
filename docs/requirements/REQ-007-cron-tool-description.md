# REQ-007: Cron Tool Description 优化

- Status: done
- Author: JETTT
- Date: 2026-03-20
- Plan: 无需独立 Plan，改动量小

## 背景

用户通过 Claude 创建每日定时任务时，Claude 误以为任务"3 天后自动删除"。原因是 `schedule_task` 工具的 description 没有明确说明 cron job 的持久化特性，Claude 将自身 session 的过期行为（Claude Code session ~3 天过期）错误地应用到了 cron job 上。

实际行为：
- **cron job 本身**：持久化到磁盘（`~/.nekobot/data/cron/jobs.json`），永久存在直到被显式 remove
- **每次触发的 session**：Claude Code 管理，可能过期，但不影响下次触发（会创建新 session）

## 目标

优化 `schedule_task` tool 的 description，明确告诉 Claude：
1. Job 持久化到磁盘，重启后依然存在
2. cron 类型的 job 永久循环运行，直到被 remove
3. 每次触发创建新的 Claude session

## 改动

- `nekobot/gateway/tools.py`: `schedule_task` description 从 `"Create, list, or remove scheduled tasks. Tasks execute as Claude prompts at scheduled times."` 改为 `"Create, list, or remove scheduled tasks. Jobs are persisted to disk and survive restarts — a cron job runs forever until explicitly removed. Use cron_expr for recurring schedules (e.g. daily, hourly). Each trigger creates a fresh Claude session."`

## 验收标准

- [x] Tool description 明确表达持久化和永久循环语义
- [x] 全量测试通过
