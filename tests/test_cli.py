"""Tests for CLI entry points."""

from typer.testing import CliRunner

from nekobot.cli import app

runner = CliRunner()


class TestCLIHelp:
    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        assert "gateway" in result.output
        assert "agent" in result.output

    def test_help_flag(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "NekoBot" in result.output

    def test_gateway_help(self):
        result = runner.invoke(app, ["gateway", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output
        assert "--verbose" in result.output

    def test_agent_help(self):
        result = runner.invoke(app, ["agent", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output
        assert "--message" in result.output
        assert "--session" in result.output
        assert "--no-mcp" in result.output
