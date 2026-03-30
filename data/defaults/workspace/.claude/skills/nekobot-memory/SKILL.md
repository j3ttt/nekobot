---
name: nekobot-memory
description: Use when the user asks to recall, search, or find something from long-term memory, past conversations, or archived knowledge
---

# NekoBot Memory Recall

## When to Use

When the user asks about something that might be stored in long-term memory:
- "还记得...吗"
- "之前说过..."
- "上次提到的..."
- Past conversations, saved notes, archived knowledge

## What to Do

1. First check Memory — Core and Memory — Active sections in your context
2. If not found, use the `recall_memory` tool to search archived knowledge
3. If still not found, browse `~/.nekobot/memory/archive/` directly with Read/Glob
4. Report what you found or that the information isn't stored
