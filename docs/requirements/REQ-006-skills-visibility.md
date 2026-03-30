# REQ-006: Skills/Commands 目录可见化

- Status: done
- Author: JETTT
- Date: 2026-03-20
- Plan: 无需独立 Plan，改动量小

## 背景

当前 bootstrap 将 Skills 和 Commands 文件放在 `~/.nekobot/workspace/.claude/` 下。`.claude` 是隐藏目录，用户在 Finder 或普通 `ls` 下看不到，不方便发现和自定义。

现状目录结构：

```
~/.nekobot/workspace/
└── .claude/              ← 隐藏，用户看不到
    ├── skills/
    │   └── nekobot-memory/SKILL.md
    └── commands/
        ├── usage.md
        └── skills.md
```

## 目标

将 skills 和 commands 放在可见的位置，通过 symlink 让 Claude Code 仍能从 `.claude/` 路径发现它们。用户在 `~/.nekobot/workspace/` 下直接看到 `skills/` 和 `commands/`，方便浏览和自定义。

目标目录结构：

```
~/.nekobot/workspace/
├── skills/                          ← 可见，用户在这里编辑
│   └── nekobot-memory/SKILL.md
├── commands/                        ← 可见
│   ├── usage.md
│   └── skills.md
└── .claude/
    ├── skills -> ../skills          ← symlink
    └── commands -> ../commands      ← symlink
```

## 约束

- Claude Code 通过 `workspace/.claude/skills/` 发现 Skills，symlink 必须对 Claude Code 透明（Claude Code 能正常跟随 symlink 读取文件）
- bootstrap 的 `ensure_home()` 不覆盖已有文件的原则不变
- 已有用户如果 `.claude/skills/` 是真实目录（非 symlink），需要兼容处理（不能破坏已有 skills）

## 验收标准

- [ ] 新用户首次 bootstrap 后，`~/.nekobot/workspace/skills/` 和 `commands/` 是可见的真实目录
- [ ] `~/.nekobot/workspace/.claude/skills` 和 `.claude/commands` 是指向上级的 symlink
- [ ] `nekobot agent` 能正常发现并使用 Skills（Claude Code 跟随 symlink）
- [ ] 已有用户升级后，如果 `.claude/skills/` 是真实目录，bootstrap 不破坏它（跳过 symlink 创建，或将内容迁移后再创建 symlink）
- [ ] `data/defaults/` 中的 seed 文件路径对应更新
