# REQ-004: 消息时间戳注入

- Status: done
- Author: JETTT
- Date: 2026-03-20
- Plan: 无需独立 Plan，改动量小

## 背景

IM 对话中消息有明确的发送时间，但 Claude 看不到。当前 system prompt 的 Runtime 段有构建时间，但后续消息复用同一 session 时 Claude 不知道每条消息的实际时间。对于定时任务、提醒类场景，时间感知尤为重要。

## 目标

让 Claude 知道每条用户消息的发送时间。

## 实现方式

在 `nekobot/gateway/router.py` 的 `_handle()` 中，将 `msg.timestamp` 拼入发给 Claude 的 content 前：

```python
# 示例
content = f"[{msg.timestamp.strftime('%Y-%m-%d %H:%M')}] {content}"
```

## 验收标准

- [ ] 每条发给 Claude 的消息前带有 `[YYYY-MM-DD HH:MM]` 时间戳
- [ ] Cron 触发的消息同样带时间戳
- [ ] 不影响现有测试
