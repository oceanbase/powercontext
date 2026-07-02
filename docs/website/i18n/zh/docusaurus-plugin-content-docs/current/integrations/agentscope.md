# AgentScope {#agentscope}

通过 AgentScope 的 MCP 客户端支持，将 PowerMem 用作 AgentScope Agents 的长期记忆工具提供者。这种集成方式保持了轻量化：PowerMem 负责记忆的存储和检索，而 AgentScope 通过其 MCP 客户端发现并调用 PowerMem 的 MCP 工具。

AgentScope 支持 HTTP 和 stdio 的 MCP Server，以及有状态和无状态的 MCP 客户端。PowerMem 通过 `powermem-mcp` 提供相同的记忆工具，因此不需要特定于 AgentScope 的存储适配器。

## 前置条件 {#prerequisites}

- 安装了支持 MCP 的 PowerMem：
```bash
pip install "powermem[server]"
```
- 使用您的存储、LLM 和 embedding 提供商配置 PowerMem。
- 在应用环境中安装 AgentScope。

## 启动 PowerMem MCP {#start-powermem-mcp}

使用可流式传输的 HTTP 来运行本地或远程的长时间运行的 AgentScope 进程：
```bash
powermem-mcp streamable-http 8848
```
PowerMem 在以下位置为 MCP 提供服务：
```text
http://localhost:8848/mcp
```
如果您的 AgentScope 进程需要自行启动 PowerMem，也可以使用 stdio：
```bash
powermem-mcp stdio
```
## 在 AgentScope 中连接 PowerMem 工具 {#connect-powermem-tools-in-agentscope}
```python
import asyncio

from agentscope.mcp import HttpMCPConfig, MCPClient


async def list_powermem_tools() -> None:
    powermem = MCPClient(
        name="powermem",
        is_stateful=False,
        mcp_config=HttpMCPConfig(url="http://localhost:8848/mcp"),
    )

    tools = await powermem.list_tools()
    print([tool.name for tool in tools])


asyncio.run(list_powermem_tools())
```
发现的工具包括标准的 PowerMem MCP 记忆操作：

- `add_memory`
- `search_memories`
- `get_memory_by_id`
- `update_memory`
- `delete_memory`
- `delete_all_memories`
- `list_memories`

AgentScope 使用面向模型的名称（例如 `mcp__powermem__search_memories`）封装了 MCP 工具；在调用 `get_tool()` 时，请使用原始的 PowerMem 工具名称。

## 函数级调用 {#function-level-calls}

AgentScope 可以通过名称获取一个可调用的 MCP 工具。当工作流需要显式的记忆读/写步骤时，这非常有用。
```python
import asyncio

from agentscope.mcp import HttpMCPConfig, MCPClient


async def search_powermem() -> None:
    powermem = MCPClient(
        name="powermem",
        is_stateful=False,
        mcp_config=HttpMCPConfig(url="http://localhost:8848/mcp"),
    )

    search_memories = await powermem.get_tool("search_memories")

    result = await search_memories(
        query="dragonfruit-zx9",
        user_id="agentscope-demo-user",
        limit=5,
    )
    print(result)


asyncio.run(search_powermem())
```
## 验证 {#verify}

1. 启动 `powermem-mcp streamable-http 8848`。
2. 运行上面的 AgentScope 代码片段。
3. 确认 `search_memories` 和 `add_memory` 出现在 `list_tools()` 打印的工具名称中。
4. 使用 `add_memory` 添加一个探针记忆，并使用 `search_memories` 搜索相同的 token。

## 疑难解答 {#troubleshooting}

- 如果 AgentScope 无法列出工具，请确认 `http://localhost:8848/mcp` 与 PowerMem MCP 的传输地址和端口匹配。
- 如果记忆调用失败，请首先验证 PowerMem 的 `.env` 文件。MCP Server 依赖于与 Python SDK 相同的存储、LLM 和 Embedding 配置。
- 如果 AgentScope 进程运行在不同的 shell 或容器中，请在其中传递相同的 PowerMem 环境变量，包括启用认证时的 `POWERMEM_API_KEY`。
- 对于 stdio、SSE 和认证的变体，请参考通用的 [MCP 客户端指南](./mcp_client.md)。

## 参考资料 {#references}

- AgentScope MCP 教程: https://doc.agentscope.io/tutorial/task_mcp.html
- PowerMem MCP 指南: ./mcp_client.md