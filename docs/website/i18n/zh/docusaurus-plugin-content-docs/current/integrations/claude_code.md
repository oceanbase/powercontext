# Claude Code {#claude-code}

通过第一方插件（`memory-powermem`，位于[`apps/claude-code-plugin/`](https://github.com/oceanbase/powermem/tree/main/apps/claude-code-plugin/)）为[Claude Code](https://code.claude.com)提供持久的、自我进化的记忆。

本页面是Claude Code集成的唯一权威来源——插件自身的[`README.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/README.md)也链接到这里。

## 最快捷的路径——让Claude Code自行设置 {#fastest-path--let-claude-code-set-itself-up}

在终端中打开Claude Code并粘贴以下单行命令：
```text
Read and follow apps/claude-code-plugin/SETUP.md to set up PowerMem memory for Claude Code.
```
Claude Code 会读取 [`apps/claude-code-plugin/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/SETUP.md) —— 这是一个标准的自动化设置提示 —— 它会检测您是处于 PowerMem 的 **源代码树**（开发者）还是其他位置（**PyPI/MCP 用户**），询问您所需的少量密钥，并将所有内容端到端连接起来。

想手动配置？下面的完整插件参考涵盖了每个选项。

---

## 功能 {#features}

- **两种连接模式**（与 PowerMem VS Code 扩展一致）。**HTTP 模式是默认模式**（标准）：仅通过 hooks 使用 REST，无 PowerMem MCP 工具在聊天中。**MCP 模式** 是可选的，当您希望在对话中使用 `search_memories` / `add_memory` 时启用。参见 [Configuration](#configuration)。
- **HTTP 模式（默认）**：根目录下的 `.mcp.json` 文件包含空的 `mcpServers`。Hooks 使用 **`POST /api/v1/memories`**（`POWERMEM_BASE_URL`，默认值为 `http://localhost:8848`）。
- **MCP 模式（可选）**：将 [`config/mcp-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/mcp-mode.mcp.json) 复制到 `.mcp.json`（或运行 `apply-connection-mode.sh mcp`）。Claude 通过 **HTTP** `…/mcp` 或 **stdio** 获取 PowerMem 工具。
- **技能**：`/memory-powermem:remember` 和 `/memory-powermem:recall` —— 在 **MCP 模式** 下有效；在默认的 HTTP 模式下无法驱动工具。
- **无缝 REST 捕获**：Hooks 在 **两种模式** 下运行。可选的 **文件轮询器** —— 参见 [watcher/README.md](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/watcher/README.md)。
- **自动检索（无需 MCP，默认开启）**：`UserPromptSubmit` hook 使用用户的提示调用 **`POST /api/v1/memories/search`** 并通过 [`additionalContext`](https://code.claude.com/docs/en/hooks#userpromptsubmit) 注入命中结果。设置 **`POWERMEM_PROMPT_SEARCH=0`**（或 `false` / `no` / `off`）以禁用 —— 每轮对话节省一次搜索往返。适用于 **HTTP 和 MCP** 模式。

## 运行时要求（终端用户） {#runtime-requirements-end-users}

| 部件 | 需要 Python 吗？ | 备注 |
|--------|----------------|-------|
| Claude Code | 否 | |
| MCP 工具 | 否 | **默认关闭**（HTTP 模式）。运行 `apply-connection-mode.sh mcp` 以启用。 |
| **Hooks**（transcript / compact → HTTP API） | **否** | `hooks/bin/` 下的原生二进制文件 + `run-hook.sh`（macOS/Linux）或 Windows 上的 PowerShell。**`POWERMEM_BASE_URL` 默认为 `http://localhost:8848`。** |
| 可选的 **文件轮询器** | 否 | 同一二进制文件：`sh hooks/run-hook.sh poll` —— 参见 [watcher/README.md](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/watcher/README.md)。 |

**macOS / Linux：** 默认的 `hooks/hooks.json` 运行 `sh …/run-hook.sh`。POSIX `sh` 始终存在。

**Windows（原生，无需 Git Bash）：** 如果缺少 `sh`，将 [`hooks/hooks.windows.example.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.windows.example.json) 中的命令合并到您的 Claude `settings.json` 中，以便 hooks 调用 `powershell.exe -File …/run-hook.ps1`。压缩包中包含 `hooks/bin/powermem-hook-windows-amd64.exe`（如果需要 `windows/arm64`，请在构建脚本中添加）。

**重建二进制文件**（开发者 / CI）：需要 Go **1.22+**，然后从仓库根目录运行 `bash scripts/build-hook-binaries.sh` 或 `make build-claude-hook`。`make package-claude-plugin` 会在压缩前自动构建它们。

## 前置条件 {#prerequisites}

1. **PowerMem HTTP API** 可从运行 Claude 的机器访问（例如 `powermem-server --port 8848`）。默认 hooks 使用 **`http://localhost:8848`** —— 使用 `POWERMEM_BASE_URL` 覆盖以连接远程服务器。
2. **仅 MCP 模式：** 另外需要暴露 MCP（通常是同一主机上的 `/mcp`）或 stdio `powermem-mcp`，并通过 [`config/mcp-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/mcp-mode.mcp.json) 切换 `.mcp.json`。
3. **Claude Code**（VS Code 扩展或 CLI）支持插件。

## 手动安装 {#manual-installation}

从 **源代码** 设置集成 —— 这是 **HTTP 模式**（默认）：hooks 将转录内容推送到 REST API，并在每轮对话中注入搜索结果，无需聊天内工具。

### 第一步 —— 下载源代码 {#step-1--download-the-source}
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
### 第 2 步 — 配置 .env {#step-2--configure-env}

复制模板并设置您的 Anthropic 凭证。对于直接访问 Anthropic API，请使用 `LLM_API_KEY`；对于 Claude Code 风格的 bearer-token 网关，请同时使用 `LLM_AUTH_TOKEN` 和 `ANTHROPIC_LLM_BASE_URL`。存储默认使用嵌入式 **seekdb** 数据库（无需单独的数据库），嵌入器默认使用本地的 `sentence-transformers/all-MiniLM-L6-v2` 模型（无需 API 密钥，首次使用时会自动下载）。
```bash
cp .env.example .env
# 然后编辑 .env，至少设置：
#   LLM_PROVIDER=anthropic        # 或 openai / qwen / ...
#   LLM_API_KEY=sk-...
#   LLM_MODEL=claude-3-5-sonnet-latest
#
# 或用于 Anthropic 兼容网关：
#   LLM_PROVIDER=anthropic
#   LLM_AUTH_TOKEN=...
#   ANTHROPIC_LLM_BASE_URL=https://your-gateway.example.com
#   LLM_MODEL=anthropic/claude-sonnet-4.6
```
每个可用设置都记录在[Configuration](#configuration)中；`pmem config init` 还可以交互式生成 `.env` 文件。

### 第 3 步 — 安装 uv {#step-3--install-uv}

PowerMem 使用 `uv` 来创建 Python 环境和安装软件包。
只需安装一次：
```bash
# 非中国大陆网络
curl -LsSf https://astral.sh/uv/install.sh | sh

# 中国大陆网络
export UV_DOWNLOAD_URL=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/
curl -sL https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/uv-installer.sh | sh

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
uv --version
```
### 第 4 步 — 安装 PowerMem 并构建 hook 二进制文件 {#step-4--install-powermem-and-build-the-hook-binaries}

`uv pip install -e '.[server,seekdb]'` 提供了 `powermem-server` 和 `pmem` 命令，以及零配置的本地 seekdb 路径和本地 embedder。
`make build-claude-hook` 编译本地 Go hook 二进制文件（需要 **Go 1.22+**）：
```bash
uv venv venv --python python3.11
source venv/bin/activate
uv pip install --python "$VIRTUAL_ENV/bin/python" -e '.[server,seekdb]'
make build-claude-hook        # 输出到 apps/claude-code-plugin/hooks/bin/
```
### 第五步 — 启动 HTTP API 服务器 {#step-5--start-the-http-api-server}

Hooks 默认地址为 `http://localhost:8848`。保持其运行（或将其作为后台服务启动）：
```bash
powermem-server --host 0.0.0.0 --port 8848
```
### 第6步 — 将插件加载到 Claude Code {#step-6--load-the-plugin-into-claude-code}
```bash
claude --plugin-dir "$(pwd)/apps/claude-code-plugin"
```
### 第 7 步 — 验证 {#step-7--verify}

结束会话（或运行 `/compact`），然后在服务器日志中查找 `POST /api/v1/memories`；在 Claude Code 中运行 `/hooks` 以确认条目已注册。如果没有任何显示，请参阅[故障排查](#troubleshooting-no-requests-while-vibe-coding)。

---

### 加载插件的其他方法 {#other-ways-to-load-the-plugin}

#### 选项 A：从目录加载（开发） {#option-a-load-from-directory-development}
```bash
claude --plugin-dir /path/to/powermem/apps/claude-code-plugin
```
#### 选项 B：从市场安装 {#option-b-install-from-marketplace}

当 PowerMem 市场条目可用时，可通过以下方式安装：
```text
/plugin marketplace add oceanbase/powermem
/plugin install memory-powermem@powermem
/reload-plugins
/memory-powermem:init
```
市场步骤安装了 Claude Code 插件连接器。`/memory-powermem:init` 步骤通过确保 `uv` 来准备 PowerMem 后端，然后使用 uvx 风格的启动器 `uvx --from 'powermem[server,seekdb]' powermem-server` 启动它。PyPI 发布版必须包含插件所需的后端功能，包括默认的本地 Embedding 依赖项。如果缺少 `uv`，init 会自动安装它：非中国大陆网络使用官方的 Astral 安装程序，而中国大陆网络使用 USTC 镜像，地址为 `https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/`。中国大陆的包解析使用 `--default-index https://pypi.tuna.tsinghua.edu.cn/simple`。

在发布前进行分支测试时，可以从一个分支安装市场并运行 init，同时将 `POWERMEM_INIT_PACKAGE` 指向相同的 Git 分支或提交；init 会将其传递给 `uvx --from`：
```text
/plugin marketplace add https://github.com/owner/powermem.git#<branch>
/plugin install memory-powermem@powermem
/reload-plugins
```

```bash
POWERMEM_INIT_PACKAGE='powermem[server,seekdb] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
  sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
```
#### 选项C：打包并复制到另一台机器（离线/内部） {#option-c-pack-and-copy-to-another-machine-offline--internal}

从 **powermem 仓库根目录** 开始：
```bash
make package-claude-plugin
```
或者直接运行脚本：
```bash
bash apps/claude-code-plugin/scripts/package-plugin.sh
```
这将生成 **`apps/claude-code-plugin/dist/powermem-claude-code-plugin-<version>.zip`**。通过 USB、内部制品服务器等方式共享该 zip 文件。

**在另一台计算机上：**

1. 解压 → 你将获得一个名为 `powermem-claude-code-plugin/` 的文件夹，其中包含 `.claude-plugin/`、`hooks/`、`skills/`、`.mcp.json` 等文件。
2. 将 Claude Code 指向该文件夹（建议使用绝对路径）：
   ```bash
   # 可选：如果未设置 POWERMEM_BASE_URL，hooks 默认使用 http://localhost:8848
   export POWERMEM_BASE_URL=https://your-team-powermem.example.com   # team server only
   claude --plugin-dir /path/to/powermem-claude-code-plugin
   ```
3. 在该机器上的要求：**无需 Python**；使用 **macOS/Linux** 的 `sh` 或遵循上述 **Windows** PowerShell hooks。**HTTP API** 必须可供 hooks 访问（如果启用了 MCP 模式，还需访问 `/mcp`）。

要发布一个 **默认启用 MCP 的 zip**，在运行 `make package-claude-plugin` 之前，将根目录的 `.mcp.json` 替换为 `config/mcp-mode.mcp.json`，或者记录用户需要运行 `apply-connection-mode.sh mcp`。

## 卸载和更新 {#uninstall-and-update}

### 卸载 {#uninstall}

如何移除插件取决于您如何启用了它：

| 安装方式 | 操作步骤 |
|----------|----------|
| **`claude --plugin-dir /path/to/...`** | 停止传递 `--plugin-dir`（从 shell 别名、脚本或 IDE 任务中移除）。可选地删除插件文件夹。**除非**您更改了全局设置（见下文），否则 `~/.claude` 中不会留下任何内容。 |
| **Zip / 复制的文件夹** | 删除解压后的目录。停止使用指向该目录的 `--plugin-dir`。 |
| **Git clone / 仓库路径** | 停止使用指向该路径的 `--plugin-dir`；如果不再需要，可以删除克隆的仓库。 |
| **Marketplace / 内置插件 UI** | 运行 `/plugin uninstall memory-powermem@powermem`，然后运行 `/reload-plugins`。如果还想移除 Marketplace 条目，运行 `/plugin marketplace remove powermem`。 |
| **您合并了 [`hooks/hooks.windows.example.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.windows.example.json) 到 `settings.json`** | 编辑 `~/.claude/settings.json` 或项目中的 `.claude/settings.json`，移除调用 `run-hook.ps1` 的 `UserPromptSubmit` / `SessionEnd` / `PostCompact` hook 条目（或还原备份）。否则，即使插件文件夹被删除，hooks 仍会继续运行。 |

hook 二进制文件仅会 **写入** 到您的 PowerMem 服务器；它不会安装系统守护进程。无需单独的“服务卸载”。

### 更新 {#update}

| 安装方式 | 更新步骤 |
|----------|----------|
| **Zip** | 下载新的 `.zip`，替换旧文件夹（删除之前的 `powermem-claude-code-plugin` 目录树，将新文件解压到相同或新路径），然后使用指向新文件夹的 `--plugin-dir` 启动 Claude。 |
| **Repo / `git`** | 执行 `git pull`（或获取您需要的版本），如果需要新的 zip，运行 `make package-claude-plugin` 或 `bash scripts/package-plugin.sh`，然后重启 Claude Code。 |
| **Marketplace** | 运行 `/plugin uninstall memory-powermem@powermem`，从 Marketplace 重新安装，然后运行 `/reload-plugins`。如果后端包有更改，重新运行 `/memory-powermem:init` 以便 uvx 解析新的 PyPI 版本。 |

更新后，重启 Claude Code 会话（或整个应用程序），以重新加载 MCP 配置、技能和 hooks。

## 配置 {#configuration}

### 两种 PowerMem 模式（默认 HTTP，可选 MCP） {#two-powermem-modes-http-default-mcp-optional}

与 PowerMem 其他地方相同的 **MCP / HTTP** 模式划分。**标准配置 = HTTP 模式**：根目录 `.mcp.json` 包含 **`mcpServers: {}`**。**无论哪种模式，hooks 始终使用 REST**。

| 模式 | 插件根目录 `.mcp.json` | Claude 聊天内 | 静默捕获（hooks → REST） |
|------|-------------------------|----------------|---------------------------|
| **HTTP 模式（默认）** | 空的 `mcpServers` — 等同于 [`config/http-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/http-mode.mcp.json) | 无 PowerMem MCP 工具 | 是（`POWERMEM_BASE_URL`，默认 `http://localhost:8848`） |
| **MCP 模式** | 包含 `powermem` — [`config/mcp-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/mcp-mode.mcp.json) | 是 — `search_memories`，`add_memory`，… | 是 |

**切换模式**（从插件目录）：
```bash
bash scripts/apply-connection-mode.sh http  # 恢复标准（默认）HTTP-only 模式
bash scripts/apply-connection-mode.sh mcp   # 启用对话内 PowerMem 工具
```
更改 `.mcp.json` 后重新启动 Claude Code。请参阅 [`config/README.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/README.md)。

**命名注意事项：** 在 **MCP 模式** 下，`transport: "http"` 的含义是“通过 HTTP 连接到 **MCP** 端点”（`https://host/mcp`），而不是“用 REST 替换 MCP”。**HTTP 模式** 的含义是“PowerMem 没有 MCP 条目”；REST 仍然由 hooks 使用。

### MCP 模式：团队或本地 URL {#mcp-mode-team-or-local-url}

运行 `apply-connection-mode.sh mcp` 后，在复制之前编辑 `.mcp.json` 或 `config/mcp-mode.mcp.json`。与您的 REST API 使用相同的主机，MCP 路径通常为 `/mcp`：
```json
{
  "mcpServers": {
    "powermem": {
      "transport": "http",
      "url": "https://powermem.example.com/mcp"
    }
  }
}
```
**stdio MCP**（本地 `powermem-mcp` 进程）—— 在 **MCP 模式** 下，将 `powermem` 块替换为：
```json
{
  "mcpServers": {
    "powermem": {
      "transport": "stdio",
      "command": "powermem-mcp",
      "args": ["stdio"]
    }
  }
}
```
确保已安装 PowerMem（`uv pip install --python "$VIRTUAL_ENV/bin/python" "powermem[server,seekdb]"`），并在使用 stdio 时提供 `.env` 文件。

### HTTP 模式：仅 REST（标准） {#http-mode-rest-only-standard}

这是 **默认** 的根 `.mcp.json`。Claude **没有** PowerMem MCP 工具；引用这些工具的技能无法调用任何内容。**Hooks** 仍会将对话记录/压缩摘要发送到 `POST /api/v1/memories`。尝试 MCP 后重置：`bash scripts/apply-connection-mode.sh http`。

### 无缝记录（hooks + HTTP API） {#seamless-recording-hooks--http-api}

插件附带了 [`hooks/hooks.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.json)、[`hooks/run-hook.sh`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/run-hook.sh) 和 **原生** 的 `hooks/bin/powermem-hook-*`（从 [`cmd/powermem-hook`](https://github.com/oceanbase/powermem/tree/main/apps/claude-code-plugin/cmd/powermem-hook/) 构建）。启用插件后，Claude Code 会合并以下 hooks：

| Hook | 发生的情况 |
|------|------------|
| `UserPromptSubmit` | 默认情况下，**`POST …/api/v1/memories/search`** 使用提交的 `prompt`；顶部结果作为 **额外上下文** 注入到该轮对话中（[Claude Code hooks](https://code.claude.com/docs/en/hooks#userpromptsubmit)）。设置 **`POWERMEM_PROMPT_SEARCH=0`**（或 `false` / `no` / `off`）以跳过搜索（hook 仍注册；禁用时开销很小）。 |
| `SessionEnd` | 从 `transcript_path` 获取完整 **对话记录**（解析的 JSONL：用户/助手/摘要行）→ **`POST …/api/v1/memories`**。 |
| `PostCompact` | `/compact` 或自动压缩后的 **`compact_summary`** 字段 → **`POST …/api/v1/memories`**。 |

**写入** hooks 使用 `POST {POWERMEM_BASE_URL}/api/v1/memories`。**Prompt 搜索** 使用 `POST {POWERMEM_BASE_URL}/api/v1/memories/search`。两者均不需要 MCP。

Claude Code 启动时可选的环境变量：

| 变量 | 是否必需 | 描述 |
|------|----------|------|
| `POWERMEM_BASE_URL` | 否 | 默认为 **`http://localhost:8848`**（与默认 `.mcp.json` 的主机相同，但没有 `/mcp`）。可设置为团队网关，例如 `https://powermem.example.com`。 |
| `POWERMEM_API_KEY` | 如果服务器使用认证 | 作为 `X-API-Key` 发送 |
| `POWERMEM_USER_ID` | 否 | 默认为操作系统登录名 |
| `POWERMEM_AGENT_ID` | 否 | 可选的 `agent_id` 用于记忆 |
| `POWERMEM_HOOK_MAX_CHARS` | 否 | 对话记录上限（默认 `120000`） |
| `POWERMEM_HOOK_SCRUB` | 否 | Hook 负载的确定性本地清理器。默认值为 `1`；仅在希望将原始 hook 数据发送到配置的服务器时设置为 `0` / `false` / `no` / `off`。 |
| `POWERMEM_HOOK_PRIVACY_LEVEL` | 否 | `standard`（默认）会屏蔽高置信度的凭据模式；`strict` 还会屏蔽常见的电子邮件和电话号码。 |
| `POWERMEM_HOOK_SECRET_ACTION` | 否 | `redact`（默认）替换匹配的值；`block` 在发现高置信度凭据时成功跳过记忆写入。 |
| `POWERMEM_HOOK_PATH_PRIVACY` | 否 | 内容和元数据的路径处理：`home`（默认；主目录路径变为 `~/...`，其他绝对路径仅保留文件名），`basename`，`omit` 或 `full`。 |
| `POWERMEM_HOOK_SEARCH_SECRET_POLICY` | 否 | 高置信度凭据模式的 Prompt 搜索处理：`skip`（默认），`redact` 或 `off`（仅禁用搜索秘密跳过/屏蔽策略；启用 hook 清理时，路径隐私和严格的 PII 清理仍然适用）。 |
| `POWERMEM_INFER_TRANSCRIPT` | 否 | 设置为 `1` 以启用服务器端对大型对话记录的推理（默认关闭） |
| `POWERMEM_INFER_COMPACT` | 否 | 设置为 `0` 以禁用对压缩摘要的推理（默认启用） |
| `POWERMEM_PROMPT_SEARCH` | 否 | **默认：启用** — 通过 `UserPromptSubmit` 在每个用户提示上注入语义搜索结果。设置为 **`0`** / **`false`** / **`no`** / **`off`** 以禁用。 |
| `POWERMEM_PROMPT_SEARCH_LIMIT` | 否 | 每个提示返回的最大记忆数（默认 **8**，上限 **30**）。 |
| `POWERMEM_PROMPT_SEARCH_MAX_CHARS` | 否 | 注入上下文字符串的上限（默认 **24000**）。 |

Hook 清理器在 `SessionEnd`、`PostCompact`、工作区文件写入、Prompt 搜索以及 `PostCompact` 分离工作环境交接之前运行。写入元数据包括一个 `privacy` 对象，其中包含活动级别、路径模式、操作和屏蔽计数；原始匹配值不会记录在其中。

**SessionEnd 超时：** Claude Code 默认对 `SessionEnd` hooks 使用短超时。Hook **立即返回**，并在 **分离的工作进程** 中上传，因此大型对话记录仍然可以上传而不会阻塞退出。如果切换到 hook 内的同步上传，请提高 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS`（参见 [Claude Code hooks – SessionEnd](https://code.claude.com/docs/en/hooks#sessionend)）。

### 疑难解答：在编程时“没有请求” {#troubleshooting-no-requests-while-vibe-coding}

您看到的通常是 **预期的**：
1. **默认 HTTP 模式** — 聊天过程中**没有** PowerMem MCP 工具，因此 Claude **不会**在每条消息上调用 `/mcp`。**`POST /api/v1/memories`**（写入）仍然来自 **`SessionEnd`** / **`PostCompact`**，而不是每次回复。默认情况下，**`POST /api/v1/memories/search`** 会通过 `UserPromptSubmit` **在每条用户消息上运行**；设置 **`POWERMEM_PROMPT_SEARCH=0`** 可关闭此功能。
2. **并非每个 hook 都是逐轮触发** — `SessionEnd` 在**会话结束**时运行（退出、`/clear`、`/resume` 切换等）。`PostCompact` 在**手动或自动压缩**后运行，而不是每次回复后运行。
3. **那些 GET 请求**（`/system/status`，`/memories/stats`，……）通常来自其他客户端（例如 **PowerMem VS Code extension** 仪表板），而不是 Claude Code 的 hooks。

**如何验证 hooks：**

- **结束 Claude Code 会话**（退出使用 `--plugin-dir` 的 CLI 会话），然后检查服务器日志中的 **`POST /api/v1/memories`**（worker 会在退出后不久运行）。
- 或触发 **`/compact`**（或等待自动压缩），并查找压缩摘要写入。
- 在 Claude Code 中输入 **`/hooks`** 并确认 `UserPromptSubmit`（如果存在）/ `SessionEnd` / `PostCompact` 列出了此插件的命令（参见 [hooks 菜单](https://code.claude.com/docs/en/hooks#the-hooks-menu)）。

**如果您希望在对话期间产生流量：**

- **`POWERMEM_PROMPT_SEARCH` 默认开启**，因此每条用户消息都会触发 **`POST /api/v1/memories/search`**，并且检索到的记忆会**自动注入**（无需 MCP 工具）。设置 **`POWERMEM_PROMPT_SEARCH=0`** 可关闭此功能。
- 或切换到 **MCP 模式**（`bash scripts/apply-connection-mode.sh mcp`），这样 Claude 可以在需要时调用记忆工具——流量会发送到 **`/mcp`**，而不一定是仪表板 GET 请求的相同路径。
- 或依赖 **VS Code extension** 保存捕获 / `sh hooks/run-hook.sh poll` 进行基于文件的写入。

### 可选：工作区文件监视器（CLI / 无 VS Code） {#optional-workspace-file-watcher-cli--no-vs-code}

如果工程师在**没有使用** [PowerMem VS Code extension](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/) 的情况下使用 **Claude Code**（该扩展已经针对 `powermem.backendUrl` **在保存时自动捕获**），可以运行本地轮询器：
```bash
export POWERMEM_BASE_URL=https://powermem.example.com
export POWERMEM_API_KEY=...   # 如有需要
export POWERMEM_WATCH_ROOT=/path/to/repo
sh hooks/run-hook.sh poll
```
请参阅 [watcher/README.md](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/watcher/README.md) 了解环境变量。

## 使用方法 {#usage}

- **默认模式 (HTTP 模式)：** Hooks 会自动捕获到 REST；聊天中不会出现 PowerMem 工具。**每次提示的语义检索默认开启**（参见 [无缝记录](#seamless-recording-hooks--http-api)）；设置 **`POWERMEM_PROMPT_SEARCH=0`** 可禁用。
- **MCP 模式：** 运行 `apply-connection-mode.sh mcp`，然后 PowerMem 工具会出现；使用 **/memory-powermem:remember** / **recall**，并有真实工具支持。每次提示的注入功能默认**保持开启**；如果只想使用显式的 MCP 工具，请设置 **`POWERMEM_PROMPT_SEARCH=0`**。
- 在 **两种** 模式下，transcript/compact hooks 会写入 REST（`POWERMEM_BASE_URL`，默认 `http://localhost:8848`），而无需模型调用工具。

## 链接 {#links}

- [PowerMem](https://github.com/oceanbase/powermem)
- [PowerMem MCP 文档](../api/0004-mcp.md)
- [Claude Code hooks 参考](https://code.claude.com/docs/en/hooks)
