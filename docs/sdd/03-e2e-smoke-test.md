# SDD-03: End-to-End Smoke Test

## Priority: CRITICAL
## Depends On: SDD-01, SDD-02
## Estimated Scope: 2 new files, ~120 lines

---

## 1. Goal

Create a minimal CLI harness that exercises the full Gateway flow without Telegram. Type a message in terminal, get Claude's response back. This validates the entire stack before connecting IM channels.

## 2. Rationale

Running with Telegram requires bot token setup, network access, etc. A stdin/stdout harness lets us validate:
- Config loading
- PromptBuilder template injection
- Gateway → SDK query() → response
- Memory write extraction
- Session persistence (resume)
- MCP tool invocation
- Usage tracking

## 3. Implementation

### 3.1 Create `scripts/cli_chat.py`

```python
"""
Minimal CLI chat harness for testing the Gateway stack.

Usage:
    python scripts/cli_chat.py [--config config.yaml]

Type messages, get responses. Type 'quit' to exit.
Session is persisted between messages (same session_id).
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nekobot.bus.events import InboundMessage
from nekobot.bus.queue import MessageBus
from nekobot.config.loader import load_config
from nekobot.gateway.prompt import PromptBuilder
from nekobot.gateway.router import Gateway
from nekobot.memory.store import MemoryStore
from nekobot.usage.tracker import UsageTracker


async def main():
    config = load_config()
    gw_cfg = config.gateway

    # Ensure dirs
    gw_cfg.workspace_resolved.mkdir(parents=True, exist_ok=True)
    gw_cfg.data_dir_resolved.mkdir(parents=True, exist_ok=True)

    # Init components
    bus = MessageBus()
    memory = MemoryStore(gw_cfg.memory_path_resolved)
    usage = UsageTracker(gw_cfg.data_dir_resolved)

    prompt_path = Path(gw_cfg.system_prompt_path)
    if not prompt_path.is_absolute():
        prompt_path = Path(__file__).parent.parent / prompt_path
    prompt_builder = PromptBuilder(prompt_path, memory)

    gateway = Gateway(
        config=gw_cfg,
        message_bus=bus,
        memory_store=memory,
        prompt_builder=prompt_builder,
        usage_tracker=usage,
    )

    print("NekoBot CLI Chat (type 'quit' to exit)")
    print("=" * 50)

    session_key = "cli:local"

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.lower() in ("quit", "exit"):
            break
        if not user_input:
            continue

        # Create inbound message
        msg = InboundMessage(
            channel="cli",
            sender_id="local",
            chat_id="local",
            content=user_input,
        )

        # Push to bus and process
        await bus.publish_inbound(msg)
        inbound = await bus.consume_inbound()

        # Call gateway handler directly
        response = await gateway._handle(inbound)

        if response:
            print(f"\n{response}")
        else:
            print("\n(no response)")


if __name__ == "__main__":
    asyncio.run(main())
```

### 3.2 Create minimal `config.yaml` for testing

```yaml
gateway:
  workspace: ~/.nekobot/workspace
  data_dir: ~/.nekobot/data
  system_prompt_path: data/system_prompt.md
  memory_path: ~/.nekobot/data/memory
  permission_mode: bypassPermissions
  forward_thinking: false  # Keep output clean for testing
```

### 3.3 Create `scripts/test_memory.py`

Quick validation that memory write/read works:

```python
"""Test memory store operations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nekobot.memory.store import MemoryStore
from nekobot.memory.extractor import extract_memory_writes

# Test write
store = MemoryStore(Path("/tmp/nekobot_test_memory"))

store.write_fact("profile", "name", "User")
store.write_fact("project", "nekobot", "testing memory layer")
store.write_fact("learning", "python_asyncio", "asyncio.Queue is useful for message passing")

print("=== Core ===")
print(store.render_core())
print("\n=== Active ===")
print(store.render_active())

# Test extractor
response = """Here's my analysis.

<memory_write>
- profile.editor: VS Code
- project.nekobot: memory layer works
</memory_write>

Let me know if you need more details."""

cleaned, facts = extract_memory_writes(response)
print(f"\n=== Extractor ===")
print(f"Cleaned: {cleaned}")
print(f"Facts: {facts}")

# Write extracted facts
store.write_facts(facts)
print(f"\n=== Core after extraction ===")
print(store.render_core())

# Cleanup
import shutil
shutil.rmtree("/tmp/nekobot_test_memory")
print("\nAll tests passed!")
```

## 4. Test Scenarios

Run `scripts/cli_chat.py` and test these scenarios in order:

### 4.1 Basic Response
```
> Hello, who are you?
# Expect: Response matching bot personality from SOUL.md
```

### 4.2 Memory Write
```
> My favorite color is blue. Remember that.
# Expect: Response + memory_write tag extracted
# Check: ~/.nekobot/data/memory/core.json or active.json updated
```

### 4.3 Session Resume
```
# Exit and restart cli_chat.py
> What's my favorite color?
# Expect: Claude remembers from resumed session OR from memory injection
```

### 4.4 Tool Use (if MCP tools wired)
```
> Search your memory for any learning notes
# Expect: Claude invokes recall_memory tool
```

### 4.5 File Operations
```
> List the files in the current workspace directory
# Expect: Claude uses Glob or Bash to list files in ~/.nekobot/workspace/
```

## 5. Directory Structure

```
scripts/
├── cli_chat.py        # Interactive CLI harness
└── test_memory.py     # Memory unit test
```

## 6. Acceptance Criteria

- [ ] `python scripts/test_memory.py` passes
- [ ] `python scripts/cli_chat.py` starts without error
- [ ] User can type a message and get a Claude response
- [ ] Response reflects bot personality from SOUL.md
- [ ] Memory injection works ({MEMORY_CORE} and {MEMORY_ACTIVE} replaced)
- [ ] Session persists across messages in same run
- [ ] Usage logged to `~/.nekobot/data/usage.jsonl`
- [ ] `sessions.json` updated with session_id

## 7. Known Issues to Watch

- If `claude-agent-sdk` is not installed, `gateway/router.py` will return "Internal error: Agent SDK not available."
- If Claude Code CLI is not authenticated (`~/.claude/`), queries will fail
- If `data/system_prompt.md` has syntax issues, prompt builder may produce malformed prompt
- `forward_thinking: false` in test config to avoid cluttering CLI output with thinking blocks
