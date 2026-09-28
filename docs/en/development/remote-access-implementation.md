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
listener, but binding to a routable address is refused for an *unauthenticated* Server: enable bearer authentication
(recommended -- terminate TLS at a reverse proxy), or opt in explicitly when TLS is terminated upstream or the network
is otherwise controlled.

```bash
# Recommended: authenticate the Server, then bind a routable address (put TLS in front in production).
POWERCONTEXT_SERVER_ACCESS_MODE=enforced \
POWERCONTEXT_SERVER_AUTH_TOKEN="replace-with-a-strong-token" \
  uv run powercontext server run --host 0.0.0.0 --port 8080
```

```bash
# Or, with TLS terminated upstream / a controlled network, opt in to an unauthenticated bind.
POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true \
  uv run powercontext server run --host 0.0.0.0 --port 8080
```

Running `--host 0.0.0.0` without one of the above exits with an error rather than silently exposing an
unauthenticated Server.

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

Both database choices expose full-text search through the same Server API. With an embedding model, SQLite uses sqlite-vec
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
| Server discovery | stable deployment identity and protocol contracts |
| Capabilities | source types, Artifact families, extraction, search modes |
| Sources | capture durable content evidence |
| Memory | flush pending Sources, remember explicit entries, search |
| Memory entries | list, get, revise, retire |
| History | list Memory changes |

Every domain request includes a scope ID. That ID selects the Source journal, Memory head, and Trigger cursor used by
the Builtin runtime. HTTP request models are transport values and remain separate from Core domain models.

Server errors use the OpenAPI error schema and include a Server-owned `X-PowerContext-Request-ID` response header
derived from the inbound request span. Validation errors, revision conflicts, missing entries, unavailable inference,
and internal failures map to stable HTTP status codes.

### Server identity and compatibility discovery

`GET /v1/server-info` is protected by `server.observe` and returns the stable deployment `server_id`, installed package
version, API contract version, response schema version, and initial feature contracts. It deliberately does not report
health, enabled runtime capabilities, limits, inventory, secrets, filesystem paths, or the authenticated principal.
Use the dedicated health, capabilities, statistics, and access endpoints for those concerns.

All discovery versions use `major` and `minor` integers. A major increment may remove or incompatibly change the
governed contract; a minor increment is backward compatible. The response `schema_version` governs its fields and
semantics, while `api_contract_version` is the major/minor projection of the OpenAPI `info.version`. Each feature
contract version applies only to its listed OpenAPI operation IDs: adding an operation or compatible semantics increments
minor; removing, renaming, or incompatibly changing a listed operation increments major. Compatible clients must ignore
unknown optional fields introduced by a schema minor version.

OpenAPI's root `x-powercontext-feature-contracts` declares the explicit feature versions. Each governed operation lists
its membership in the same extension; `make api-generate` produces the discovery metadata from those declarations.
Generation rejects invalid versions, unknown or duplicate memberships, and features without operations. Version bumps
remain an explicit contract edit, not an automatic consequence of changing membership.

The Server stores one identity singleton using the Runtime-owned primary relational database. Startup creates the identity
table idempotently, then atomically creates or loads the singleton, so concurrent initializers converge on one ID and
restarts, package upgrades, backup restore, and replicas sharing that database retain it. If identity schema
initialization or loading fails, Server startup fails before readiness instead of publishing a temporary identity. An
in-memory SQLite deployment, including SQLite URI memory mode, receives a new ID with each database lifetime because it
has no durable store. Applications using the same shared-memory SQLite database share both data and identity while any
Runtime connection keeps that database alive; after the last connection closes, reopening creates new data and identity.

Treat a restored backup as the same deployment and keep its ID. When a backup is used to create an independent clone,
stop every Server process using the clone database and rotate only the clone:

```bash
uv run powercontext server identity-reset --env-file /path/to/clone.env --maintenance-confirmed
```

The command refuses in-memory databases and requires the explicit maintenance confirmation. It cannot detect active
replicas, so stopping them is an operator precondition. Logical application-data imports do not copy the identity unless
the `pc_server_identity` table itself is included.

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
        server_info = await client.get_server_info()
        capabilities = await client.get_capabilities()
        result = await client.search_memory(
            SearchMemoryRequest(
                scope_id="project-alpha",
                query="composition root",
                limit=10,
                mode="auto",
            )
        )
        print(server_info.model_dump())
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
uv run powercontext live
uv run powercontext ready
uv run powercontext capabilities
uv run powercontext --json capabilities
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
