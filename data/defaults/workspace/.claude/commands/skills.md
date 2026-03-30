---
description: List all available skills and slash commands
allowed-tools: [Read, Glob]
---

List all skills and commands available in this workspace:

1. Scan `~/.nekobot/workspace/.claude/skills/*/SKILL.md` — for each, read the YAML frontmatter and show the `name` and `description`
2. Scan `~/.nekobot/workspace/.claude/commands/*.md` — for each, read the YAML frontmatter and show the filename (as `/command-name`) and `description`
3. Also scan `~/.claude/commands/*.md` for user-level commands

Output as a compact list grouped by type (Skills / Commands).
