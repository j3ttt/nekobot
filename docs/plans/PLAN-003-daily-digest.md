# PLAN-003: 日报 Skill / Daily Digest

- Status: draft
- Planner: claude-opus
- Date: 2026-03-20
- Requirement: REQ-003

## 概述

以自包含的 Claude Agent Skill 形式实现日报功能。一个目录打包所有文件：Skill 指南、抓取脚本、数据源配置。不新增 nekobot Python 模块，不修改核心代码。

## 设计决策

| 决策点 | 选项 | 选定 | 理由 |
|--------|------|------|------|
| 实现形式 | A: nekobot/sources/ 模块 / B: 自包含 Skill | B | 不侵入核心，用户可自行编辑 |
| 数据抓取 | A: Claude 用 WebSearch / B: Python 脚本 / C: 混合 | B | 确定性高，快，便宜 |
| 总结生成 | A: Python 模板 / B: Claude | B | Claude 擅长筛选和总结 |
| 脚本依赖 | A: 加入 pyproject.toml / B: 脚本自管理 | B | 不污染核心依赖 |
| 配置位置 | A: config.yaml / B: Skill 目录内 sources.yaml | B | 自包含，与 Skill 同生命周期 |

## 架构

```
~/.nekobot/workspace/.claude/skills/daily-digest/
  SKILL.md              # Claude 行为指南
  fetch.py              # 抓取脚本（自带 feedparser/httpx 依赖）
  sources.yaml          # 数据源配置
```

触发流程：

```
Cron / 用户手动 "生成早报"
  → prompt 到 Claude
  → Claude 匹配 daily-digest Skill
  → Skill 指导：运行 fetch.py → 读输出 → 筛选总结 → 格式化推送
```

## 文件详设

### SKILL.md

```markdown
---
name: daily-digest
description: Use when generating daily digest, morning/evening report, news summary, or when a cron job triggers digest generation
---

# Daily Digest

## When to Use

- User asks for a daily report / 日报 / 早报 / 晚报
- Cron job triggers with digest-related prompt
- User asks to check news or updates from configured sources

## Steps

1. Run the fetch script:
   ```
   python ~/.nekobot/workspace/.claude/skills/daily-digest/fetch.py
   ```
2. Read the JSON output — each item has: source, title, content, url, published
3. Filter: skip duplicates, remove irrelevant or old items
4. Group by source, highlight key items
5. Summarize into a concise digest
6. Format for the target channel (Markdown for Telegram, plain text for others)

## Output Format

```
📰 日报 — 2026-03-20

## Hacker News
- **Title 1** — 一句话摘要 [link]
- **Title 2** — 一句话摘要 [link]

## GitHub: nekobot
- Issue #42: Bug description — 新开
- PR #43: Feature name — 已合并

---
共 N 条更新，来自 M 个数据源
```

## Adding New Sources

Edit `sources.yaml` in this skill directory. Supported types: `rss`, `github`.
```

### fetch.py (~100 行)

```python
#!/usr/bin/env python3
"""Fetch items from configured data sources. Output JSON to stdout."""

import json
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

def fetch_rss(source: dict) -> list[dict]:
    """Fetch RSS feed items."""
    try:
        import feedparser
    except ImportError:
        return [{"source": source["name"], "title": "[ERROR] feedparser not installed",
                 "content": "Run: pip install feedparser", "url": "", "published": ""}]
    feed = feedparser.parse(source["url"])
    max_items = source.get("max_items", 20)
    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "source": source["name"],
            "title": entry.get("title", ""),
            "content": entry.get("summary", "")[:500],
            "url": entry.get("link", ""),
            "published": entry.get("published", ""),
        })
    return items

def fetch_github(source: dict) -> list[dict]:
    """Fetch GitHub issues/PRs via API."""
    try:
        import httpx
    except ImportError:
        return [{"source": source["name"], "title": "[ERROR] httpx not installed",
                 "content": "Run: pip install httpx", "url": "", "published": ""}]
    repo = source["repo"]
    url = f"https://api.github.com/repos/{repo}/issues"
    params = {"state": "open", "per_page": source.get("max_items", 20), "sort": "updated"}
    resp = httpx.get(url, params=params, timeout=10)
    resp.raise_for_status()
    items = []
    for issue in resp.json():
        items.append({
            "source": source["name"],
            "title": f"{'PR' if 'pull_request' in issue else 'Issue'} #{issue['number']}: {issue['title']}",
            "content": (issue.get("body") or "")[:500],
            "url": issue["html_url"],
            "published": issue["updated_at"],
        })
    return items

FETCHERS = {"rss": fetch_rss, "github": fetch_github}

def main():
    config_path = Path(__file__).parent / "sources.yaml"
    if not config_path.exists():
        print("[]")
        return
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    sources = config.get("sources", [])
    all_items = []
    for source in sources:
        fetcher = FETCHERS.get(source.get("type"))
        if fetcher:
            try:
                all_items.extend(fetcher(source))
            except Exception as e:
                all_items.append({
                    "source": source.get("name", "unknown"),
                    "title": f"[ERROR] {e}",
                    "content": "", "url": "", "published": "",
                })
    json.dump(all_items, sys.stdout, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
```

### sources.yaml (默认模板)

```yaml
# Daily Digest 数据源配置
# 取消注释并修改以启用数据源

sources: []

# 示例:
#
# sources:
#   - type: rss
#     name: "Hacker News"
#     url: "https://hnrss.org/frontpage"
#     max_items: 20
#
#   - type: rss
#     name: "朝日新聞"
#     url: "https://www.asahi.com/rss/asahi/newsheadlines.rdf"
#     max_items: 10
#
#   - type: github
#     name: "nekobot"
#     repo: "user/nekobot"
#     max_items: 10
```

## 任务分解

| # | Task | Files | Depends On |
|---|------|-------|------------|
| 1 | 编写 fetch.py | `data/defaults/workspace/.claude/skills/daily-digest/fetch.py` | — |
| 2 | 编写 SKILL.md | `data/defaults/workspace/.claude/skills/daily-digest/SKILL.md` | — |
| 3 | 编写 sources.yaml 模板 | `data/defaults/workspace/.claude/skills/daily-digest/sources.yaml` | — |
| 4 | Bootstrap seed | `nekobot/bootstrap.py` | 1, 2, 3 |
| 5 | 测试 fetch.py | `tests/test_daily_digest.py` | 1 |

### Task 4: Bootstrap seed

`nekobot/bootstrap.py` 的 `_SEED_FILES` 新增 daily-digest 目录下三个文件，bootstrap 时 seed 到 `~/.nekobot/workspace/.claude/skills/daily-digest/`。

### Task 5: 测试

- `test_daily_digest.py`:
  - `fetch_rss()` mock feedparser 返回，验证输出格式
  - `fetch_github()` mock httpx 返回，验证输出格式
  - `main()` 空 sources.yaml 输出 `[]`
  - `main()` 缺失依赖时输出 ERROR item

## 文件清单

### 新建
- `data/defaults/workspace/.claude/skills/daily-digest/SKILL.md`
- `data/defaults/workspace/.claude/skills/daily-digest/fetch.py`
- `data/defaults/workspace/.claude/skills/daily-digest/sources.yaml`
- `tests/test_daily_digest.py`

### 修改
- `nekobot/bootstrap.py` — `_SEED_FILES` 新增 daily-digest 三个文件

### 删除
- 无

## 接口变更

无。不修改任何 nekobot 模块接口。

## 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| feedparser/httpx 未安装 | fetch.py 运行失败 | 优雅降级：输出 ERROR item，Claude 看到后提示用户安装 |
| Skill description 匹配不准 | Claude 不触发或误触发 | description 明确列出触发词，用户可调整 |
| fetch.py 路径硬编码 | 用户改了 workspace 路径就不对 | SKILL.md 中用相对描述，Claude 会用 Glob 找到实际路径 |
| GitHub API rate limit | 无 token 时 60 次/小时 | sources.yaml 可扩展支持 token 字段，MVP 先不加 |

## 使用示例

```
用户: 帮我配置每天早晚的日报
Claude: 好的。我先看看你的数据源配置。

[Claude 读取 sources.yaml，发现是空的]
Claude: 你的数据源还没配置。要加什么？比如 RSS 订阅、GitHub 仓库？

用户: 加 Hacker News 和 nekobot 的 GitHub issues
Claude: 收到。

[Claude 编辑 sources.yaml]
[Claude 调用 schedule_task tool 创建早报 + 晚报]

Claude: 搞定了。每天 8:00 和 20:00 会自动抓取 HN 和 nekobot issues，总结后推送给你。
```
