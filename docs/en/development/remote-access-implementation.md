# Current remote access implementation

This document follows the remote access implementation end to end, from the OpenAPI contract and generated transport
code to the FastAPI Server, Python Client, CLI providers, and FastMCP projection. It records current behavior rather
than the target architecture proposed by an RFC.

The checked-in [`openapi/powercontext.yaml`](../../../openapi/powercontext.yaml) file is the authoritative HTTP contract.
The [Python API reference](../modules.md) is generated from public modules in the package.

## Implementation flow

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

OpenAPI owns the HTTP shape used by both Server and Client code. Generated Pydantic models validate transport values
strictly. Core dataclasses are mapped explicitly instead of being used as wire models because their constructors do
not enforce OpenAPI field constraints. Closed Core literal aliases are reused when validation semantics are identical.

The production `create_server_app()` factory opens `PowerContextRuntime` in its FastAPI lifespan, closes it during
shutdown, and always mounts MCP. HTTP and MCP use that same Runtime instance. The lower-level `create_app()`
factory only binds the HTTP adapter and does not consume process settings. The CLI shell discovers component-owned
command groups instead of maintaining their commands itself.

## Implemented surface

| Component | Current behavior |
| --- | --- |
| HTTP Server | Runtime-backed Source and Memory operations, readiness, capabilities, and request IDs |
| HTTP contract | Source capture and Memory-family commands and queries |
| Python Client | Synchronous typed methods for health, Source capture, and Memory operations |
| CLI | A generic command-provider shell with Client and Server command groups |
| MCP | An allow-listed Memory tool set mounted on the same Server |
| Runtime | SQLite Source journal, Memory storage, cursor, and optional persisted scheduling |

The production factory owns the Runtime lifecycle. The lower-level `create_app()` factory remains available for
contract and adapter tests; without a binding it reports `not_ready`.

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

Every response includes `X-Request-ID`. The readiness endpoint returns `503 Service Unavailable` when required bindings
are not ready. The lower-level adapter factory accepts optional readiness and capability providers for explicit
application assemblies. Production Server and MCP factories derive both values from the Runtime they own and reject
detached overrides.

Known domain failures are mapped before the response returns through request ID middleware. Missing Memory values use
`404`; Source, Revision, and inactive-entry conflicts use `409`; invalid transport or Runtime requests use `422`; and
unavailable Runtime or inference dependencies use `503`. Only unexpected failures use `500 internal_error`.

Artifact-family operations use family-specific prefixes. Memory operations stay under `/v1/memory/`; future Artifact
families must use their own prefixes instead of extending a generic context namespace. Content capture remains a Source
operation and returns `202 Accepted` without inventing a durable Operation resource.

`ServerSettings` groups process configuration under `http`, `mcp`, `runtime`, `storage`, and `inference`. Environment
variables use explicit flat names with the `POWERCONTEXT_SERVER_` prefix, for example
`POWERCONTEXT_SERVER_STORAGE_PATH` and `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL`. Pydantic Settings splits only
at the Server group boundary, leaving field names such as `generation_model` intact. Provider credentials remain owned
by the selected model provider.

When `inference.generation_model` is configured, the composition root builds `LLMMemoryCandidatePipeline`. Its evidence
projector exposes captured `ContentSource` text and metadata to the Memory extraction schema. Without a generation
model, capture and explicit Memory operations remain available, while flush reports that extraction is unsupported.

The `inference.embedding_*` fields contain the embedding model, profile ID, dimension, normalization, and timeout.
`storage.vec1_extension` remains specific to the SQLite profile. The composition root requires both blocks together,
builds the Pydantic AI embedding adapter, and passes it with the matching `EmbeddingProfile` to the Runtime.
`/v1/capabilities` reports extraction and search behavior from the initialized Runtime and backend; it does not expose
storage paths, model names, Source window sizes, or inference budgets.

### Run with an OpenAI-compatible endpoint and Vec1

The repository provides a Linux installer for the pinned
[official Vec1 0.7 source](https://sqlite.org/vec1/doc/trunk/doc/vec1.md):

```shell
make vec1-install
```

It downloads the official Vec1 source and SQLite headers, verifies their SHA-256 digests, compiles a portable loadable
extension, and probes it through the APSW version used by PowerContext. The resulting
`.powercontext/vec1.so` file is local build output and is not committed.

Copy the example environment file and replace the model names, embedding dimension, endpoint, and credentials:

```shell
cp .env.example .env
set -a
. ./.env
set +a
powercontext server run
```

`ServerSettings` does not load `.env` files itself; the shell or process supervisor must export the values. Use the
`openai-chat:` model prefix for generic Chat Completions-compatible generation endpoints. Embeddings use the
`openai:` prefix. The example assumes generation and embeddings share `OPENAI_BASE_URL` and `OPENAI_API_KEY`.

## Python Client

`PowerContextClient` is synchronous and validates successful responses against the OpenAPI-derived models. In addition
to health and capability discovery, it provides:

- `capture_content_source()` and `flush_memory()`;
- `remember_memory()` and `search_memory()`;
- `list_memory_entries()` and `get_memory_entry()`;
- `revise_memory_entry()` and `retire_memory_entry()`;
- `list_memory_changes()`.

Each application method accepts its generated request model. The generated operation metadata records the request type,
response type, and exact successful status. This lets capture require `202` while the Memory operations require `200`.
Memory responses use `ArtifactReference` for exact Revisions. Entry responses and search hits carry a nested
`MemoryCitation`; callers can pass that citation directly to get, revise, or retire requests. The Client does not retry
mutations or infer Runtime behavior.

Client failures use these stable exception classes:

- `TransportError` when no valid HTTP response is received;
- `ServerResponseError` for a non-success HTTP status, including a validated stable error code when supplied;
- `InvalidResponseError` when a successful response violates the transport model.

`ClientSettings` reads the Server URL and timeout through the `POWERCONTEXT_CLIENT_` prefix. Command-line options
override those settings.

## CLI

The top-level Typer shell discovers command groups supplied by installed components. It owns global help and version
handling but does not define component commands.

The current groups provide:

```text
powercontext client live
powercontext client ready
powercontext client capabilities
powercontext server run
```

Client commands accept `--server-url`, `--timeout`, and `--json`. The Server command reads process configuration from
`ServerSettings`; optional `--host` and `--port` values are partial init-source overrides over the environment source.

## MCP

FastMCP projects selected operations from an assembled FastAPI application. The route map exposes search, list, exact
get, remember, revise, and retire. It excludes Source capture, flush, changes, health, readiness, and capabilities.

The Streamable HTTP endpoint is mounted at `/mcp/`. A local process can start the combined application with:

```shell
powercontext server run
```

The `server` installation extra includes FastMCP; there is no separate MCP extra. `create_server_app` is the only
production composition factory and always mounts MCP at the path from the same immutable `ServerSettings` instance used
by the Runtime. The MCP package only provides projection and mounting primitives. Adding an HTTP endpoint does not
expose it through MCP; each MCP primitive requires an explicit route selection.

## Contract workflow

Change `openapi/powercontext.yaml` before changing generated transport models. Reuse or map a Core Protocol model when
its meaning matches the wire type. Keep transport-only metadata outside Core.

Schemas marked with `x-powercontext-python-model` import the declared public Core type. This is limited to the matching
Memory literal aliases. Object schemas such as Artifact references, Memory citations, Memory hits, and Revision changes
are generated Pydantic models and mapped explicitly to Core dataclasses. Memory and entry identity fields are rejected
at the transport boundary unless they are non-empty, bounded, printable ASCII values.

Run the following checks after a contract change:

```shell
make api-generate
make contract-test
make unit-test
make e2e-test
```

Tests should verify generated output and public behavior. They should not depend on incidental packaging internals or
generated source layout.

## Not implemented

Authentication, authorization, multi-tenant isolation, durable Operation status, distributed workers, and non-Python
SDKs remain outside the current implementation. Codex plugin packaging is also separate from the HTTP and MCP runtime.
