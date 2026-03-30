# REQ-002: 定时任务 / Cron

- Status: done
- Author: JETTT
- Date: 2026-03-19
- Plan: PLAN-002-scheduled-tasks

## 背景

NekoBot 需要定时执行任务的能力，比如：
- 每天早晚推送日报/天气
- 定时提醒航班时间
- 周期性检查 GitHub stars 并汇报
- 一次性提醒（"3 小时后提醒我开会"）
- 待办清单 + 到期提醒

nanobot 已有成熟的 cron 实现，可以复用设计。

## 目标

1. 支持三种调度方式：cron 表达式、固定间隔、一次性定时
2. Claude 自己能通过 MCP tool 创建/管理定时任务
3. 任务触发时，作为 Claude 的 prompt 执行（不是简单发文本，而是让 Claude 动态生成内容）
4. 响应推送到用户指定的 IM 渠道
5. 任务持久化，进程重启后恢复

## 约束

- 复用 nanobot 的设计思路，但适配 nekobot 架构
- 不引入外部调度依赖（不用 celery/APScheduler），用 asyncio 原生
- 与 CuriosityPing 共存，不冲突

## 验收标准

- [ ] 支持 cron 表达式（如 `0 9 * * *`）、间隔（如每 30 分钟）、一次性定时
- [ ] Claude 可通过 tool 调用创建/列出/删除任务
- [ ] 任务触发时 Claude 执行 prompt 并推送到 IM
- [ ] 任务持久化到磁盘，重启后自动恢复
- [ ] 用户可通过 `/cron` slash command 管理任务
