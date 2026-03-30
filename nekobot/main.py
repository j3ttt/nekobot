"""NekoBot entry point — delegates to Typer CLI app."""

from nekobot.cli import app


def main() -> None:
    """CLI entry point (registered in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()
