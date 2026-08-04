# Run and use the PowerContext Server

The ready-to-run Server owns one `BuiltinRuntime` and exposes it through HTTP. The same process can project a
curated subset of Memory operations as MCP tools. `ServerSettings.mcp.enabled` controls that projection, so MCP does
not require a separate entry point or extra.

## Install and start

Install the Server role together with the CLI to run this instance from the command line:

```bash
uv add "powercontext[cli,server]"
```

Start the Server:

```bash
uv run powercontext server run
```

The default listener is `127.0.0.1:8000`. SQLite data is stored in `powercontext.db`. Command options can override the
listener:

```bash
uv run powercontext server run --host 0.0.0.0 --port 8080
```

The process opens its configured database, creates a scope-bound Builtin runtime, and closes owned database, inference,
and scheduler resources during shutdown.

## Server configuration

`ServerSettings` keeps transport and Builtin configuration at one level:

| Group | Purpose |
| --- | --- |
| `http` | Listener host and port |
| `mcp` | Whether MCP is mounted and at which path |
| `runtime` | Source-window and scheduler policy |
| `database` | SQLite or OceanBase configuration |
| `inference` | Optional generation and embedding configuration |

Environment variables use the `POWERCONTEXT_SERVER_` prefix. Nested fields are joined with underscores:

```bash
export POWERCONTEXT_SERVER_HTTP_PORT="8080"
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///data/powercontext.db"
export POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT="200"
export POWERCONTEXT_SERVER_MCP_ENABLED="false"
```

SQLite is the default database. Select OceanBase by changing only the discriminator and URL:

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND="oceanbase"
export POWERCONTEXT_SERVER_DATABASE_URL="mysql+aoceanbase://user:password@host:2881/powercontext?charset=utf8mb4"
```

Both database choices expose full-text search through the same Server API. With an embedding model, SQLite uses Vec1
and OceanBase uses HNSW for `vector` and `hybrid` searches.

Inference configuration is documented in [Configure Pydantic AI inference](pydantic-ai-inference.md).

Set `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` to process pending Source windows on a persisted interval. Scheduled
jobs use the SQLite sidecar at `POWERCONTEXT_HOME/scheduler.db`. Scheduling works with either application database and
requires a configured generation pipeline.

## HTTP surface

The source contract is `openapi/powercontext.yaml`. Generated Pydantic models and operation descriptors under
`powercontext.http._generated` are build artifacts of that contract.

| Area | Operations |
| --- | --- |
| Health | liveness and readiness |
| Capabilities | source types, Artifact families, extraction, search modes |
| Sources | capture durable content evidence |
| Memory | flush pending Sources, remember explicit entries, search |
| Memory entries | list, get, revise, retire |
| History | list Memory changes |

Every domain request includes a scope ID. That ID selects the Source journal, Memory head, and Trigger cursor used by
the Builtin runtime. HTTP request models are transport values and remain separate from Core domain models.

Server errors use the OpenAPI error schema and include an `X-Request-ID` response header. Validation errors, revision
conflicts, missing entries, unavailable inference, and internal failures map to stable HTTP status codes.

## Python Client

Install the Client role for the SDK:

```bash
uv add "powercontext[client]"
```

`PowerContextClient` is async-native and uses the generated request and response models:

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

The client validates successful responses with Pydantic. Transport failures, invalid responses, and structured Server
errors are exposed as distinct exceptions from `powercontext.client`.

## CLI

Add the CLI extra to expose the installed Client command:

```bash
uv add "powercontext[cli,client]"
```

The `client` command provides process and capability checks:

```bash
uv run powercontext client live
uv run powercontext client ready
uv run powercontext client capabilities
uv run powercontext client --json capabilities
```

Use `POWERCONTEXT_CLIENT_SERVER_URL` and `POWERCONTEXT_CLIENT_TIMEOUT` for client defaults.

The CLI discovers command groups from installed roles. `powercontext[cli]` provides the Builtin command by default;
Client and Server commands appear only when their role extras are also installed.

## MCP

MCP is enabled by default and mounted at `/mcp`. Disable it without changing the HTTP API:

```bash
export POWERCONTEXT_SERVER_MCP_ENABLED="false"
```

To change the mount path:

```bash
export POWERCONTEXT_SERVER_MCP_PATH="/agent"
```

The MCP projection includes the agent-facing Memory operations for search, listing, reading, remembering, revising,
and retiring entries, plus Candidate Review operations for listing, reading, approving, rejecting, and revising
Candidates. Health, capability, Source capture, Experience, flush, and change-history endpoints remain HTTP-only.

HTTP and MCP share the same Server application and Runtime binding. A request made through either transport therefore
uses the same scope isolation, validation, concurrency checks, and persistence behavior.

## Programmatic composition

Applications that host FastAPI themselves can build the same service:

```python
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings

app = create_server_app(settings=ServerSettings())
```

`create_server_app()` owns the built-in Runtime lifecycle. Tests and embedding applications may inject a
`candidate_pipeline` or `embedding_model` without replacing that lifecycle.
