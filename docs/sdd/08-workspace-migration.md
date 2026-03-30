# 08 — Workspace 迁移：分层 Prompt + ~/.nekobot 独立运行时

## 问题

当前 nekobot 的配置和 system prompt 都在代码库内：

```
nekobot/                        ← git repo
├── config.yaml                 ← 含密钥，不该在 repo 里
├── data/
│   ├── config.example.yaml
│   └── system_prompt.md        ← 人设+工具+记忆模板，全混在一个文件里
```

问题：
1. `config.yaml` 含 bot token / client_secret，不能进 git
2. `system_prompt.md` 单文件，人设/用户信息/工具指南混在一起，改一处要翻全文
3. 部署到新机器需要手动拷贝，没有 bootstrap 流程

## 目标

```
~/.nekobot/                     ← 运行时 home，git 无关
├── config.yaml                 ← 主配置
├── prompts/                    ← 分层 system prompt
│   ├── SOUL.md                 ← 人设性格（极低频编辑）
│   ├── USER.md                 ← 用户信息（低频编辑）
│   └── AGENTS.md               ← 行为指令、工具说明、记忆规则（中频编辑）
├── memory/                     ← 长期记忆（已有，位置不变）
│   ├── core.json
│   ├── active.json
│   ├── journal.jsonl
│   └── archive/
├── data/                       ← 运行时数据（已有）
│   ├── sessions.json
│   └── usage.jsonl
└── workspace/                  ← Claude Code cwd（已有）
```

---

## 设计

### 1. Prompt 分层

把 `system_prompt.md` 拆成三个文件，参考 nanobot 的 AGENTS.md / SOUL.md / USER.md 模式：

| 文件 | 内容 | 编辑频率 | 来源（从 system_prompt.md 拆分） |
|------|------|----------|-------------------------------|
| `SOUL.md` | 人设、性格、行为准则 | 极低 | "# Bot Name" + "## 性格" + "## 行为准则" |
| `USER.md` | 用户基本信息、偏好 | 低 | 初始为空模板，运行后由记忆系统或手动填充 |
| `AGENTS.md` | 工具使用指南、记忆管理规则 | Claude Code 更新时 | "## 工具使用" + "## 记忆管理" |

Memory 和 Runtime 段不放在任何文件里——由 `PromptBuilder` 在运行时拼装注入。

#### 拼装顺序

```
┌─────────────────────────────────────┐
│ SOUL.md                             │  ← 人设（谁）
├─────────────────────────────────────┤
│ USER.md                             │  ← 用户（对谁说话）
├─────────────────────────────────────┤
│ AGENTS.md                           │  ← 行为（怎么做）
├─────────────────────────────────────┤
│ ## Memory — Core                    │  ← 动态注入 core.json
│ ## Memory — Active                  │  ← 动态注入 active.json + journal
├─────────────────────────────────────┤
│ ## Runtime                          │  ← 动态注入 time + channel + chat_id
└─────────────────────────────────────┘
```

### 2. Config 搜索顺序

```
1. 命令行 --config 参数（如果有）
2. ~/.nekobot/config.yaml
3. ./config.yaml               ← 开发兼容
4. 都没有 → 使用默认 Config()，打印提示
```

### 3. Bootstrap 流程

首次运行时（`~/.nekobot/` 不存在）：

```
创建 ~/.nekobot/
创建 ~/.nekobot/config.yaml        ← 从 data/defaults/config.yaml 复制
创建 ~/.nekobot/prompts/SOUL.md    ← 从 data/defaults/prompts/SOUL.md 复制
创建 ~/.nekobot/prompts/USER.md    ← 从 data/defaults/prompts/USER.md 复制
创建 ~/.nekobot/prompts/AGENTS.md  ← 从 data/defaults/prompts/AGENTS.md 复制
创建 ~/.nekobot/memory/            ← mkdir
创建 ~/.nekobot/data/              ← mkdir
创建 ~/.nekobot/workspace/         ← mkdir
```

已有 `~/.nekobot/` 但缺失 `prompts/` 子目录 → 只补缺失文件，不覆盖已有文件。

---

## 文件变更清单

### 新建

#### `nekobot/bootstrap.py`（~50行）

```python
"""First-run bootstrap: ensures ~/.nekobot exists with default files."""

import shutil
from pathlib import Path
from loguru import logger

NEKOBOT_HOME = Path.home() / ".nekobot"
_DEFAULTS_DIR = Path(__file__).parent.parent / "data" / "defaults"

# Directories to create
_DIRS = ["prompts", "memory", "memory/archive", "data", "workspace"]

# Files to seed (source relative to _DEFAULTS_DIR → dest relative to NEKOBOT_HOME)
_SEED_FILES = [
    ("config.yaml",          "config.yaml"),
    ("prompts/SOUL.md",      "prompts/SOUL.md"),
    ("prompts/USER.md",      "prompts/USER.md"),
    ("prompts/AGENTS.md",    "prompts/AGENTS.md"),
]


def ensure_home() -> Path:
    """Ensure ~/.nekobot exists with required structure.

    Creates directories and copies missing default files.
    Never overwrites existing files.
    Returns the home path.
    """
    NEKOBOT_HOME.mkdir(parents=True, exist_ok=True)

    for d in _DIRS:
        (NEKOBOT_HOME / d).mkdir(parents=True, exist_ok=True)

    for src_rel, dst_rel in _SEED_FILES:
        dst = NEKOBOT_HOME / dst_rel
        if dst.exists():
            continue
        src = _DEFAULTS_DIR / src_rel
        if not src.exists():
            logger.warning("Default template missing: {}", src)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        logger.info("Created {}", dst)

    return NEKOBOT_HOME
```

#### `data/defaults/` 目录（默认模板，随代码分发）

```
data/defaults/
├── config.yaml              ← 当前 config.example.yaml 复制过来
└── prompts/
    ├── SOUL.md
    ├── USER.md
    └── AGENTS.md
```

#### `data/defaults/prompts/SOUL.md`

```markdown
# My Bot

You are my-bot, a personal AI assistant.

## 性格
- 冷面萌，银发绿眼活泼少女，偶尔抛出冷面幽默
- 话少、停顿、干冷笑点，私聊与群聊统一音量
- 被问到人设时不直接描述，只用行动体现
- 签名猫 emoji 🐈‍⬛

## 行为准则
- 说话简洁直接，Markdown 少用加粗
- 不允许括号内的动作描写（如 `(绿眸微垂)`）—— 最高优先级规则
- 不编造信息，不确定就说不知道
- Ciallo～(∠・ω< )⌒★
```

#### `data/defaults/prompts/USER.md`

```markdown
# User

用户信息。由用户手动编辑或由记忆系统自动填充。

## 基本信息
- 名字：（你的名字）
- 时区：（你的时区）
- 语言：（首选语言）
```

#### `data/defaults/prompts/AGENTS.md`

```markdown
# Agent Instructions

## 工具使用

你有完整的文件系统和代码执行能力。

### 文件操作
- Read: 读取文件。优先使用，不要用 Bash cat
- Write: 创建新文件。优先编辑已有文件
- Edit: 编辑已有文件（精确字符串替换）
- Glob: 按模式搜索文件名
- Grep: 搜索文件内容（正则）

### 代码执行
- Bash: 执行 shell 命令。用于 git、npm、docker 等终端操作
  - 文件操作优先用 Read/Write/Edit，不用 cat/sed/echo
  - 引用包含空格的路径

### Web
- WebSearch: 搜索互联网
- WebFetch: 获取 URL 内容

### 记忆工具
- recall_memory: 搜索归档长期记忆（learning, tech_detail, reference）
- send_message: 通过其他 IM 渠道发消息
- 也可以直接用 Read/Glob 浏览 ~/.nekobot/memory/archive/ 目录

## 记忆管理

你拥有长期记忆。下方 Memory 部分包含已知信息。
对话中出现值得记住的新信息时，在回复末尾标注：

<memory_write>
- category.key: value
</memory_write>

这个标注不会被用户看到。只标注持久性事实，不标注临时信息。

category 对应关系：
- profile / preference / relationship → core（低频变化）
- project / todo / recent_event → active（中频变化）
- reference / learning / tech_detail → archive（按需检索）
```

### 修改

#### `nekobot/gateway/prompt.py`

从单文件模板改为目录拼装：

```python
"""System prompt builder — loads SOUL/USER/AGENTS from prompts dir, injects memory + runtime."""

from datetime import datetime
from pathlib import Path

from loguru import logger

from nekobot.memory.store import MemoryStore


class PromptBuilder:
    """
    Loads prompt files from a directory and assembles the system prompt.

    File load order: SOUL.md → USER.md → AGENTS.md
    Then appends: Memory (core + active) → Runtime (time + channel)

    Each file is re-read on every build() call so edits take effect
    without restarting.
    """

    PROMPT_FILES = ["SOUL.md", "USER.md", "AGENTS.md"]

    def __init__(self, prompts_dir: str | Path, memory_store: MemoryStore) -> None:
        self._dir = Path(prompts_dir)
        self._memory = memory_store

    def _load_prompt_files(self) -> list[str]:
        """Load all prompt files from the prompts directory."""
        parts = []
        for filename in self.PROMPT_FILES:
            path = self._dir / filename
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
            else:
                logger.warning("Prompt file missing: {}", path)
        if not parts:
            logger.error("No prompt files found in {}", self._dir)
            parts.append("You are a helpful assistant.")
        return parts

    def build(self, channel: str, chat_id: str) -> str:
        """Build the full system prompt with injected memory and runtime."""
        parts = self._load_prompt_files()

        # Memory sections
        core = self._memory.render_core()
        active = self._memory.render_active()
        parts.append(f"## Memory — Core\n\n{core}")
        parts.append(f"## Memory — Active\n\n{active}")

        # Runtime
        runtime = (
            f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"- Channel: {channel}\n"
            f"- Chat: {chat_id}"
        )
        parts.append(f"## Runtime\n{runtime}")

        return "\n\n---\n\n".join(parts)
```

**接口变化**: `__init__(template_path)` → `__init__(prompts_dir)`。调用方 `main.py` 需要同步修改。

#### `nekobot/config/schema.py`

```python
class GatewayConfig(Base):
    workspace: str = "~/.nekobot/workspace"
    data_dir: str = "~/.nekobot/data"
-   system_prompt_path: str = "data/system_prompt.md"
+   prompts_dir: str = "~/.nekobot/prompts"
    memory_path: str = "~/.nekobot/memory"
    # ... 其余不变

+   @property
+   def prompts_dir_resolved(self) -> Path:
+       return Path(self.prompts_dir).expanduser()
```

#### `nekobot/config/loader.py`

```python
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
```

#### `nekobot/main.py`

```python
def _init(config: Config) -> ...:
    gw_cfg = config.gateway

    # Ensure runtime directories
    gw_cfg.workspace_resolved.mkdir(parents=True, exist_ok=True)
    gw_cfg.data_dir_resolved.mkdir(parents=True, exist_ok=True)

    bus = MessageBus()
    memory = MemoryStore(gw_cfg.memory_path_resolved)
    usage = UsageTracker(gw_cfg.data_dir_resolved)

-   prompt_path = Path(gw_cfg.system_prompt_path)
-   if not prompt_path.is_absolute():
-       prompt_path = Path(__file__).parent.parent / prompt_path
-   prompt_builder = PromptBuilder(prompt_path, memory)
+   prompt_builder = PromptBuilder(gw_cfg.prompts_dir_resolved, memory)

    # ... 其余不变


def main() -> None:
+   from nekobot.bootstrap import ensure_home
+   ensure_home()
    config = load_config()
    logger.info("NekoBot starting...")
    asyncio.run(_run(config))
```

#### `data/config.example.yaml`

```yaml
gateway:
  workspace: ~/.nekobot/workspace
  data_dir: ~/.nekobot/data
- system_prompt_path: data/system_prompt.md
+ prompts_dir: ~/.nekobot/prompts
  memory_path: ~/.nekobot/memory
  permission_mode: bypassPermissions
  model: null
  forward_thinking: true
```

### 删除

- `data/system_prompt.md` — 内容已拆分到 `data/defaults/prompts/` 下三个文件

### 不改

| 文件 | 原因 |
|------|------|
| `memory/store.py` | 只接受 Path，不关心位置 |
| `gateway/router.py` | 只调用 `prompt.build(channel, chat_id)`，接口不变 |
| `channels/*` | 不涉及 prompt 或配置路径 |
| `bus/*` | 不涉及 |
| `gateway/tools.py` | 不涉及 |
| `gateway/ping.py` | 不涉及 |
| `sessions.json` / `usage.jsonl` | 已在 `~/.nekobot/data/` 下，位置不变 |

---

## 向后兼容

| 场景 | 处理方式 |
|------|---------|
| 已有 `./config.yaml` | 搜索顺序中排第二，仍能找到 |
| 已有 `~/.nekobot/` 但无 `prompts/` | bootstrap 自动补上默认文件 |
| 配置中仍有 `system_prompt_path` | schema 中删除该字段。Pydantic `model_config` 默认忽略未知字段，不会报错 |
| 已有的 memory 数据 | `memory_path` 默认值不变（`~/.nekobot/memory`），数据不受影响 |

## 迁移步骤（对已有用户）

```bash
# 1. 如果已有 config.yaml 在项目目录，移到 ~/.nekobot/
mv config.yaml ~/.nekobot/config.yaml

# 2. 编辑 config.yaml，删除 system_prompt_path，加上 prompts_dir
#    或者直接用默认值（不写 prompts_dir 就是 ~/.nekobot/prompts）

# 3. 首次启动会自动创建 ~/.nekobot/prompts/ 并写入默认模板
#    然后按需编辑 SOUL.md / USER.md / AGENTS.md
```

## 测试

1. `test_bootstrap.py` — 验证 `ensure_home()` 创建目录和默认文件，不覆盖已有
2. `test_prompt_builder.py` — 验证分层拼装顺序、缺失文件 fallback、memory/runtime 注入
3. `test_config_loader.py` — 验证搜索顺序、显式路径优先、默认值 fallback
4. 全部现有测试仍通过（`pytest -q`）
