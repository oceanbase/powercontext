# Qoder {#qoder}

通过 Qoder 的 MCP 支持将 Qoder 连接到 PowerMem。PowerMem 的 VS Code 扩展目前尚未自动生成 Qoder 配置，因此需要手动配置此集成。

Qoder MCP 参考文档：

- [Qoder IDE MCP](https://docs.qoder.com/user-guide/chat/model-context-protocol)
- [Qoder CLI MCP servers](https://docs.qoder.com/en/cli/mcp-servers)

## 推荐设置 —— 让 Qoder Agent 进行设置 {#recommended-setup--let-qoder-agent-set-it-up}

首先下载代码并进入目录：
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
然后在 Qoder 中打开 AI Agent 窗口，并粘贴这一行：
```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```
Agent 遵循 [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md)，优先使用可复用的 `powermem-server` HTTP API 后端，并仅通过 MCP 配置 Qoder 以适配当前的 Qoder IDE/CLI 设置。

## 前置条件 {#prerequisites}

- Qoder IDE 或 Qoder CLI。
- 一个正在运行的 PowerMem 后端：
  - `powermem-server --host 0.0.0.0 --port 8848`
  - 或 `powermem-mcp sse`（默认端口 8848）
- PowerMem 已配置好您的 LLM 提供商、API 密钥和模型。

## 手动设置 {#manual-setup}

仅在您希望手动连接 Qoder 时使用此部分。

### 安装 {#install}

如果您希望使用本地 stdio MCP，请安装支持 MCP 的 PowerMem：
```bash
pip install "powermem[server,seekdb]"
```
仅当您的 `.env` 指向非 seekdb 的存储/嵌入提供商时，使用 `powermem[server]`。

然后运行已安装的 MCP 命令：
```bash
powermem-mcp stdio
```
对于远程MCP，启动API服务器：
```bash
powermem-server --host 0.0.0.0 --port 8848
```
### 配置 Qoder IDE {#configure-qoder-ide}

打开 Qoder MCP 设置：

1. 打开 Qoder 设置。
2. 前往 **MCP** 或 **Connectors & MCP**。
3. 添加一个名为 `powermem` 的服务器。

对于远程 HTTP/SSE MCP，粘贴以下 JSON：
```json
{
  "mcpServers": {
    "powermem": {
      "type": "streamable-http",
      "url": "http://localhost:8848/mcp"
    }
  }
}
```
如果您的 Qoder 版本使用 `http` 或 SSE 命名而不是 `streamable-http`，请使用相同的端点，并在 UI 中选择匹配的 HTTP/SSE 传输方式。Qoder 在当前版本中会检测可流式传输的 HTTP 端点。

对于本地 stdio MCP，请使用：
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
如果您的 PowerMem 服务器需要认证，请为 stdio 添加相关的环境变量：
```json
{
  "mcpServers": {
    "powermem": {
      "command": "powermem-mcp",
      "args": ["stdio"],
      "env": {
        "POWERMEM_API_KEY": "<your-api-key>"
      }
    }
  }
}
```
### 配置 Qoder CLI {#configure-qoder-cli}

添加一个用户级别的 stdio MCP Server：
```bash
qodercli mcp add -s user powermem -- powermem-mcp stdio
```
如果 Qoder CLI 已经在运行，请重新加载 MCP Server：
```text
/mcp reload
```
对于 HTTP/SSE 传输，可以使用上面的 Qoder IDE JSON 流程，或者按照 Qoder CLI 的 `qodercli mcp add -t http` 语法操作，具体取决于您安装的版本。Qoder 将 MCP 配置存储在用户范围的 `~/.qoder/settings.json`，本地项目范围的 `.qoder/settings.local.json`，或共享项目范围的 `.mcp.json` 中。

## 验证 {#verify}

1. 在 Qoder 中，打开 MCP Server 列表并确认 `powermem` 已连接。
2. 展开服务器并确认以下工具可用：`add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories` 和 `list_memories`。
3. 添加一个包含 `PowerMem Qoder probe: dragonfruit-zx9` 的记忆。
4. 搜索 `dragonfruit-zx9` 并确认返回了结果。

## 故障排查 {#troubleshooting}

- 如果远程 MCP 无法连接，请验证 `http://localhost:8848/api/v1/system/health` 是否正常，以及 `http://localhost:8848/mcp` 是否可访问。
- 如果 stdio MCP 失败，请验证是否可以从终端启动 `powermem-mcp stdio`。
- 如果工具超时，请增加 Qoder 的 MCP 请求超时时间，或检查 PowerMem 服务器日志以排查 LLM 提取速度慢的问题。
- 如果身份验证失败，请为 stdio 设置 `POWERMEM_API_KEY`，或在 Qoder 的 MCP Server 设置中配置所需的 HTTP 头。

## 卸载 {#uninstall}

从 Qoder 设置中移除 `powermem` MCP Server，或者运行匹配的 Qoder CLI 移除命令以适配您的安装范围。然后重新加载 Qoder 或运行 `/mcp reload`。