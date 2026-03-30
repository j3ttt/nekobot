# REQ-003: 日报 / Daily Digest

- Status: planned
- Author: JETTT
- Date: 2026-03-20
- Plan: PLAN-003-daily-digest

## 背景

NekoBot 有了定时任务能力（PLAN-002）后，一个核心使用场景是定时生成日报：从 RSS、GitHub 等数据源抓取信息，由 Claude 筛选总结，推送到用户 IM。

## 目标

1. 支持多种数据源（RSS、GitHub issues，可扩展）
2. 数据抓取用 Python 脚本（确定性），总结用 Claude（创意）
3. 整个日报能力打包为一个自包含的 Skill，不侵入 nekobot 核心代码
4. 用户通过编辑配置文件增减数据源，不需要改代码
5. 与 Cron 模块配合，实现定时自动推送

## 约束

- 以 Claude Agent Skill 形式实现，不新增 nekobot Python 模块
- 抓取脚本的依赖（feedparser、httpx）不加入 nekobot 核心依赖
- 不依赖 PLAN-002 的代码实现（Skill 可以手动触发，Cron 只是定时调度手段）

## 验收标准

- [ ] daily-digest Skill 目录包含 SKILL.md + fetch.py + sources.yaml
- [ ] `fetch.py` 支持 RSS 和 GitHub 两种数据源类型
- [ ] Claude 能通过 Skill 自动调用 fetch.py 并总结输出
- [ ] 用户编辑 sources.yaml 即可增减数据源
- [ ] 配合 Cron 可实现定时早晚报推送
