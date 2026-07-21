# Current remote access implementation

This document follows the remote access implementation end to end, from the OpenAPI contract and generated transport
code to the FastAPI Server, Python Client, CLI providers, and FastMCP projection. It records current behavior rather
than the target architecture proposed by an RFC.

The checked-in [`openapi/powercontext.yaml`](../../../openapi/powercontext.yaml) file is the authoritative HTTP contract.
The [Python API reference](../modules.md) is generated from public modules in the package.

## Implementation flow

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

OpenAPI owns the HTTP shape used by both Server and Client code. Generated models remain transport types. An existing
Core Protocol model is reused through an explicit boundary mapping only when it carries the same meaning.

Server assembly stays in `powercontext.server`, and MCP assembly stays in `powercontext.mcp`. The CLI shell discovers
component-owned command groups instead of maintaining their commands itself.

## Implemented surface

| Component | Current behavior |
| --- | --- |
| HTTP Server | Liveness, readiness, capability discovery, and request IDs |
| Python Client | Synchronous methods for the three HTTP operations |
| CLI | A generic command-provider shell with Client and Server command groups |
| MCP | An allow-listed `get_capabilities` tool over the assembled Server |
| Runtime | No application service is bound |

The default capability set is empty. Without a Runtime binding, the Server does not claim Source processing, Artifact
generation, retrieval, persistence, or scheduling behavior.

## HTTP contract

| Method | Path | Response |
| --- | --- | --- |
| `GET` | `/health/live` | `HealthResponse` |
| `GET` | `/health/ready` | `ReadinessResponse` |
| `GET` | `/v1/capabilities` | `Capabilities` |

Every response includes `X-Request-ID`. The readiness endpoint returns `503 Service Unavailable` when required bindings
are not ready. The readiness probe and capability provider can be supplied when the FastAPI application is assembled.

`ServerSettings` reads `POWERCONTEXT_SERVER_HOST` and `POWERCONTEXT_SERVER_PORT`. The defaults are `127.0.0.1` and
`8000`.

## Python Client

`PowerContextClient` provides `get_liveness()`, `get_readiness()`, and `get_capabilities()`. The facade is synchronous
and validates successful responses against the OpenAPI-derived models.

Client failures use these stable exception classes:

- `TransportError` when no valid HTTP response is received;
- `ServerResponseError` for a non-success HTTP status;
- `InvalidResponseError` when a successful response violates the transport model.

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

Client commands accept `--server-url`, `--timeout`, and `--json`. The Server command accepts `--host` and `--port`.

## MCP

FastMCP projects selected operations from an assembled FastAPI application. The current route map exposes only
`get_capabilities` as a tool. It does not expose liveness, readiness, resources, prompts, or Runtime tools.

The Streamable HTTP endpoint is mounted at `/mcp/`. A local process can start the combined application with:

```shell
uvicorn powercontext.mcp:create_mcp_app --factory
```

Adding an HTTP endpoint does not expose it through MCP. Each MCP primitive requires an explicit route selection.

## Contract workflow

Change `openapi/powercontext.yaml` before changing generated transport models. Reuse or map a Core Protocol model when
its meaning matches the wire type. Keep transport-only metadata outside Core.

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

The current remote surface does not define Source capture, Artifact Revision reads, context retrieval, Memory
generation, durable Operations, persistence, authentication, or non-Python SDKs. These features need an accepted
Runtime application-service boundary or a separate design before they become remote contracts.
