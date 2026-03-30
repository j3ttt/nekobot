# NekoBot 🐈‍⬛

Personal AI assistant: Claude Code + personality + long-term memory + IM gateway.

NekoBot wraps [Claude Code](https://docs.anthropic.com/en/docs/claude-code) via the [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) (`claude-agent-sdk`), giving it a persistent personality, layered long-term memory, and multi-channel IM access (Telegram, DingTalk, Discord, ...).

## Architecture

```
IM Channels (Telegram, DingTalk, ...)
        │
        ▼
    MessageBus (async queues)
        │
        ▼
    Gateway ─── PromptBuilder (SOUL/USER/AGENTS + memory injection)
        │   ├── MemoryStore (core / active / archive / journal)
        │   ├── MCP Tools (recall_memory, send_message)
        │   ├── MediaHandler (voice transcription via Groq)
        │   ├── CuriosityPing (proactive messaging)
        │   └── UsageTracker (cost & token logging)
        ▼
    Claude Code (via Agent SDK)
        - Read / Write / Edit / Bash / Glob / Grep
        - WebSearch / WebFetch
        - Auto model routing (haiku / sonnet / opus)
        - Session persistence & auto-compaction
```

## Key Design Decisions

- **Custom system prompt (full replacement)**: 3-layer prompt files (`SOUL.md` / `USER.md` / `AGENTS.md`) in `~/.nekobot/prompts/`, assembled by PromptBuilder with memory + runtime injection.
- **`bypassPermissions`**: Assistant has full server access. No sandbox.
- **Persistent sessions**: Each `channel:chat_id` maps to a Claude `session_id`. Conversations resume across restarts via `resume=session_id`.
- **Layered memory**: `core.json` (stable facts) → `active.json` (volatile context) → `archive/` (searchable knowledge) → `journal.jsonl` (conversation summaries). LLM self-annotates via `<memory_write>` tags.
- **Thin gateway**: IM channels only do message relay and format conversion. All LLM logic lives in Claude Code.

## Project Structure

```
nekobot/
├── bus/            # Async message queue (InboundMessage / OutboundMessage)
├── channels/       # IM channel implementations (Telegram, DingTalk)
├── config/         # Pydantic schema + YAML loader
├── gateway/        # Core: prompt builder, router (SDK client), MCP tools, ping, media
├── memory/         # Layered store, <memory_write> extractor, archive search
├── usage/          # JSONL cost & token tracker
├── bootstrap.py    # First-run setup (~/.nekobot/ directory creation)
└── main.py         # Entry point

data/
├── defaults/              # Bootstrap seed files
│   ├── config.yaml
│   └── prompts/
│       ├── SOUL.md        # Personality & character
│       ├── USER.md        # User info template
│       └── AGENTS.md      # Tool guide & memory rules
└── config.example.yaml    # Configuration example

docs/
├── plan.md         # Architecture design document
├── research.md     # Research notes
└── sdd/            # Step-by-step design documents
```

Runtime data lives in `~/.nekobot/` (created automatically on first run):
```
~/.nekobot/
├── config.yaml           # Main config (tokens, settings)
├── prompts/              # System prompt files (user-editable)
│   ├── SOUL.md           # Personality (rarely changed)
│   ├── USER.md           # User info (occasionally changed)
│   └── AGENTS.md         # Tool guide & memory rules
├── memory/
│   ├── core.json         # Stable facts (profile, preferences)
│   ├── active.json       # Volatile context (projects, todos)
│   ├── archive/          # Searchable knowledge (.md files)
│   └── journal.jsonl     # Conversation summaries
├── data/
│   ├── sessions.json     # channel:chat_id → session_id mapping
│   └── usage.jsonl       # Cost & token logs
└── workspace/            # Claude Code cwd
```

## Setup

```bash
# Install
pip install -e ".[telegram]"

# First run creates ~/.nekobot/ with default config and prompts
python -m nekobot.main

# Or configure manually first
cp data/config.example.yaml ~/.nekobot/config.yaml
# Edit ~/.nekobot/config.yaml: set Telegram token, allow_from, etc.
# Edit ~/.nekobot/prompts/ files to customize personality
```

Requires Claude authentication to be available (`~/.claude/`).

## Configuration

See `data/config.example.yaml`. Key options:

```yaml
gateway:
  workspace: ~/.nekobot/workspace
  prompts_dir: ~/.nekobot/prompts
  permission_mode: bypassPermissions
  forward_thinking: true

channels:
  telegram:
    enabled: true
    token: "BOT_TOKEN"
    allow_from: ["*"]       # ["*"] = allow all, [] = deny all
```

## Memory System

NekoBot uses `<memory_write>` tags for the LLM to self-annotate important facts:

```
<memory_write>
- profile.new_laptop: MacBook Pro M4, 36GB
- project.nekobot: deployed to production
</memory_write>
```

Gateway strips these tags before sending the response to the user, then persists the facts to the appropriate memory layer based on category prefix.

Archive memories can be retrieved via the `recall_memory` MCP tool, or Claude can directly browse `~/.nekobot/memory/archive/` using its built-in Read/Glob tools.

## Development

```bash
pytest tests/ -v          # run tests
ruff check nekobot/       # lint
python scripts/cli_chat.py  # CLI smoke test
```

See [CLAUDE.md](CLAUDE.md) for development conventions and multi-agent coordination protocol.

## Docs

- [CLAUDE.md](CLAUDE.md) — Development contract (module boundaries, interfaces, conventions)
- [Architecture Design](docs/architecture.md) — Full design document
- [Research Notes](docs/research.md) — Claude Agent SDK research
- [HANDOFF.md](HANDOFF.md) — Development changelog

## License

MIT License. See [LICENSE](LICENSE).
