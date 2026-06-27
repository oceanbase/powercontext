# 通用 MCP 客户端 {#generic-mcp-client}

本指南适用于 Claude Desktop、Cline、Codex、OpenCode、Roo Code、Goose 或任何其他支持标准 MCP Server 定义的 MCP 客户端。

## 前置条件 {#prerequisites}

- 已安装支持 MCP 的 PowerMem。对于零配置的本地 seekdb，请使用 `powermem[server,seekdb]`，或者当您的 `.env` 指向非 seekdb 的存储/嵌入提供商时，使用 `powermem[server]`。
- 已使用您的 LLM 提供商、API 密钥和模型配置 PowerMem。

## 启动 MCP Server {#start-an-mcp-server}

选择一种传输方式。

### Stdio {#stdio}

当客户端可以启动本地命令时，使用 stdio：
```bash
powermem-mcp stdio
```
客户端配置结构：
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
### 可流式的 HTTP {#streamable-http}

对于远程或长时间运行的 MCP，使用可流式的 HTTP：
```bash
powermem-mcp streamable-http 8848
```
客户端配置结构：
```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```
### SSE {#sse}

当客户端明确需要使用 SSE 时，请使用 SSE：
```bash
powermem-mcp sse 8848
```
使用相同的 URL 结构：
```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```
OpenCode 将相同的服务器存储在 `mcp.powermem` 下，而不是 `mcpServers.powermem`；具体的 JSON 结构请参见 [OpenCode](./opencode.md)。

## 暴露的工具 {#exposed-tools}

PowerMem 暴露了核心记忆工具：

- `add_memory`
- `search_memories`
- `get_memory_by_id`
- `update_memory`
- `delete_memory`
- `delete_all_memories`
- `list_memories`

当客户端支持时，还会暴露用户配置工具。

## 验证 {#verify}

1. 重新加载 MCP 客户端。
2. 确认已连接 `powermem` 服务器。
3. 确认上述工具已列出。
4. 添加并搜索包含 `dragonfruit-zx9` 的测试记忆。

## 故障排查 {#troubleshooting}

- 如果 stdio 失败，请确认 `powermem-mcp stdio` 从安装 PowerMem 的同一环境启动。
- 如果远程 MCP 失败，请确认 URL 和端口与您启动的传输匹配。
- 如果启用了认证，请通过环境变量传递 `POWERMEM_API_KEY` 用于 stdio，或为远程 MCP 配置请求头。
- 如果记忆操作失败，请检查 `.env`、LLM 提供商、Embedding 提供商和服务器日志。

## 卸载 {#uninstall}

仅从客户端配置中移除 `powermem` MCP Server 条目，然后重新加载客户端。