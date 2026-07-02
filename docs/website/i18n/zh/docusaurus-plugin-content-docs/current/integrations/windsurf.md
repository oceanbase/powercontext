# Windsurf {#windsurf}

通过 [PowerMem VS Code 扩展](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/) 生成的配置将 Windsurf 连接到 PowerMem。该扩展会写入 `~/.windsurf/context/powermem.json`。

## 推荐设置 —— 让 Windsurf Agent 进行设置 {#recommended-setup--let-windsurf-agent-set-it-up}

首先下载代码并进入目录：
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
然后在 Windsurf 中打开 AI Agent 窗口，并粘贴这一行：
```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```
Agent 遵循 [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md)，优先使用可复用的 `powermem-server` HTTP API 后端，并且仅在 Windsurf 是当前目标时更新 Windsurf 的配置。

## 前置条件 {#prerequisites}

- 已安装 Windsurf。
- PowerMem 后端运行在 `http://localhost:8848` 或其他可访问的 URL。
- PowerMem 已配置好您的 LLM 提供商、API 密钥和模型。

## 手动设置 {#manual-setup}

仅当您希望手动连接 Windsurf 时使用此部分。

### 安装 {#install}

在 VS Code 或 Cursor 中安装 PowerMem VS Code 扩展，然后运行 **PowerMem: Setup** 并设置后端 URL。

### 配置 {#configure}

运行 **PowerMem: Link to AI Tools**。

在 MCP 模式下，扩展会写入：
```json
{
  "contextProvider": "powermem-mcp",
  "mcp": {
    "url": "http://localhost:8848/mcp"
  }
}
```
如果您配置了本地 MCP 路径，它会写入：
```json
{
  "contextProvider": "powermem-mcp",
  "mcp": {
    "configPath": "uvx"
  }
}
```
在 HTTP 模式下，它写入：
```json
{
  "contextProvider": "powermem",
  "api": "http://localhost:8848/api/v1/memories/search"
}
```
如果您的服务器需要认证，该扩展会添加 `apiKey`。

## 验证 {#verify}

1. 重启 Windsurf。
2. 确认 PowerMem 上下文提供器已加载。
3. 添加一个包含 `PowerMem Windsurf probe: dragonfruit-zx9` 的记忆。
4. 从 Windsurf 搜索 `dragonfruit-zx9`，并确认返回了结果。

## 故障排查 {#troubleshooting}

- 如果 Windsurf 未加载配置，请检查 `~/.windsurf/context/powermem.json` 是否存在且为有效的 JSON。
- 如果 MCP 模式失败，请验证后端是否暴露了 `/mcp`。
- 如果本地 MCP 模式失败，请验证配置的命令是否在 `PATH` 中可用。

## 卸载 {#uninstall}

删除 `~/.windsurf/context/powermem.json`，然后重启 Windsurf。