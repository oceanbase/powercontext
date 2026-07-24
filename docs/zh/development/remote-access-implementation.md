# 当前远程访问实现

本文档端到端说明当前远程访问实现，从 OpenAPI contract 和 generated transport code 开始，依次覆盖 FastAPI
Server、Python Client、CLI provider 和 FastMCP 投影。它记录当前行为，不描述 RFC 提议的目标架构。

受版本控制的 [`openapi/powercontext.yaml`](../../../openapi/powercontext.yaml) 是 HTTP contract 的规范来源。
[Python API 参考](../modules.md)由 package 中的 public module 生成。

## 实现流程

```text
openapi/powercontext.yaml
        |
        v
generated models, operations, and schema
        |                    |
        v                    v
FastAPI adapters       Python Client
        |
        v
PowerContextRuntime -> scoped PowerContext
        |
        v
FastMCP allow-list

Client and Server command providers -> Typer CLI shell
```

OpenAPI 持有 Server 和 Client code 共用的 HTTP shape。Generated Pydantic model 对 transport value 进行严格
校验。Core dataclass 的 constructor 不执行 OpenAPI field constraint，因此 object schema 使用 generated
transport model，并在 adapter 中显式映射。只有 validation semantics 完全相同的封闭 Core literal alias 才直接
复用。

生产入口 `create_server_app()` 在 FastAPI lifespan 中打开 `PowerContextRuntime`，在 shutdown 时关闭，并始终
挂载 MCP。HTTP 和 MCP 使用同一个 Runtime instance。底层 `create_app()` 只绑定 HTTP adapter，不消费进程
配置。CLI shell 发现 component 持有的 command group，不自行维护这些 command。

## 已实现的功能面

| Component | 当前行为 |
| --- | --- |
| HTTP Server | Runtime-backed Source 和 Memory operation、readiness、capability 与 request ID |
| HTTP contract | Source capture 与 Memory family command/query |
| Python Client | Health、Source capture 和 Memory operation 的同步 typed method |
| CLI | 通用 command-provider shell，以及 Client 和 Server command group |
| MCP | 挂载在同一 Server 上的 Memory tool allow-list |
| Runtime | SQLite Source journal、Memory storage、cursor 与可选持久化 scheduler |

Production factory 持有 Runtime lifecycle。较低层的 `create_app()` 仍用于 contract 和 adapter test；没有
binding 时会报告 `not_ready`。

## HTTP contract

| Method | Path | Response |
| --- | --- | --- |
| `GET` | `/health/live` | `HealthResponse` |
| `GET` | `/health/ready` | `ReadinessResponse` |
| `GET` | `/v1/capabilities` | `Capabilities` |
| `POST` | `/v1/sources/content` | `CaptureContentSourceResponse` |
| `POST` | `/v1/memory/flush` | `FlushMemoryResponse` |
| `POST` | `/v1/memory/remember` | `MemoryMutationResponse` |
| `POST` | `/v1/memory/search` | `SearchMemoryResponse` |
| `POST` | `/v1/memory/entries/list` | `ListMemoryEntriesResponse` |
| `POST` | `/v1/memory/entries/get` | `MemoryEntry` |
| `POST` | `/v1/memory/entries/revise` | `MemoryMutationResponse` |
| `POST` | `/v1/memory/entries/retire` | `MemoryMutationResponse` |
| `POST` | `/v1/memory/changes` | `ListMemoryChangesResponse` |

每个 response 都包含 `X-Request-ID`。必要 binding 尚未 ready 时，readiness endpoint 返回 `503 Service
Unavailable`。底层 adapter factory 允许显式 application assembly 提供 readiness probe 和 capability
provider。Production Server 和 MCP factory 从其持有的 Runtime 派生这两项信息，并拒绝脱离 Runtime 的
override。

已知 domain failure 会在 response 经过 request ID middleware 返回前完成映射。Memory value 缺失返回
`404`，Source、Revision 或 inactive entry conflict 返回 `409`，无效 transport 或 Runtime request 返回
`422`，Runtime 或 inference dependency 不可用返回 `503`。只有未知 failure 返回 `500 internal_error`。

Artifact family operation 使用 family-specific prefix。Memory operation 位于 `/v1/memory/`，Content capture
位于 `/v1/sources/`。Capture 返回 `202 Accepted`，表示 Source 已持久化，不承诺 scheduler 已执行。

`ServerSettings` 将进程配置分为 `http`、`mcp`、`runtime`、`storage` 和 `inference`。环境变量使用带
`POWERCONTEXT_SERVER_` prefix 的显式扁平名称，例如 `POWERCONTEXT_SERVER_STORAGE_PATH` 和
`POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL`。Pydantic Settings 只在 Server 配置组边界拆分，
`generation_model` 等字段名保持完整。Provider credential 仍由选定的 model provider 持有。

配置 `inference.generation_model` 后，composition root 会构造 `LLMMemoryCandidatePipeline`。对应 evidence projector
会向 Memory extraction schema 暴露 `ContentSource` 的 text 和 metadata。没有 generation model 时，capture
和显式 Memory operation 仍可使用，flush 会报告 extraction 不受支持。

`inference.embedding_*` 字段包含 embedding model、profile ID、dimension、normalization 和 timeout；
`storage.vec1_extension` 仍属于 SQLite profile。Composition root 要求两者同时存在，构造 Pydantic AI
embedding adapter，并将其与匹配的 `EmbeddingProfile` 传给 Runtime。`/v1/capabilities` 从初始化成功的
Runtime 和 backend 报告 extraction 与 search behavior，不暴露 storage path、model name、Source window size
或 inference budget。

### 使用 OpenAI-compatible endpoint 和 Vec1 启动

仓库为固定版本的[官方 Vec1 0.7 source](https://sqlite.org/vec1/doc/trunk/doc/vec1.md)提供 Linux installer：

```shell
make vec1-install
```

该脚本下载官方 Vec1 source 与 SQLite header，校验 SHA-256，编译 portable loadable extension，并通过
PowerContext 使用的 APSW 进行实际探测。生成的 `.powercontext/vec1.so` 是本地构建产物，不进入版本控制。

复制示例环境配置，并替换 model name、embedding dimension、endpoint 和 credential：

```shell
cp .env.example .env
set -a
. ./.env
set +a
powercontext server run
```

`ServerSettings` 不自行读取 `.env`；必须由 shell 或 process supervisor 导出这些值。通用 Chat
Completions-compatible generation endpoint 使用 `openai-chat:` model prefix，embedding 使用 `openai:`
prefix。示例假设 generation 与 embedding 共用 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`。

## Python Client

`PowerContextClient` 是同步 facade，根据 OpenAPI-derived model 验证成功 response。它提供 health 与
capability discovery，以及 Source capture、flush、remember、search、entry list/get/revise/retire 和 change
query。

Memory response 使用 `ArtifactReference` 表示精确 Revision。Entry response 与 search hit 携带嵌套
`MemoryCitation`，调用方可以直接将该 citation 传给 get、revise 或 retire request。

Client failure 使用以下稳定 exception class：

- 没有收到有效 HTTP response 时使用 `TransportError`；
- HTTP status 为 non-success 时使用 `ServerResponseError`，并保留稳定 error code 和 request ID；
- 成功 response 不符合 transport model 时使用 `InvalidResponseError`。

`ClientSettings` 通过 `POWERCONTEXT_CLIENT_` prefix 读取 Server URL 和 timeout。Command-line option 覆盖这些
设置。

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

Client command 接受 `--server-url`、`--timeout` 和 `--json`。Server command 从 `ServerSettings` 读取 process
configuration；可选的 `--host` 和 `--port` 作为 init source，对 environment source 做局部覆盖。

## MCP

FastMCP 从 assembled FastAPI application 投影选定的 operation。Route map 暴露 search、list、exact get、
remember、revise 和 retire；不暴露 Source capture、flush、changes、health、readiness 或 capabilities。

Streamable HTTP endpoint 挂载在 `/mcp/`。本地 process 可以通过以下命令启动组合后的 application：

```shell
powercontext server run
```

`server` installation extra 直接包含 FastMCP，不再提供单独的 MCP extra。`create_server_app` 是唯一的
production composition factory，并始终将 MCP 挂载到同一个 immutable `ServerSettings` instance 指定的 path。
MCP package 只提供 projection 与 mounting primitive。增加 HTTP endpoint 不会使它自动暴露到 MCP；每个 MCP
primitive 都需要显式选择 route。

## Contract workflow

修改 generated transport model 前，应先修改 `openapi/powercontext.yaml`。当 Core Protocol model 与 wire type
语义相同时，复用或映射该 model。Transport-only metadata 保留在 Core 之外。

标记了 `x-powercontext-python-model` 的 schema 会导入声明的公共 Core type。当前只对 validation semantics
一致的 Memory literal alias 使用这一机制。Artifact reference、Memory citation、Memory hit 和 Revision
changes 等 object schema 使用 generated Pydantic model，并显式映射到 Core dataclass。Memory 与 entry
identity field 只有在非空、长度受限且属于 printable ASCII 时，才会通过 transport boundary。

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

Authentication、authorization、multi-tenant isolation、durable Operation status、distributed worker 和非
Python SDK 仍不在当前实现范围内。Codex plugin packaging 也独立于 HTTP 与 MCP runtime。
