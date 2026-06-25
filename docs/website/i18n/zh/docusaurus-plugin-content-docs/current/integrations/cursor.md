# Cursor {#cursor}

通过 MCP 将 Cursor 连接到 PowerMem。推荐的路径是 [PowerMem VS Code 扩展](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/)，它运行在 Cursor 中，并为您写入 `~/.cursor/mcp.json`。

## 推荐设置 — 让 Cursor Agent 进行设置 {#recommended-setup--let-cursor-agent-set-it-up}

首先下载代码并进入目录：
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
然后在 Cursor 中打开 AI Agent 窗口，并粘贴这一行：
```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```
Agent 遵循 [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md)，首先复用或启动 `powermem-server`，并仅为当前 IDE 配置 Cursor 的 MCP 入口。

## 前置条件 {#prerequisites}

- 启用了 MCP 支持的 Cursor。
- 一个正在运行的 PowerMem 后端：
  - `powermem-server --host 0.0.0.0 --port 8848`
  - 或 `powermem-mcp sse`（默认端口 8848；等同于 `powermem-mcp sse 8848`）
- PowerMem 已配置好您的 LLM 提供商、API 密钥和模型。

安装 `powermem[server]` 以满足 HTTP API 服务器和 MCP 运行时依赖项的需求，并在使用默认的嵌入式 seekdb 存储/嵌入器时添加 `seekdb`。

## 手动设置 {#manual-setup}

仅在您希望手动连接 Cursor 时使用此部分。

### 安装 {#install}

在 Cursor 中安装 PowerMem VS Code 扩展：

1. 打开 Cursor 扩展。
2. 安装打包的 PowerMem `.vsix`，或从源码运行扩展。
3. 运行 **PowerMem: Setup** 并设置后端 URL。

完整的设置清单请参阅 [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md)。

### 配置 {#configure}

运行 **PowerMem: Link to AI Tools**。扩展会将以下条目合并到 `~/.cursor/mcp.json` 中：
```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```
如果您更喜欢使用 stdio MCP，请在 **PowerMem: Setup** 中设置 **MCP server path**，然后重新运行 **Link to AI Tools**。扩展会写入：
```json
{
  "mcpServers": {
    "powermem": {
      "command": "powermem-mcp",
      "args": ["stdio"]
    }
  }
}
```
## 验证 {#verify}

1. 重新加载 Cursor。
2. 打开 Cursor MCP 设置，确认 `powermem` 已列出。
3. 让 Cursor 列出可用的 MCP 工具；PowerMem 应该提供以下功能：`add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories` 和 `list_memories`。
4. 运行一个小的回路测试：添加一个包含 `dragonfruit-zx9` 的记忆，然后搜索 `dragonfruit-zx9`。

## 故障排查 {#troubleshooting}

- 如果 Cursor 无法连接，请确认 `http://localhost:8848/api/v1/system/health` 状态正常。
- 如果 `~/.cursor/mcp.json` 已存在，扩展会合并 `mcpServers.powermem` 并保留其他服务器。
- 如果 stdio 模式失败，请确认 `powermem-mcp stdio` 能在终端中运行。

## 卸载 {#uninstall}

仅移除 `~/.cursor/mcp.json` 中的 `mcpServers.powermem` 条目，然后重新加载 Cursor。要卸载扩展本身，请参考 [`apps/vscode-extension/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/UNINSTALL.md)。