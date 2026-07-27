---
title: 接口
description: 在 Codex 插件、CLI、Python SDK、HTTP 和 MCP 之间选择。
---

# 接口

所有远程接口都操作同一个 Server 和同一份持久化 Memory。

| 接口 | 适用场景 | 安装 |
| --- | --- | --- |
| Codex 插件 | 在 Codex 中跨会话恢复和显式维护 Memory | `powercontext setup codex` |
| CLI | 配置、诊断、Server 进程控制和能力检查 | `powercontext[cli,client,server]` |
| Python Client SDK | 对运行中的 Server 发起类型化异步调用 | `powercontext[client]` |
| Core SDK | 进程内 Source、Artifact、Trigger 和组合契约 | 基础包 |
| HTTP | 从任意语言集成服务 | `powercontext[server]` |
| MCP | 面向 Agent 的 Memory 读写工具 | 由 Server 启用 |

## Codex 插件

project-context skill 指导 Codex 何时检索、记忆、修订或停用 Memory。Prompt Hook 会恢复相关条目，并把
用户输入采集为 Source 证据；MCP 工具执行显式操作。插件不会启动或内嵌 Server。

## CLI

```text
powercontext setup codex
powercontext doctor
powercontext server run
powercontext client ready
powercontext client capabilities
powercontext builtin capabilities
```

CLI 只显示已安装 extra 所提供的命令。

## Python Client SDK

由 Server 管理持久化时，使用 Client SDK：

```python
import asyncio

from powercontext.http import RememberMemoryRequest, SearchMemoryRequest
from powercontext.client import PowerContextClient


async def main() -> None:
    async with PowerContextClient("http://127.0.0.1:8000") as client:
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id="project:example",
                kind="decision",
                text="保持公开 API 异步化。",
            )
        )
        result = await client.search_memory(
            SearchMemoryRequest(
                scope_id="project:example",
                query="公开 API",
            )
        )
        print([hit.text for hit in result.hits])


asyncio.run(main())
```

变更操作的响应包含精确 citation。修订、停用或读取不可变条目版本时，应把该 citation 传回 Server。

## Core SDK

基础 `powercontext` 包为自行管理 composition root 的应用导出 Python 协议和模型。它不会替应用选择存储、
调度、传输或推理。需要在同一进程使用随附的 SQLite 或 OceanBase 实现时，安装 `builtin`。

## HTTP 和 MCP

Server 在 `/openapi.json` 提供 OpenAPI 文档，在 `/health/ready` 提供就绪检查，在 `/v1/capabilities`
提供能力信息，并默认在 `/mcp` 提供 Streamable HTTP MCP。HTTP 是完整应用契约，MCP 是面向 Agent 的
Memory 操作子集。
