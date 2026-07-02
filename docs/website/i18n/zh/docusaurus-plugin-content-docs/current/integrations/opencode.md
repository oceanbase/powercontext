# OpenCode {#opencode}

通过 OpenCode 的 MCP 支持将 OpenCode 连接到 PowerMem。

OpenCode MCP 参考资料：

- [OpenCode MCP servers](https://opencode.ai/docs/mcp-servers/)
- [OpenCode config](https://opencode.ai/docs/config/)

## 推荐设置 —— 让你的 MCP 客户端 Agent 进行设置 {#recommended-setup--let-your-mcp-client-agent-set-it-up}

首先下载代码并进入目录：
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
然后在 OpenCode 中打开 AI Agent 窗口，并粘贴这一行：
```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```
Agent 遵循 [`apps/mcp-client/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/SETUP.md)，直接运行 `powermem-mcp`，并仅配置当前的 OpenCode 设置。

## 前置条件 {#prerequisites}

- 已安装 OpenCode。
- 一个 PowerMem 后端：
  - 如果需要 PowerMem UI/API 功能，推荐使用：`powermem-server --host 0.0.0.0 --port 8848`
  - 如果需要 OpenCode MCP 工具，推荐使用：`powermem-mcp stdio` 或 `powermem-mcp streamable-http 8848`
- PowerMem 已配置好您的 LLM 提供商、API 密钥和模型。

安装 `powermem[server]` 以支持 HTTP API 服务器和 MCP 运行时依赖项；如果使用默认的嵌入式 seekdb 存储/嵌入器，请添加 `seekdb`。

## 手动设置 {#manual-setup}

仅在您希望手动连接 OpenCode 时使用此部分。

### 选择配置范围 {#choose-a-config-scope}

OpenCode 从 `opencode.json` 中读取 MCP 配置。常见位置如下：

| 范围 | 文件 |
|------|------|
| 全局用户配置 | `~/.config/opencode/opencode.json` |
| 项目配置 | `opencode.json` |
| 项目本地配置 | `.opencode/opencode.json` |

如果是个人 PowerMem 设置，使用全局配置。仅当团队希望共享 MCP Server 定义时，使用项目配置。

### 本地 stdio MCP {#local-stdio-mcp}

对于本地使用，在 `mcp` 对象下添加 PowerMem：
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "local",
      "command": ["powermem-mcp", "stdio"],
      "enabled": true
    }
  }
}
```
如果您的 PowerMem 服务器需要认证，请传递环境变量：
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "local",
      "command": ["powermem-mcp", "stdio"],
      "enabled": true,
      "environment": {
        "POWERMEM_API_KEY": "<your-api-key>"
      }
    }
  }
}
```
### 远程 MCP {#remote-mcp}

启动一个可流式传输的 HTTP MCP 端点：
```bash
powermem-mcp streamable-http 8848
```
然后配置 OpenCode：
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "remote",
      "url": "http://localhost:8848/mcp",
      "enabled": true
    }
  }
}
```
如果你在远程 URL 后面暴露了 PowerMem MCP，请将 `http://localhost:8848/mcp` 替换为该 URL，并在需要时添加 headers。

## 验证 {#verify}

1. 重启 OpenCode 或重新加载 MCP Server。
2. 确认 `powermem` 被列为启用的 MCP Server。
3. 确认诸如 `add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories` 和 `list_memories` 等工具可见。
4. 添加一个包含 `PowerMem OpenCode probe: dragonfruit-zx9` 的记忆。
5. 搜索 `dragonfruit-zx9` 并确认返回了结果。

## 故障排查 {#troubleshooting}

- 如果本地 MCP 失败，在终端运行 `powermem-mcp stdio` 并修复缺失的依赖或 `.env` 值。
- 如果远程 MCP 失败，验证 `powermem-mcp streamable-http 8848` 是否正在运行，并确保 `http://localhost:8848/mcp` 可以从 OpenCode 访问。
- 如果工具超时，检查 PowerMem 服务器日志，并考虑增加 OpenCode MCP 的超时时间。
- 如果认证失败，为本地 MCP 设置 `POWERMEM_API_KEY` 或配置远程 MCP headers。

## 卸载 {#uninstall}

从你编辑的 OpenCode 配置文件中移除 `mcp.powermem` 条目，然后重启 OpenCode 或重新加载 MCP Server。对于 Agent 引导的清理，请参考 [`apps/mcp-client/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/UNINSTALL.md)。