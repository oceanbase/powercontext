# Cline {#cline}

通过 MCP 将 [Cline](https://github.com/cline/cline) 连接到 PowerMem。

## 前置条件 {#prerequisites}

- 在 VS Code 或兼容的编辑器中安装了 Cline。
- PowerMem 已配置好您的 LLM 提供商、API 密钥和模型。
- 一个 PowerMem MCP 端点：
  - 本地 stdio: `powermem-mcp stdio`
  - 远程 MCP: `powermem-mcp streamable-http 8848` 或 `powermem-mcp sse 8848`

## 推荐设置 {#recommended-setup}

对于 Cline，使用通用的 MCP 客户端设置：
```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```
当 Cline 是目标客户端时，仅需配置 Cline 的 MCP Server 条目。

## 手动设置 {#manual-setup}

在 Cline 的 MCP 设置中添加一个 PowerMem MCP Server。

对于本地 stdio MCP：
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
对于远程 MCP：
```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```
如果您的 PowerMem 服务器需要认证，请在 stdio 环境中传递 `POWERMEM_API_KEY`，或者在 Cline 中配置所需的远程请求头。

## 验证 {#verify}

1. 重新加载 Cline MCP Server。
2. 确认 `powermem` 已连接。
3. 确认诸如 `add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories` 和 `list_memories` 等工具可见。
4. 添加并搜索包含 `dragonfruit-zx9` 的测试记忆。

## 故障排查 {#troubleshooting}

- 如果 stdio MCP 失败，请在终端中运行 `powermem-mcp stdio`。
- 如果远程 MCP 失败，请确认 `http://localhost:8848/mcp` 可以从 Cline 访问。
- 如果工具超时，请检查 PowerMem 日志以及您的 LLM/embedding 配置。

## 卸载 {#uninstall}

仅从 Cline 的 MCP 设置中移除 `mcpServers.powermem` 条目，然后重新加载 Cline。