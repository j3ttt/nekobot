"""Configuration loader."""

from pathlib import Path

import yaml
from loguru import logger

from nekobot.config.schema import Config

_SEARCH_PATHS = [
    Path.home() / ".nekobot" / "config.yaml",
    Path("config.yaml"),
]


def load_config(path: str | Path | None = None) -> Config:
    """Load config from explicit path, or search standard locations."""
    if path:
        p = Path(path)
        if p.exists():
            return _load_yaml(p)
        logger.error("Config not found: {}", p)
        return Config()

    for candidate in _SEARCH_PATHS:
        if candidate.exists():
            logger.info("Using config: {}", candidate)
            return _load_yaml(candidate)

    logger.warning("No config.yaml found, using defaults")
    return Config()


def _load_yaml(p: Path) -> Config:
    with open(p) as f:
        raw = yaml.safe_load(f) or {}
    return Config(**raw)
