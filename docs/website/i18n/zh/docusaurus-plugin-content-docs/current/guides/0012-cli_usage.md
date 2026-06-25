# PowerMem CLI 使用指南 {#powermem-cli-usage-guide}

本指南介绍如何使用 PowerMem 1.0.0 中引入的命令行界面 (CLI)。CLI 提供了一整套记忆操作、配置管理、备份/恢复功能，以及一个交互式 Shell——所有操作均可在终端中完成。

## 目录 {#table-of-contents}

- [安装与调用](#installation-and-invocation)
- [全局选项](#global-options)
- [命令概览](#command-overview)
- [记忆命令](#memory-commands)
- [配置命令](#configuration-commands)
- [统计信息](#statistics)
- [管理命令](#management-commands)
- [交互式 Shell](#interactive-shell)
- [Shell 自动补全](#shell-completion)

---

## 安装与调用 {#installation-and-invocation}

安装 PowerMem 后，CLI 可通过以下方式使用：

- **`pmem`** – 主要入口点（当作为控制台脚本安装时）
- **`powermem-cli`** – 备用入口点
```bash
# 确保已安装 PowerMem
pip install powermem

# 查看版本和帮助
pmem --version
pmem --help
```
如果未提供子命令，CLI 将打印 "Missing command." 并显示主帮助信息。

---

## 全局选项 {#global-options}

这些选项属于根命令。**`--env-file` / `-f` 和 `--verbose` / `-v` 必须出现在子命令名称之前**（标准 Click 组选项）。**`--json` / `-j` 也可以在许多子命令中使用**，需放在子命令之后——请参阅每个命令的帮助信息。

| 选项 | 简写 | 描述 |
|------|------|------|
| `--env-file PATH` | `-f` | `.env` 配置文件的路径。覆盖默认值（例如 `./.env`）。 |
| `--json` | `-j` | 以 JSON 格式输出结果。 |
| `--verbose` | `-v` | 启用详细输出（例如错误的堆栈跟踪）。 |
| `--install-completion SHELL` | — | 为 `bash`、`zsh`、`fish` 或 `powershell` 安装 shell 自动补全。 |
| `--version` | — | 显示 CLI 版本。 |
| `--help` | `-h` | 显示帮助信息。 |

### 全局 env 文件位置 {#global-env-file-placement}

根命令中的选项 **`--env-file` / `-f`**：

- **位置：** 始终放在子命令之前，例如 `pmem -f ./.env.staging memory list`，而不是 `pmem memory list -f ./.env`（后者对于全局选项无效）。
- **作用范围：** 在该次调用中，同一个文件将被用于 **`memory`**、**`config`**、**`stats`**、**`manage`** 和 **`shell`**（行为与设置 `POWERMEM_ENV_FILE` 相同）。
- **`config validate` 和 `config init`** 也接受在子命令中使用 **`--env-file` / `-f`**：
  - **validate：** 要验证的文件（如果省略，将依次回退到全局 `--env-file`，然后是默认发现的 `.env`）。
  - **init：** 要写入的目标文件（如果省略，将依次回退到全局 `--env-file`，然后是默认路径）。
- **`memory search`：** 在 `search` 之后，**`-f` 表示 `--filters`**（JSON 过滤器），而不是 env 文件。要为该次运行指定 `.env` 文件，请使用 **`pmem -f path/to/.env memory search "…"`** 或 **`pmem --env-file path/to/.env memory search "…"`**。

**示例：**
```bash
pmem -f .env.production memory list
pmem --env-file .env.production config show
pmem --json stats
pmem -v memory add "User prefers dark mode" --user-id user123
pmem --install-completion bash
```
---

## 命令概览 {#command-overview}

| 命令组       | 子命令                          | 描述                     |
|--------------|--------------------------------|--------------------------|
| **memory**   | add, search, get, update, delete, list, delete-all | 对记忆进行CRUD操作和搜索。 |
| **config**   | show, validate, test, init    | 查看、验证、测试和初始化配置。 |
| **stats**    | (无)                          | 显示记忆统计信息。         |
| **manage**   | backup, restore, cleanup, migrate | 备份、恢复、清理和迁移数据。 |
| **shell**    | (无)                          | 启动交互模式（REPL）。     |

---

## Memory 命令 {#memory-commands}

所有 Memory 命令都在 `memory` 组下运行，并使用与 Python SDK 相同的后端（相同的配置和存储）。要使用非默认的 `.env` 文件，请在 `memory` 之前传递 **全局** `--env-file` / `-f`（参见[全局环境文件位置](#global-env-file-placement)）。

### pmem memory add CONTENT {#pmem-memory-add-content}

添加一个新的记忆。内容可以是一个事实或简短描述；在启用推理（默认）时，系统可能会去重或与现有记忆合并。

**参数:**

- `CONTENT`（必需）：记忆内容（例如一句话或一个简短段落）。

**选项:**

| 选项                | 简写  | 描述                                   |
|---------------------|-------|----------------------------------------|
| `--user-id USER_ID` | `-u`  | 记忆的用户 ID。                        |
| `--agent-id AGENT_ID` | `-a` | 记忆的 Agent ID。                      |
| `--run-id RUN_ID`   | `-r`  | 运行/会话 ID。                         |
| `--metadata JSON`   | `-m`  | 以 JSON 字符串形式提供的元数据，例如 `'{"key": "value"}'`。 |
| `--scope SCOPE`     | —     | 取值范围：`private`、`agent_group`、`user_group`、`public`。 |
| `--memory-type TYPE` | —    | 取值范围：`working`、`short_term`、`long_term`。 |
| `--no-infer`        | —     | 禁用智能推理（不去重/合并）。           |
| `--json`            | `-j`  | 以 JSON 格式输出。                     |

**示例:**
```bash
pmem memory add "User prefers dark mode" --user-id user123
pmem memory add "API key is stored in vault" -m '{"category": "security"}'
pmem memory add "Meeting at 3pm Friday" -u user1 -a agent1 --no-infer
```
---

### pmem memory search QUERY {#pmem-memory-search-query}

通过语义相似性搜索与给定查询相关的记忆。

**参数:**

- `QUERY` (必填): 搜索查询文本。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--user-id USER_ID` | `-u` | 按用户 ID 过滤。 |
| `--agent-id AGENT_ID` | `-a` | 按 Agent ID 过滤。 |
| `--run-id RUN_ID` | `-r` | 按运行/会话 ID 过滤。 |
| `--limit N` | `-l` | 最大结果数量（默认值: 10）。 |
| `--threshold T` | `-t` | 最小相似度分数（例如 `0.3`）。 |
| `--filters JSON` | `-f` | 以 JSON 格式提供的额外过滤条件。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**注意:** 在此子命令中，**`-f` 是 `--filters`**，而不是全局环境文件。若要使用特定的 `.env` 文件，请运行 `pmem -f /path/.env memory search "…" `（全局选项需置于 `memory` 之前）。

**示例:**
```bash
pmem memory search "user preferences" --user-id user123
pmem memory search "dark mode" -l 5 -j
pmem memory search "123" -t 0.3
pmem -f .env.production memory search "preferences" --user-id user123
```
---

### pmem memory get MEMORY_ID {#pmem-memory-get-memory_id}

通过全局 ID 检索单个记忆。可选的 `--user-id` / `--agent-id` 用于强制访问控制（仅当记忆属于该用户/Agent 时才会返回）。

**参数:**

- `MEMORY_ID`（必需）：数字记忆 ID。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--user-id USER_ID` | `-u` | 用于访问控制的用户 ID。 |
| `--agent-id AGENT_ID` | `-a` | 用于访问控制的 Agent ID。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem memory get 123456789
pmem memory get 123456789 --user-id user123
```
---

### pmem memory update MEMORY_ID CONTENT {#pmem-memory-update-memory_id-content}

更新现有记忆的内容（以及可选的元数据）。

**参数:**

- `MEMORY_ID`（必需）：数字记忆 ID。
- `CONTENT`（必需）：新内容。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--user-id USER_ID` | `-u` | 用于访问控制的用户 ID。 |
| `--agent-id AGENT_ID` | `-a` | 用于访问控制的 Agent ID。 |
| `--metadata JSON` | `-m` | 新的元数据，格式为 JSON。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem memory update 123456789 "Updated content"
pmem memory update 123456789 "New content" -m '{"updated": true}'
```
---

### pmem memory delete MEMORY_ID {#pmem-memory-delete-memory_id}

通过 ID 删除记忆。如果未使用 `--yes`，将提示确认。

**参数:**

- `MEMORY_ID` (必需): 数字记忆 ID。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--user-id USER_ID` | `-u` | 用于访问控制的用户 ID。 |
| `--agent-id AGENT_ID` | `-a` | 用于访问控制的 Agent ID。 |
| `--yes` | `-y` | 跳过确认。 |

**示例:**
```bash
pmem memory delete 123456789
pmem memory delete 123456789 --yes
```
---

### pmem memory list {#pmem-memory-list}

列出记忆，并支持可选的过滤、分页和排序功能。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--user-id USER_ID` | `-u` | 按 user ID 过滤。 |
| `--agent-id AGENT_ID` | `-a` | 按 agent ID 过滤。 |
| `--run-id RUN_ID` | `-r` | 按 run ID 过滤。 |
| `--limit N` | `-l` | 最大结果数（默认值: 50）。 |
| `--offset N` | `-o` | 分页偏移量（默认值: 0）。 |
| `--sort-by FIELD` | `-s` | 排序字段: `created_at`、`updated_at`、`id`（默认值: `created_at`）。 |
| `--order ORDER` | — | `asc` 或 `desc`（默认值: `desc`）。 |
| `--filters JSON` | `-f` | 以 JSON 格式提供的额外过滤条件。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem memory list --user-id user123
pmem memory list -l 20 -o 0
pmem memory list --sort-by created_at --order desc
```
---

### pmem memory delete-all {#pmem-memory-delete-all}

删除所有符合指定过滤条件的记忆。**此操作不可逆。** 需要使用 `--confirm` 并进行交互式确认。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--user-id USER_ID` | `-u` | 按 user ID 过滤。 |
| `--agent-id AGENT_ID` | `-a` | 按 agent ID 过滤。 |
| `--run-id RUN_ID` | `-r` | 按 run ID 过滤。 |
| `--confirm` | — | **必需。** 确认批量删除操作。 |

**示例:**
```bash
pmem memory delete-all --user-id user123 --confirm
pmem memory delete-all --run-id session1 --confirm
```
---

## 配置命令 {#configuration-commands}

配置命令使用与 SDK 相同的基于 `.env` 的设置。在任何子命令之前使用 **全局** `--env-file` / `-f`，或者在 **`config validate`** 和 **`config init`** 中使用子命令自己的 `--env-file` / `-f`（参见[全局 env 文件位置](#global-env-file-placement)）。

### pmem config show {#pmem-config-show}

显示当前配置（来自选定的 `.env` 文件）。敏感值（例如 API 密钥、密码）会被隐藏，除非使用了 `--show-secrets`。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--section SECTION` | `-s` | 要显示的部分：`llm`、`embedder`、`vector_store`、`graph_store`、`intelligent_memory`、`agent_memory`、`reranker` 或 `all`（默认）。 |
| `--show-secrets` | — | 显示 API 密钥和密码（谨慎使用）。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem config show
pmem config show --section llm
pmem config show -j
pmem -f .env.production config show
```
---

### pmem config validate {#pmem-config-validate}

验证配置文件。报告错误和可选警告；使用 `--strict` 时，会执行更多检查。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--env-file PATH` | `-f` | 要验证的 `.env` 文件路径。 |
| `--strict` | — | 启用严格验证。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem config validate
pmem config validate -f .env.production
pmem config validate --strict
```
---

### pmem config test {#pmem-config-test}

测试数据库、LLM 和 embedder 的连接性（使用当前配置）。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--component COMPONENT` | `-c` | 可选值：`database`、`llm`、`embedder`、`all`（默认）。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem config test
pmem config test -c database
pmem config test -c llm
```
---

### pmem config init {#pmem-config-init}

运行一个交互式配置向导，用于创建或更新 `.env` 文件。支持快速开始（最少提示）或自定义（完整）模式。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--env-file PATH` | `-f` | 目标 `.env` 文件（默认：自动检测或 `./.env`）。 |
| `--dry-run` | — | 显示计划的更改但不写入。 |
| `--test` / `--no-test` | — | 写入后运行验证和连接性测试（默认：否）。 |
| `--component COMPONENT` | `-c` | 当使用 `--test` 时：`database`、`llm`、`embedder` 或 `all`。 |

**示例:**
```bash
pmem config init
pmem config init -f .env
pmem config init --test --component database
```
---

## 统计 {#statistics}

### pmem stats {#pmem-stats}

显示记忆统计信息（总计数、按类型分布、年龄等）。可选过滤器适用于与其他记忆命令相同的后端。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--user-id USER_ID` | `-u` | 按用户 ID 过滤。 |
| `--agent-id AGENT_ID` | `-a` | 按 Agent ID 过滤。 |
| `--detailed` | `-d` | 显示更详细的统计信息。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem stats
pmem stats -u user123
pmem stats --agent-id agent1 -j
pmem stats --detailed
```
---

## 管理命令 {#management-commands}

### pmem manage backup {#pmem-manage-backup}

将记忆导出到一个 JSON 文件。通过过滤器和限制条件控制包含哪些记忆。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--output PATH` | `-o` | 输出文件（默认: `powermem_backup_<timestamp>.json`）。 |
| `--user-id USER_ID` | `-u` | 按 user ID 过滤。 |
| `--agent-id AGENT_ID` | `-a` | 按 agent ID 过滤。 |
| `--run-id RUN_ID` | `-r` | 按 run ID 过滤。 |
| `--limit N` | `-l` | 导出的最大记忆数量（默认: 10000）。 |
| `--include-metadata` | — | 包含元数据（默认: true）。 |
| `--json` | `-j` | 以 JSON 格式输出状态。 |

**示例:**
```bash
pmem manage backup -o backup.json
pmem manage backup --user-id user123 -o user_backup.json
pmem manage backup -l 1000
```
---

### pmem manage restore {#pmem-manage-restore}

从由 `pmem manage backup` 生成的 JSON 备份文件中导入记忆。可以覆盖用户/Agent ID，并跳过重复项。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--input PATH` | `-i` | **必需。** 输入备份文件。 |
| `--user-id USER_ID` | `-u` | 覆盖所有恢复记忆的用户 ID。 |
| `--agent-id AGENT_ID` | `-a` | 覆盖所有恢复记忆的 Agent ID。 |
| `--dry-run` | — | 预览恢复操作但不写入。 |
| `--skip-duplicates` | — | 跳过已存在的记忆（默认值: true）。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem manage restore -i backup.json
pmem manage restore -i backup.json --dry-run
pmem manage restore -i backup.json -u new_user
```
---

### pmem manage cleanup {#pmem-manage-cleanup}

移除或归档低保留分数的记忆（基于艾宾浩斯理论）。使用 `--dry-run` 进行预览。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--threshold T` | `-t` | 删除保留分数低于此值的记忆（默认值: 0.1）。 |
| `--archive-threshold T` | — | 归档保留分数低于此值的记忆（默认值: 0.3）。 |
| `--user-id USER_ID` | `-u` | 按 user_id 过滤。 |
| `--agent-id AGENT_ID` | `-a` | 按 agent_id 过滤。 |
| `--dry-run` | — | 仅预览，不进行更改。 |
| `--force` | `-f` | 跳过确认。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem manage cleanup --dry-run
pmem manage cleanup --threshold 0.2
pmem manage cleanup -u user123 --force
```
---

### pmem manage migrate {#pmem-manage-migrate}

在存储之间迁移数据（例如主存储和子存储）。可用性取决于存储后端。

**选项:**

| 选项 | 简写 | 描述 |
|------|------|------|
| `--target-store INDEX` | `-t` | **必需。** 目标子存储索引。 |
| `--source-store INDEX` | `-s` | 源子存储索引（默认：主存储）。 |
| `--delete-source` | — | 迁移后从源中删除。 |
| `--dry-run` | — | 仅预览。 |
| `--json` | `-j` | 以 JSON 格式输出。 |

**示例:**
```bash
pmem manage migrate -t 0 --dry-run
pmem manage migrate -t 1 --delete-source
```
---

## 交互式 Shell {#interactive-shell}

### pmem shell {#pmem-shell}

启动 PowerMem 的交互式 REPL（读取-求值-输出循环）。您可以运行记忆和统计命令，而无需每次都输入 `pmem memory` 或 `pmem stats`，并为会话设置默认的 `user_id` / `agent_id`。

**Shell 内的命令：**

| 命令 | 描述 |
|------|------|
| `add <content> [--user-id id] [--agent-id id]` | 添加一条记忆。 |
| `search <query> [--user-id id] [--limit n] [--threshold t]` | 搜索记忆。 |
| `get <memory_id> [--user-id id]` | 根据 ID 获取记忆。 |
| `update <memory_id> <content> [--user-id id]` | 更新记忆。 |
| `delete <memory_id> [--user-id id]` | 删除记忆。 |
| `list [--user-id id] [--limit n]` | 列出记忆。 |
| `stats [--user-id id]` | 显示统计信息。 |
| `set user <user_id>` | 设置默认用户 ID。 |
| `set agent <agent_id>` | 设置默认 Agent ID。 |
| `set json on\|off` | 启用/禁用 JSON 输出。 |
| `show settings` | 显示当前会话设置。 |
| `clear` | 清屏。 |
| `help` | 显示帮助信息。 |
| `exit`, `quit`, `q` | 退出 Shell。 |

**示例会话：**
```bash
$ pmem shell

==================================================
  PowerMem Interactive Mode
==================================================
Type 'help' for available commands, 'exit' to quit

powermem> set user user123
powermem> add "User prefers dark mode"
powermem> search "preferences"
powermem> list --limit 10
powermem> exit
```
---

## Shell 补全 {#shell-completion}

您可以为 `pmem`（以及 `powermem-cli`）安装 TAB 补全功能，以便在按下 TAB 键时建议子命令和选项。

**安装：**
```bash
# Bash
pmem --install-completion bash
# 然后 source ~/.bashrc 或打开新的终端。

# Zsh
pmem --install-completion zsh

# Fish
pmem --install-completion fish

# PowerShell
pmem --install-completion powershell
# 将打印出的脚本添加到 $PROFILE 以持久化。
```
Bash/Zsh 脚本存储在 `~/.config/powermem/` 下，如果您确认，将在您的 `~/.bashrc` 或 `~/.zshrc` 中添加一行 source 命令。Fish 自动补全脚本安装在 `~/.config/fish/completions/pmem.fish` 下。PowerShell 的相关指令会打印出来供您添加到配置文件中。

---

## 概要 {#summary}

- 使用 **`pmem`**（或 **`powermem-cli`**），**全局选项**需放在子命令**之前**：**`-f` / `--env-file`**（适用于该运行的所有命令组）、**`-j` / `--json`**、**`-v` / `--verbose`**，以及 **memory**、**config**、**stats**、**manage** 和 **shell** 作为子命令。
- **记忆操作**：`memory add/search/get/update/delete/list/delete-all`，支持过滤器和 JSON 输出。
- **配置**：`config show/validate/test/init` 用于检查、验证、测试和交互式创建 `.env`。
- **统计**：`stats` 支持可选的 user/agent 过滤器和 `--detailed`。
- **管理**：`manage backup/restore/cleanup/migrate` 用于备份、恢复、清理保留数据和存储迁移。
- **交互式使用**：`pmem shell` 提供一个 REPL，支持会话默认值和相同的操作。
- **自动补全**：`pmem --install-completion bash|zsh|fish|powershell` 用于 TAB 补全。

有关配置详情（例如 `.env` 变量），请参阅 [配置指南](./0003-configuration.md)。有关 SDK 和 API 的使用，请参阅 [快速入门](./0001-getting_started.md) 和 [API 文档](../api/overview.md)。
