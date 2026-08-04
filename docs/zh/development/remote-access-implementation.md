# 运行和使用 PowerContext Server

可直接运行的 Server 持有一个 `BuiltinRuntime`，并通过 HTTP 暴露它。同一个进程可以将一组经过筛选的 Memory
operation 投影为 MCP tool。`ServerSettings.mcp.enabled` 控制该投影，因此 MCP 不需要独立入口或 extra。

## 安装和启动

同时安装 Server role 和 CLI，才能从命令行运行本实例：

```bash
uv add "powercontext[cli,server]"
```

启动 Server：

```bash
uv run powercontext server run
```

默认监听 `127.0.0.1:8000`，SQLite 数据保存在 `powercontext.db`。命令参数可以覆盖监听地址：

```bash
uv run powercontext server run --host 0.0.0.0 --port 8080
```

进程会打开配置的 database，创建按 scope 隔离的 Builtin runtime，并在关闭时释放其持有的 database、inference 和
scheduler 资源。

## Server 配置

`ServerSettings` 将 transport 和 Builtin 配置保持在同一层级：

| 配置组 | 用途 |
| --- | --- |
| `http` | listener host 和 port |
| `mcp` | 是否挂载 MCP 及其 path |
| `runtime` | Source window 和 scheduler 策略 |
| `database` | SQLite 或 OceanBase 配置 |
| `inference` | 可选 generation 和 embedding 配置 |

环境变量使用 `POWERCONTEXT_SERVER_` prefix，嵌套字段用下划线连接：

```bash
export POWERCONTEXT_SERVER_HTTP_PORT="8080"
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///data/powercontext.db"
export POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT="200"
export POWERCONTEXT_SERVER_MCP_ENABLED="false"
```

默认 database 是 SQLite。只需修改 discriminator 和 URL 即可选择 OceanBase：

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND="oceanbase"
export POWERCONTEXT_SERVER_DATABASE_URL="mysql+aoceanbase://user:password@host:2881/powercontext?charset=utf8mb4"
```

两种 database 都通过同一组 Server API 提供全文检索。配置 embedding model 后，SQLite 使用 Vec1，OceanBase
使用 HNSW 提供 `vector` 和 `hybrid` 检索。

inference 配置见[配置 Pydantic AI 推理](pydantic-ai-inference.md)。

设置 `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` 可以按持久化 interval 处理待消费的 Source window。
定时 job 使用 `POWERCONTEXT_HOME/scheduler.db` 作为 SQLite sidecar。调度可以配合任一 application database
使用，但必须配置 generation pipeline。

## HTTP 接口

契约源文件是 `openapi/powercontext.yaml`。`powercontext.http._generated` 下的 Pydantic model 和 operation descriptor
由该契约生成。

| 领域 | Operation |
| --- | --- |
| Health | liveness 和 readiness |
| Capabilities | source type、Artifact family、extraction、search mode |
| Sources | capture 持久化 content evidence |
| Memory | flush 待处理 Source、remember 显式 entry、search |
| Memory entries | list、get、revise、retire |
| History | list Memory change |

每个领域请求都包含 scope ID。该 ID 选择本地 runtime 使用的 Source journal、Memory head 和 Trigger cursor。
HTTP request model 是 transport value，与 Core domain model 保持独立。

Server error 使用 OpenAPI error schema，并在 response header 中包含 `X-Request-ID`。validation error、revision
conflict、entry 不存在、inference unavailable 和内部 failure 会映射为稳定的 HTTP status code。

## Python Client

安装 Client role 以使用 SDK：

```bash
uv add "powercontext[client]"
```

`PowerContextClient` 是 async-native client，使用生成的 request 和 response model：

```python
from powercontext.http import SearchMemoryRequest
from powercontext.client import PowerContextClient


async def search() -> None:
    async with PowerContextClient("http://127.0.0.1:8000") as client:
        capabilities = await client.get_capabilities()
        result = await client.search_memory(
            SearchMemoryRequest(
                scope_id="project-alpha",
                query="composition root",
                limit=10,
                mode="auto",
            )
        )
        print(capabilities.model_dump())
        print(result.model_dump())
```

client 使用 Pydantic 校验成功 response。transport failure、无效 response 和结构化 Server error 分别映射为
`powercontext.client` 中不同的 exception。

## CLI

增加 CLI extra 后，已安装的 Client command 才会出现：

```bash
uv add "powercontext[cli,client]"
```

`client` command 提供进程和 capability 检查：

```bash
uv run powercontext client live
uv run powercontext client ready
uv run powercontext client capabilities
uv run powercontext client --json capabilities
```

可以通过 `POWERCONTEXT_CLIENT_SERVER_URL` 和 `POWERCONTEXT_CLIENT_TIMEOUT` 设置 client 默认值。

CLI 通过已安装 role 的 entry point 发现 command group。`powercontext[cli]` 默认提供 Builtin command；只有同时
安装 Client 或 Server role，相应 command 才会出现在帮助信息中。

## MCP

MCP 默认启用并挂载到 `/mcp`。可以在不改变 HTTP API 的情况下关闭：

```bash
export POWERCONTEXT_SERVER_MCP_ENABLED="false"
```

修改 mount path：

```bash
export POWERCONTEXT_SERVER_MCP_PATH="/agent"
```

MCP 投影包含面向 agent 的 Memory operation，用于 search、list、read、remember、revise 和 retire entry；也包含
Candidate Review operation，用于 list、read、approve、reject 和 revise Candidate。health、capability、Source capture、
Experience、flush 和 change history endpoint 仍然只通过 HTTP 提供。

HTTP 和 MCP 共用同一个 Server application 和 Runtime binding。无论通过哪种 transport 发起请求，都会使用相同的
scope isolation、validation、并发校验和 persistence behavior。

## 程序化组合

自行托管 FastAPI 的应用可以构造同一个 service：

```python
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings

app = create_server_app(settings=ServerSettings())
```

`create_server_app()` 持有内置 Runtime 的生命周期。测试或嵌入式应用可以注入 `candidate_pipeline` 或
`embedding_model`，无需替换整个生命周期。
