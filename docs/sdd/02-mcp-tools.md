# SDD-02: MCP Tool Registration (recall_memory + send_message)

## Priority: CRITICAL
## Depends On: SDD-01 (SDK verification)
## Estimated Scope: 2 files modified, ~80 lines changed

---

## 1. Goal

Wire `recall_memory` and `send_message` as MCP tools that Claude can invoke during a query session. Currently the handler functions exist but are not registered with the SDK.

## 2. Current State

`gateway/tools.py`:
- `handle_recall_memory(query, memory_store)` — searches archive/ directory, returns formatted results
- `handle_send_message(channel, chat_id, content, message_bus)` — publishes OutboundMessage to bus
- `build_mcp_tools()` — **placeholder**, returns raw objects instead of MCP server

`gateway/router.py`:
- Line 187-188: `# MCP tools will be registered here once we confirm the SDK API`
- `_build_options()` does NOT pass `mcp_servers` to `ClaudeAgentOptions`

## 3. Design

### 3.1 Tool Definitions

**recall_memory**:
- Name: `recall_memory`
- Description: "Search archived long-term knowledge: learning notes, tech details, reference materials. Use when the user asks about something that might be in long-term memory but not in the active context."
- Parameters: `{"query": str}`
- Returns: formatted search results or "No matching archived memories found."

**send_message**:
- Name: `send_message`
- Description: "Send a message to a specific IM channel. Use this to proactively reach the user on a different platform."
- Parameters: `{"channel": str, "chat_id": str, "content": str}`
- Returns: "Message sent."

### 3.2 Registration Pattern (Expected)

Based on SDK docs, the expected pattern is:

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("recall_memory", "Search archived long-term knowledge...", {"query": str})
async def recall_memory_tool(args):
    results = search_archive(archive_path, args["query"])
    if not results:
        return {"content": [{"type": "text", "text": "No matching archived memories found."}]}
    # format results...
    return {"content": [{"type": "text", "text": formatted}]}

server = create_sdk_mcp_server(name="memory", tools=[recall_memory_tool])
```

**If SDK uses a different pattern** (determined in SDD-01), adapt accordingly. The handler logic stays the same.

### 3.3 Lifecycle

MCP servers should be created **once** at Gateway initialization, not per-query. They hold references to `MemoryStore` and `MessageBus` via closures.

## 4. Implementation

### 4.1 Rewrite `gateway/tools.py`

```python
"""Custom MCP tool definitions for the Claude Agent SDK."""

from pathlib import Path

from loguru import logger

from nekoclaw.bus.events import OutboundMessage
from nekoclaw.bus.queue import MessageBus
from nekoclaw.memory.search import search_archive
from nekoclaw.memory.store import MemoryStore


def create_memory_mcp_server(memory_store: MemoryStore):
    """Create MCP server with recall_memory tool."""
    from claude_agent_sdk import tool, create_sdk_mcp_server

    archive_path = memory_store._archive_path

    @tool(
        "recall_memory",
        "Search archived long-term knowledge: learning notes, tech details, "
        "reference materials. Use when the user asks about something that might "
        "be in long-term memory but not in the active context. "
        "You can also directly use Read/Glob to browse the archive directory.",
        {"query": str},
    )
    async def recall_memory(args):
        results = search_archive(archive_path, args["query"])
        if not results:
            return {"content": [{"type": "text", "text": "No matching archived memories found."}]}
        lines = []
        for r in results:
            lines.append(f"**{r['title']}** ({r['path']})")
            lines.append(f"  {r['snippet']}")
            lines.append("")
        return {"content": [{"type": "text", "text": "\n".join(lines).strip()}]}

    return create_sdk_mcp_server(name="memory", tools=[recall_memory])


def create_im_mcp_server(message_bus: MessageBus):
    """Create MCP server with send_message tool."""
    from claude_agent_sdk import tool, create_sdk_mcp_server

    @tool(
        "send_message",
        "Send a message to a specific IM channel. Use this to proactively "
        "reach the user on a different platform or send scheduled messages.",
        {"channel": str, "chat_id": str, "content": str},
    )
    async def send_message(args):
        await message_bus.publish_outbound(
            OutboundMessage(
                channel=args["channel"],
                chat_id=args["chat_id"],
                content=args["content"],
            )
        )
        return {"content": [{"type": "text", "text": "Message sent."}]}

    return create_sdk_mcp_server(name="im", tools=[send_message])
```

### 4.2 Update `gateway/router.py`

In `Gateway.__init__()`, create MCP servers:

```python
def __init__(self, config, message_bus, memory_store, prompt_builder, usage_tracker):
    # ... existing init ...

    # MCP tool servers (created once, reused per query)
    from nekobot.gateway.tools import create_memory_mcp_server, create_im_mcp_server
    self._memory_mcp = create_memory_mcp_server(memory_store)
    self._im_mcp = create_im_mcp_server(message_bus)
```

In `_build_options()`, pass MCP servers:

```python
def _build_options(self, system_prompt, session_id):
    opts = {
        "system_prompt": system_prompt,
        "permission_mode": self.config.permission_mode,
        "cwd": str(self.config.workspace_resolved),
        "setting_sources": ["project"],
        "mcp_servers": {
            "memory": self._memory_mcp,
            "im": self._im_mcp,
        },
    }
    # ... rest unchanged ...
```

## 5. Testing

Test manually after SDD-01 is done:

```python
# In a test script:
async for msg in query(
    prompt="What do you know about my learning notes?",
    options=ClaudeAgentOptions(
        system_prompt="You have a recall_memory tool. Use it to search for learning notes.",
        mcp_servers={"memory": memory_server},
        permission_mode="bypassPermissions",
        cwd="/tmp",
    ),
):
    print(msg)
# Expect: Claude invokes recall_memory tool, gets results, responds
```

## 6. Acceptance Criteria

- [x] `build_mcp_servers()` returns valid MCP server dicts
- [x] Both servers are passed to `ClaudeAgentOptions` in `_build_options()`
- [ ] Claude can invoke `recall_memory` during a query and get archive search results (needs E2E test)
- [ ] Claude can invoke `send_message` during a query and it publishes to MessageBus (needs E2E test)
- [x] No `# placeholder` or `# TODO` comments remain in `gateway/tools.py`

**Note**: Implementation completed alongside SDD-01. Used `build_mcp_servers()` (single function) instead of separate `create_memory_mcp_server` / `create_im_mcp_server` as originally planned. Input schemas use JSON Schema dicts (not Python types) per actual SDK API.

## 7. Fallback

If `create_sdk_mcp_server` is not available or works differently:

- Option A: Define tools as plain dicts in OpenAI tool format and pass via `tools` parameter
- Option B: Use a subprocess-based MCP stdio server (heavier, but standard MCP protocol)
- Option C: Embed tool calls as instructions in system prompt and parse Claude's text output

Document whichever approach works in a comment at the top of `gateway/tools.py`.
