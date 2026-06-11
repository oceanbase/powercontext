# AgentScope

Use PowerMem as a long-term memory tool provider for AgentScope agents through
AgentScope's MCP client support. This keeps the integration lightweight:
PowerMem owns memory storage and retrieval, while AgentScope discovers and calls
PowerMem MCP tools through its MCP client.

AgentScope supports HTTP and stdio MCP servers, stateful and stateless MCP
clients. PowerMem exposes the same memory tools through `powermem-mcp`, so no
AgentScope-specific storage adapter is required.

## Prerequisites

- PowerMem installed with MCP support:

```bash
pip install "powermem[mcp]"
```

- PowerMem configured with your storage, LLM, and embedding providers.
- AgentScope installed in the application environment.

## Start PowerMem MCP

Use streamable HTTP for a long-running local or remote AgentScope process:

```bash
powermem-mcp streamable-http 8848
```

PowerMem serves MCP at:

```text
http://localhost:8848/mcp
```

If your AgentScope process should launch PowerMem itself, stdio is also
available:

```bash
powermem-mcp stdio
```

## Connect PowerMem tools in AgentScope

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

The discovered tools include the standard PowerMem MCP memory operations:

- `add_memory`
- `search_memories`
- `get_memory_by_id`
- `update_memory`
- `delete_memory`
- `delete_all_memories`
- `list_memories`

AgentScope wraps MCP tools with a model-facing name such as
`mcp__powermem__search_memories`; use the original PowerMem tool name when
calling `get_tool()`.

## Function-level calls

AgentScope can obtain a single callable MCP tool by name. This is useful when a
workflow wants explicit memory read/write steps.

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

## Verify

1. Start `powermem-mcp streamable-http 8848`.
2. Run the AgentScope snippet above.
3. Confirm `search_memories` and `add_memory` appear in
   the tool names printed by `list_tools()`.
4. Add a probe memory with `add_memory` and search for the same token with
   `search_memories`.

## Troubleshooting

- If AgentScope cannot list tools, confirm `http://localhost:8848/mcp` matches
  the PowerMem MCP transport and port.
- If memory calls fail, validate the PowerMem `.env` first. The MCP server
  depends on the same storage, LLM, and embedding configuration as the Python SDK.
- If the AgentScope process runs in a different shell or container, pass the same
  PowerMem environment variables there, including `POWERMEM_API_KEY` when auth is
  enabled.
- Use the generic [MCP client guide](./mcp_client.md) for stdio, SSE, and auth
  variations.

## References

- AgentScope MCP tutorial: https://doc.agentscope.io/tutorial/task_mcp.html
- PowerMem MCP guide: ./mcp_client.md
