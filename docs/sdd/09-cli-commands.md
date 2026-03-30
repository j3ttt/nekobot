# 09 — CLI 子命令 (`nekobot gateway` / `nekobot agent`)

## 问题

当前 nekobot 只有一个入口 `nekobot`（`nekobot.main:main`），启动后所有组件在同一进程内运行。没有独立的 CLI 对话工具，测试时需要 `python scripts/cli_chat.py`，不够正式。

参考 nanobot 的 `nanobot gateway` / `nanobot agent` 模式，拆分为子命令。

## 目标

```
nekobot gateway          # 长驻服务：IM channels + gateway 消息循环
nekobot agent            # 交互式 CLI 对话，直接调用 Gateway，无 channels
nekobot agent -m "hello" # 单条消息模式
nekobot --help           # 显示帮助
python -m nekobot        # 同上
```

---

## 设计

### 命令定义

使用 Typer 框架。

#### `nekobot gateway`

等价于之前的 `nekobot` 命令：启动 channels + gateway loop，持续运行直到 SIGINT/SIGTERM。

选项：
- `--config PATH` — 显式指定配置文件路径（覆盖默认搜索）
- `--verbose / -v` — 开启 debug 日志

#### `nekobot agent`

替代 `scripts/cli_chat.py`。

选项：
- `--config PATH` — 同上
- `--message / -m TEXT` — 单条消息模式：发送一条消息，打印回复，退出
- `--session / -s ID` — 指定 session key（默认 `cli:local`）
- `--no-mcp` — 不加载 MCP 工具（调试用）
- `--markdown / --no-markdown` — Markdown 渲染回复（默认开启）
- `--verbose / -v` — 开启 debug 日志

交互体验：
- **prompt_toolkit** — 输入历史（上下箭头）、粘贴处理、异步输入
- **rich** — Markdown 渲染回复、thinking spinner、格式化输出
- **终端管理** — 保存/恢复 termios 状态、flush 输入缓冲
- 退出命令：`exit`、`quit`、`/exit`、`/quit`、`:q`、Ctrl+C

#### 无子命令

`nekobot`（无参数）→ `no_args_is_help=True`，显示帮助。

### 架构

```
nekobot/cli.py
├── app = Typer(no_args_is_help=True)
├── _setup_logging(verbose)
├── _init_gateway(config_path, no_mcp) → (config, bus, gw, ping)
│     ├── ensure_home()
│     ├── load_config(config_path)
│     └── 初始化 Bus/Memory/Usage/Prompt/MCP/Media/Ping/Gateway
├── @app.command gateway(config, verbose)
│     └── asyncio.run(_run_gateway)  # channels + gateway loop + signal handling
└── @app.command agent(config, verbose, message, session, no_mcp)
      └── asyncio.run(_run_agent)    # interactive loop or single message
```

两个命令共享 `_init_gateway()` 初始化逻辑。区别：
- `gateway` 启动 ChannelManager + 信号处理
- `agent` 不启动 channels，直接通过 `gateway._handle()` 处理消息

---

## 文件变更清单

### 新建

| 文件 | 说明 |
|------|------|
| `nekobot/cli.py` | Typer app + gateway/agent 命令 + 共享初始化逻辑 |
| `nekobot/__main__.py` | `python -m nekobot` 支持 |
| `tests/test_cli.py` | CLI help 输出测试 |

### 修改

| 文件 | 变更 |
|------|------|
| `nekobot/main.py` | 简化为 `from nekobot.cli import app; app()` |
| `pyproject.toml` | 新增 `typer>=0.9` 依赖 |

### 删除

| 文件 | 原因 |
|------|------|
| `scripts/cli_chat.py` | 功能由 `nekobot agent` 替代 |

### 不变

| 文件 | 原因 |
|------|------|
| `nekobot/gateway/router.py` | Gateway 类接口不变 |
| `nekobot/channels/*` | 不涉及 |
| `nekobot/bus/*` | 不涉及 |
| `nekobot/memory/*` | 不涉及 |
| `nekobot/config/*` | `load_config` 已支持 path 参数 |
| `nekobot/bootstrap.py` | 不涉及 |

---

## 验证

1. `pip install -e ".[dev]"` 安装成功
2. `nekobot --help` 显示 gateway + agent 两个子命令
3. `nekobot gateway --help` / `nekobot agent --help` 显示各自选项
4. `python -m nekobot --help` 同上
5. `pytest -q` 全部通过
