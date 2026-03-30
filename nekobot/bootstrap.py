"""First-run bootstrap: ensures ~/.nekobot exists with default files."""

import shutil
from pathlib import Path

from loguru import logger

NEKOBOT_HOME = Path.home() / ".nekobot"
_DEFAULTS_DIR = Path(__file__).parent.parent / "data" / "defaults"

# Directories to create
_DIRS = [
    "prompts",
    "memory",
    "memory/archive",
    "data",
    "workspace",
    "workspace/.claude",
    "workspace/skills",
    "workspace/commands",
    "data/cron",
]

# Files to seed (source relative to _DEFAULTS_DIR → dest relative to NEKOBOT_HOME)
_SEED_FILES = [
    ("config.yaml", "config.yaml"),
    ("prompts/SOUL.md", "prompts/SOUL.md"),
    ("prompts/USER.md", "prompts/USER.md"),
    ("prompts/AGENTS.md", "prompts/AGENTS.md"),
    ("prompts/MEMORIZING.md", "prompts/MEMORIZING.md"),
    ("workspace/commands/memorizing.md", "workspace/commands/memorizing.md"),
]

# Symlinks: .claude/X → ../X (so Claude Code discovers skills/commands via .claude/)
_SYMLINKS = [
    ("workspace/.claude/skills", "../skills"),
    ("workspace/.claude/commands", "../commands"),
]


def _ensure_symlink(link_path: Path, target: str) -> None:
    """Create a symlink, migrating existing real directories if needed."""
    if link_path.is_symlink():
        # Already a symlink — check it points to the right target
        if str(link_path.readlink()) == target:
            return
        link_path.unlink()
    elif link_path.is_dir():
        # Existing real directory from older bootstrap — migrate contents
        real_dest = link_path.parent / target
        real_dest.mkdir(parents=True, exist_ok=True)
        for item in link_path.iterdir():
            dest_item = real_dest / item.name
            if not dest_item.exists():
                shutil.move(str(item), str(dest_item))
                logger.info("Migrated {} → {}", item, dest_item)
        shutil.rmtree(link_path)
        logger.info("Removed old directory {}", link_path)

    link_path.symlink_to(target)
    logger.info("Created symlink {} → {}", link_path, target)


def ensure_home(home: Path | None = None) -> Path:
    """Ensure ~/.nekobot exists with required structure.

    Creates directories and copies missing default files.
    Never overwrites existing files.
    Returns the home path.
    """
    root = home or NEKOBOT_HOME
    root.mkdir(parents=True, exist_ok=True)

    for d in _DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)

    for src_rel, dst_rel in _SEED_FILES:
        dst = root / dst_rel
        if dst.exists():
            continue
        src = _DEFAULTS_DIR / src_rel
        if not src.exists():
            logger.warning("Default template missing: {}", src)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        logger.info("Created {}", dst)

    for link_rel, target in _SYMLINKS:
        _ensure_symlink(root / link_rel, target)

    return root
