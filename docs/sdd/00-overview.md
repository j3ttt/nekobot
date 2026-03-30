# SDD Archive — NekoBot

> **SDD 体系已归档。新功能使用 REQ → PLAN 流程，不再新建 SDD。**
>
> - 模块边界和接口契约：[CLAUDE.md](/CLAUDE.md)
> - 需求：`docs/requirements/REQ-xxx.md`
> - 设计+任务分解：`docs/plans/PLAN-xxx.md`
> - 变更广播：[HANDOFF.md](/HANDOFF.md)

## Completed SDDs (Historical)

| # | SDD | Description |
|---|-----|-------------|
| 01 | `01-sdk-verification.md` | Install claude-agent-sdk, verify API, fix imports |
| 02 | `02-mcp-tools.md` | Wire recall_memory + send_message as MCP tools |
| 03 | `03-e2e-smoke-test.md` | End-to-end test: stdin → Claude → stdout |
| 04 | `04-curiosity-ping.md` | Proactive messaging timer |
| 05 | `05-media-handler.md` | Voice transcription + image handling |
| 06 | `06-error-resilience.md` | Typed error handling, stderr capture, session preservation |
| 07 | `07-tests.md` | Unit test suite |
| 08 | `08-workspace-migration.md` | Bootstrap ~/.nekobot/, prompt layering (SOUL/USER/AGENTS) |
| 09 | `09-cli-commands.md` | Typer CLI: `nekobot gateway` + `nekobot agent` subcommands |
