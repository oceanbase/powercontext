# GitHub Copilot {#github-copilot}

使用 [PowerMem VS Code 扩展](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/) 生成的配置将 GitHub Copilot 连接到 PowerMem。该扩展会写入 `~/.github/copilot/powermem.json`。

## 推荐设置 — 让你的 IDE Agent 进行设置 {#recommended-setup--let-your-ide-agent-set-it-up}

首先下载代码并进入目录：
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
然后在使用 GitHub Copilot 的 IDE 中打开 AI Agent 窗口，并粘贴这一行：
```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```
Agent 遵循 [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md)，优先使用可复用的 `powermem-server` HTTP API 后端，并且仅在 GitHub Copilot 是当前目标时更新其配置。

## 前置条件 {#prerequisites}

- 在编辑器中安装了 GitHub Copilot。
- 一个运行中的 PowerMem 后端，地址为 `http://localhost:8848` 或其他可访问的 URL。
- PowerMem 已配置好你的 LLM 提供商、API 密钥和模型。

## 手动设置 {#manual-setup}

仅当你希望手动连接 GitHub Copilot 时使用此部分。

### 安装 {#install}

在 VS Code 或 Cursor 中安装 PowerMem VS Code 扩展，然后运行 **PowerMem: Setup**。

### 配置 {#configure}

运行 **PowerMem: Link to AI Tools**。

在 MCP 模式下，扩展会写入：
```json
{
  "name": "PowerMem",
  "type": "mcp",
  "mcpServer": {
    "command": "powermem-mcp",
    "args": ["stdio"]
  },
  "url": "http://localhost:8848/mcp"
}
```
在 HTTP 模式下，它会写入：
```json
{
  "name": "PowerMem",
  "type": "context_provider",
  "endpoint": "http://localhost:8848/api/v1/memories/search"
}
```
如果您的 PowerMem 服务器需要认证，扩展会在 HTTP 模式下添加一个 `X-API-Key` 认证头，或者在本地 MCP 中使用 `POWERMEM_API_KEY`。

## 验证 {#verify}

1. 重新加载编辑器或重启 Copilot。
2. 确认 Copilot 已加载 PowerMem 提供程序。
3. 添加一个包含 `PowerMem Copilot probe: dragonfruit-zx9` 的记忆。
4. 让 Copilot 搜索记忆中的 `dragonfruit-zx9`，并确认它检索到了探针。

## 故障排查 {#troubleshooting}

- 如果 Copilot 无法识别 PowerMem，请确认 `~/.github/copilot/powermem.json` 是否存在。
- 如果 MCP 无法运行，请验证 `powermem-mcp stdio` 是否正常工作，或者使用远程 MCP 地址 `http://localhost:8848/mcp`。
- 如果 HTTP 模式因认证错误失败，请重新运行 **PowerMem: Setup** 并设置 API 密钥。

## 卸载 {#uninstall}

删除 `~/.github/copilot/powermem.json`，然后重新加载编辑器。