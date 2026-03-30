# SDD-01: Claude Agent SDK Verification & Integration Fix

## Priority: CRITICAL
## Depends On: None
## Estimated Scope: 3 files modified, ~50 lines changed

---

## 1. Goal

Install `claude-agent-sdk`, verify its actual API surface, and fix any incorrect assumptions in our code. This SDD unblocks everything else.

## 2. Background

`gateway/router.py` and `gateway/tools.py` were written against the **documented** Claude Agent SDK API (from `platform.claude.com/docs/en/api/agent-sdk/python`). However, the SDK has never actually been installed or run. Several assumptions may be wrong:

- Import paths (`from claude_agent_sdk import query, ClaudeAgentOptions`)
- Class/function names (`AssistantMessage`, `TextBlock`, `ResultMessage`)
- `ClaudeAgentOptions` fields (`system_prompt`, `permission_mode`, `resume`, `mcp_servers`, `hooks`, `setting_sources`)
- Message streaming protocol (`async for message in query(...)`)
- MCP server registration (`create_sdk_mcp_server`, `@tool` decorator)

## 3. Steps

### 3.1 Install SDK

```bash
cd /path/to/nekobot
pip install claude-agent-sdk
```

If the package doesn't exist on PyPI, check:
- `pip install anthropic-claude-agent-sdk`
- `pip install @anthropic-ai/claude-agent-sdk`
- npm: `npm install @anthropic-ai/claude-agent-sdk` (TS version exists, Python may not yet)
- Check https://pypi.org/project/claude-agent-sdk/
- Check https://github.com/anthropics/claude-code for SDK packages

### 3.2 Verify Import Paths

Run in Python REPL:

```python
import claude_agent_sdk
dir(claude_agent_sdk)
# Expected: query, ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage,
#           TextBlock, ResultMessage, tool, create_sdk_mcp_server, HookMatcher
```

Record the actual exports. If the module name is different (e.g., `claude_code_sdk`, `anthropic.agent`), update all imports.

### 3.3 Verify ClaudeAgentOptions Fields

```python
from claude_agent_sdk import ClaudeAgentOptions
import inspect
print(inspect.signature(ClaudeAgentOptions.__init__))
# or
help(ClaudeAgentOptions)
```

Expected fields we use:
- `system_prompt: str` — custom system prompt (mode 1, full replacement)
- `resume: str | None` — session ID to resume
- `permission_mode: str` — "bypassPermissions"
- `mcp_servers: dict` — MCP server instances
- `hooks: dict` — hook definitions
- `cwd: str` — working directory
- `setting_sources: list[str]` — ["project"] to load CLAUDE.md
- `model: str | None`
- `max_turns: int | None`
- `max_budget_usd: float | None`

### 3.4 Verify Message Types

```python
from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
# Check: are these the actual class names?
# Check: does AssistantMessage.content contain TextBlock instances?
# Check: does ResultMessage have session_id, total_cost_usd, usage, num_turns, duration_ms?
```

### 3.5 Verify query() Streaming Protocol

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for msg in query(
    prompt="Hello",
    options=ClaudeAgentOptions(
        system_prompt="You are a helpful assistant. Reply briefly.",
        permission_mode="bypassPermissions",
        cwd="/tmp",
    ),
):
    print(type(msg), msg)
```

Check:
- Does it yield `AssistantMessage` then `ResultMessage`?
- What block types appear in `AssistantMessage.content`?
- Does `ResultMessage` contain `session_id`?

### 3.6 Verify MCP Tool Registration

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("test_tool", "A test tool", {"name": str})
async def test_tool(args):
    return {"content": [{"type": "text", "text": f"Hello {args['name']}"}]}

server = create_sdk_mcp_server(name="test", tools=[test_tool])
print(type(server), server)
```

Record: what type does `create_sdk_mcp_server()` return? How is it passed to `ClaudeAgentOptions.mcp_servers`?

## 4. Files to Modify

### `gateway/router.py`
- Fix imports (lines 29-31) to match actual SDK module path
- Fix `_build_options()` (line 164-190) to match actual `ClaudeAgentOptions` signature
- Fix `_handle()` (line 102-157) to match actual message types and attributes

### `gateway/tools.py`
- Fix MCP registration to match actual `@tool` / `create_sdk_mcp_server` API

### `main.py`
- No changes expected unless SDK requires initialization step

## 5. Acceptance Criteria

- [x] SDK installed and importable
- [x] `python -c "from claude_agent_sdk import query, ClaudeAgentOptions"` succeeds
- [x] All imports in `gateway/router.py` resolve without error
- [x] All imports in `gateway/tools.py` resolve without error
- [ ] A minimal `query()` call returns a response (cannot test from within Claude Code — nested session blocked)
- [x] `ResultMessage.session_id` is accessible (verified via introspection)
- [x] Document any API differences from our assumptions in a comment block at top of `gateway/router.py`

## 6. Resolved: SDK Migration History

Initially installed `claude-code-sdk==0.0.25`, then migrated to `claude-agent-sdk>=0.1.49` which provides:
- `ClaudeAgentOptions` with full field support (`setting_sources`, `max_budget_usd`, `tools` preset)
- `stderr` callback for debugging
- Consistent API with official documentation

All fixes applied to `router.py`, `tools.py`, `main.py`, `pyproject.toml`.
