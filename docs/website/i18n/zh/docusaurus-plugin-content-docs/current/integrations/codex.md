# Codex {#codex}

通过 MCP 将 Codex 连接到 PowerMem。推荐的设置路径是通用的[PowerMem MCP 客户端设置](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/SETUP.md)。

## 推荐设置 —— 让您的 MCP 客户端 Agent 完成设置 {#recommended-setup--let-your-mcp-client-agent-set-it-up}

首先下载代码并进入目录：
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
然后打开运行 Codex 的 AI Agent 窗口，并粘贴以下这一行：
```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```
Agent 遵循 [`apps/mcp-client/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/SETUP.md)，直接运行 `powermem-mcp`，并仅更新 Codex MCP 配置。

## 前置条件 {#prerequisites}

- 已安装 Codex 并能够读取 `~/.codex/context.json`。
- 一个正在运行的 PowerMem MCP endpoint 或本地 `powermem-mcp` 命令。
- PowerMem 已配置好您的 LLM 提供商、API 密钥和模型。

## 手动设置 {#manual-setup}

仅在您希望手动连接 Codex 时使用此部分。

### 配置 {#configure}

将 PowerMem 添加到 `~/.codex/context.json`：
```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```
如果 PowerMem MCP 端点需要认证，添加匹配的头信息或将 `POWERMEM_API_KEY` 传递给一个 stdio MCP 命令。

## 验证 {#verify}

1. 重启 Codex，使其重新加载 `~/.codex/context.json`。
2. 确认 `powermem` MCP Server 已列出。
3. 添加一个内容为 `PowerMem Codex probe: dragonfruit-zx9` 的探测记忆。
4. 搜索 `dragonfruit-zx9` 并确认 Codex 收到了结果。

## 故障排查 {#troubleshooting}

- 如果 Codex 忽略了配置，请验证 `~/.codex/context.json` 是否为有效的 JSON。
- 如果 MCP 失败，请确认 `http://localhost:8848/mcp` 是否可访问，或者切换到 stdio MCP。

## 卸载 {#uninstall}

从 `~/.codex/context.json` 中移除 `mcpServers.powermem`。保留其他提供者不变。有关 Agent 引导的清理，请参考 [`apps/mcp-client/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/UNINSTALL.md)。