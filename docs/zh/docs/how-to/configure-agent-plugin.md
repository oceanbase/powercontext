---
title: 配置 Agent Plugin
description: 在兼容 Agent 中加载可复用的 PowerContext skills 和 MCP configuration。
---

# 配置 Agent Plugin

PowerContext 提供一个可移植的 Agent Plugin package，供能够加载 Agent
Plugin skills 和 MCP configuration 的 Agent 使用。

先 clone 仓库，或使用一个已经包含集成 package 的源码 checkout：

```bash
git clone https://github.com/oceanbase/powercontext.git
cd powercontext
```

package root 为：

```text
integrations/agent-plugin/powercontext/
```

它包含：

- `plugin.json`：可移植 Agent Plugin metadata。
- `mcp.json`：指向 PowerContext Streamable HTTP endpoint 的 MCP
  configuration。
- `skills/project-context/SKILL.md`：用于 Memory 和 Handoff 工作流的可复用指令。

加载 package 前，先启动 PowerContext Server：

```bash
uv run powercontext server run
```

该 package 默认让兼容 Agent 连接：

```text
http://127.0.0.1:8000/mcp
```

## 在 VS Code 中加载

VS Code 支持通过 `chat.pluginLocations` 加载本地 Agent Plugin 目录。可以用
下面的步骤验证这个 package 能被真实 Agent Plugin host 加载：

1. 打开 VS Code 的 JSON settings。
2. 启用 Agent Plugins，并注册 PowerContext package root：

   ```json
   {
     "chat.plugins.enabled": true,
     "chat.pluginLocations": {
       "/absolute/path/to/cloned/powercontext/integrations/agent-plugin/powercontext": true
     }
   }
   ```

3. 重新加载 VS Code。
4. 确认 PowerContext plugin 出现在 Agent Plugins view 中，并且 chat 可以使用
   `project-context` skill 和 `powercontext` MCP server。

注册路径必须指向包含 `plugin.json`、`mcp.json` 和 `skills/` 的 package root。

该 package 不负责启动 Server，不新增 MCP tools，也不实现 Runtime 或 Memory
行为。Memory search、writes、revisions、Handoff behavior 和 persistence 仍由
PowerContext Server 及其现有 MCP tools 负责。

认证由加载该 package 的 Agent 或 client 管理。Agent Plugins 1.0.0 不定义远程
MCP server 的可移植 credential-reference 字段，因此仓库中的 `mcp.json` 不包含静态
credentials 或 token placeholders。不要把 bearer token 写入 `mcp.json`。

当某个 Agent 支持 Agent Plugin skills 和 MCP configuration，并且你只需要显式的
Memory 与 Handoff 操作而不是专属 integration 时，可以使用这个 package。
