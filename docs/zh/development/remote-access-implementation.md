# 当前远程访问实现

本文档端到端说明当前远程访问实现，从 OpenAPI contract 和 generated transport code 开始，依次覆盖 FastAPI
Server、Python Client、CLI provider 和 FastMCP 投影。它记录当前行为，不描述 RFC 提议的目标架构。

受版本控制的 [`openapi/powercontext.yaml`](../../../openapi/powercontext.yaml) 是 HTTP contract 的规范来源。
[Python API 参考](../modules.md)由 package 中的 public module 生成。

## 实现流程

```text
Core Protocol models
        |
        | explicit mapping when semantics match
        v
openapi/powercontext.yaml
        |
        v
generated models, operations, and schema
        |                         |
        v                         v
FastAPI Server              Python Client
        |
        v
FastMCP projection

Client and Server command providers -> Typer CLI shell
```

OpenAPI 持有 Server 和 Client code 共用的 HTTP shape。Generated model 保持为 transport type。只有现有 Core
Protocol model 具备相同语义时，才通过显式 boundary mapping 复用。

Server assembly 位于 `powercontext.server`，MCP assembly 位于 `powercontext.mcp`。CLI shell 发现 component 持有
的 command group，不自行维护这些 command。

## 已实现的功能面

| Component | 当前行为 |
| --- | --- |
| HTTP Server | Liveness、readiness、capability discovery 和 request ID |
| Python Client | 对应三个 HTTP operation 的同步 method |
| CLI | 通用 command-provider shell，以及 Client 和 Server command group |
| MCP | 基于 assembled Server、使用 allow-list 的 `get_capabilities` tool |
| Runtime | 当前没有绑定 application service |

默认 capability set 为空。没有 Runtime binding 时，Server 不会声称支持 Source processing、Artifact generation、
retrieval、persistence 或 scheduling。

## HTTP contract

| Method | Path | Response |
| --- | --- | --- |
| `GET` | `/health/live` | `HealthResponse` |
| `GET` | `/health/ready` | `ReadinessResponse` |
| `GET` | `/v1/capabilities` | `Capabilities` |

每个 response 都包含 `X-Request-ID`。必要 binding 尚未 ready 时，readiness endpoint 返回 `503 Service
Unavailable`。组装 FastAPI application 时可以提供 readiness probe 和 capability provider。

`ServerSettings` 读取 `POWERCONTEXT_SERVER_HOST` 和 `POWERCONTEXT_SERVER_PORT`，默认值分别为
`127.0.0.1` 和 `8000`。

## Python Client

`PowerContextClient` 提供 `get_liveness()`、`get_readiness()` 和 `get_capabilities()`。该 facade 是同步接口，并
根据 OpenAPI-derived model 验证成功 response。

Client failure 使用以下稳定 exception class：

- 没有收到有效 HTTP response 时使用 `TransportError`；
- HTTP status 为 non-success 时使用 `ServerResponseError`；
- 成功 response 不符合 transport model 时使用 `InvalidResponseError`。

## CLI

顶层 Typer shell 发现已安装 component 提供的 command group。它持有 global help 和 version handling，但不定义
component command。

当前 command group 提供：

```text
powercontext client live
powercontext client ready
powercontext client capabilities
powercontext server run
```

Client command 接受 `--server-url`、`--timeout` 和 `--json`。Server command 接受 `--host` 和 `--port`。

## MCP

FastMCP 从 assembled FastAPI application 投影选定的 operation。当前 route map 只把 `get_capabilities` 暴露为
tool，不暴露 liveness、readiness、resource、prompt 或 Runtime tool。

Streamable HTTP endpoint 挂载在 `/mcp/`。本地 process 可以通过以下命令启动组合后的 application：

```shell
uvicorn powercontext.mcp:create_mcp_app --factory
```

增加 HTTP endpoint 不会使它自动暴露到 MCP。每个 MCP primitive 都需要显式选择 route。

## Contract workflow

修改 generated transport model 前，应先修改 `openapi/powercontext.yaml`。当 Core Protocol model 与 wire type
语义相同时，复用或映射该 model。Transport-only metadata 保留在 Core 之外。

Contract 变更后运行：

```shell
make api-generate
make contract-test
make unit-test
make e2e-test
```

Test 应验证 generated output 和 public behavior，不应依赖偶然形成的 packaging internal 或 generated source
layout。

## 尚未实现

当前远程功能面没有定义 Source capture、Artifact Revision read、context retrieval、Memory generation、durable
Operation、persistence、authentication 或非 Python SDK。这些功能需要已经接受的 Runtime application-service
boundary 或单独设计，之后才能成为 remote contract。
