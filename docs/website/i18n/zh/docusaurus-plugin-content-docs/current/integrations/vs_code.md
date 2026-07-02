# VS Code {#vs-code}

使用官方的 [PowerMem VS Code 扩展](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/) 将 VS Code 连接到 PowerMem，从编辑器中查询记忆、保存选中文本，并链接其他 AI 工具。

## 推荐设置 — 让 VS Code Agent 进行设置 {#recommended-setup--let-vs-code-agent-set-it-up}

首先下载代码并进入目录：
```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```
然后在 VS Code 中打开 AI Agent 窗口，并粘贴这一行：
```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```
Agent 遵循 [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md)，优先复用或启动 HTTP API 后端，仅在 HTTP 无法正常运行时回退到仅 MCP 模式。

## 前置条件 {#prerequisites}

- VS Code 1.104 或更新版本。
- 一个正在运行的 PowerMem 后端：
  - `powermem-server --host 0.0.0.0 --port 8848` 用于 HTTP API + MCP。
  - `powermem-mcp sse`（端口 8848）当你只需要 MCP 时。
- 一个已配置的 PowerMem `.env` 文件或等效的环境变量。在启动后端之前，设置你的 LLM 提供商、API 密钥和模型。

安装 `powermem[server]` 以满足 HTTP API 服务器和 MCP 运行时依赖项的需求；如果使用默认的嵌入式 seekdb 存储/嵌入器，还需添加 `seekdb`。

## 手动设置 {#manual-setup}

仅在你希望手动配置 VS Code 时使用此部分。

### 安装 {#install}

从源码安装：
```bash
cd apps/vscode-extension
npm install
npm run compile
```
然后在 VS Code 中按下 `F5`，启动 Extension Development Host。

从打包的 `.vsix`：
```bash
code --install-extension powermem-vscode-*.vsix
```
### 配置 {#configure}

1. 打开命令面板。
2. 运行 **PowerMem: Setup**。
3. 将 **Backend URL** 设置为 `http://localhost:8848` 或您的远程 PowerMem 服务器。
4. 仅当服务器需要 `X-API-Key` 时设置 **API key**。
5. 将 **Connection Mode** 保持为 `mcp`，除非您需要仅支持 HTTP 的上下文集成。
6. 运行 **Test connection**。

完整的 Agent 友好型设置提示请参阅 [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md)。

### 使用 {#use}

- **PowerMem: Query Memories** 用于搜索已保存的记忆。
- **PowerMem: Add Selection to Memory** 用于保存高亮文本。
- **PowerMem: Quick Note** 用于保存简短笔记。
- **PowerMem: Dashboard** 打开扩展仪表板。
- **PowerMem: Link to AI Tools** 用于为 Cursor、Claude、Windsurf 和 GitHub Copilot 写入支持的客户端配置。

## 验证 {#verify}

1. 确认状态栏显示 **PowerMem** 且没有警告图标。
2. 运行 **PowerMem: Quick Note** 并保存 `PowerMem VS Code probe: dragonfruit-zx9`。
3. 运行 **PowerMem: Query Memories** 并搜索 `dragonfruit-zx9`。
4. 确认探针出现在结果中。

## 故障排除 {#troubleshooting}

- 如果状态栏显示未连接，请验证 `powermem.backendUrl` 和后端健康检查端点。
- 如果搜索无结果，请检查服务器日志并确认使用了相同的 `userId` scope。
- 如果链接的客户端未检测到 PowerMem，请重新运行 **PowerMem: Link to AI Tools** 并重新加载目标客户端。

## 卸载 {#uninstall}

请参阅 [`apps/vscode-extension/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/UNINSTALL.md)。