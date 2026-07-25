- Proposal Name: `remote_access_architecture`
- Start Date: 2026-07-16
- RFC PR: [oceanbase/powercontext#11](https://github.com/oceanbase/powercontext/pull/11)
- Tracking Issue: Not assigned

# Summary

This RFC proposes a remote access boundary for PowerContext. A Server exposes application services through an
OpenAPI-defined HTTP contract. Generated Client SDK transports and a curated MCP surface consume the same Server
semantics. The proposal defines ownership and contract flow. It does not define Runtime processing, persistence,
scheduling, or a final domain API.

Names such as Source, Artifact Revision, ContextBundle, Memory Generation, and Operation are scenario vocabulary in
this RFC. Related operations and schemas illustrate the remote boundary. Accepting this RFC does not accept those
names as a wire contract.

# Motivation

Core Protocol types can be used directly by Python applications, but remote processes and other languages need a
stable transport boundary. Agent integrations also need a way to discover selected capabilities without creating a
second application model.

The boundary needs one owner for the HTTP contract and a clear path from domain behavior to each transport. Without
that ownership, Server handlers, Client SDKs, and MCP adapters can drift into separate definitions of the same action.

# Guide-level explanation

## Global architecture

An application uses Core Protocol models and supplies Runtime application services for its workflows. It can call those
services locally or expose them through a Server. Remote clients use an SDK generated from the Server's OpenAPI
contract. Agent integrations use a smaller MCP projection supplied by the same Server.

```text
Core Protocol
      |
Runtime application services
      |-------------------------|
      v                         v
Local integration          Server adapters
                                |-------------------|
                                v                   v
                         OpenAPI HTTP contract     MCP projection
                                |
                     Generated Client transport
                                |
                      Handwritten Client facade

Component command groups -> CLI extension shell
```

| Layer | Responsibility |
| --- | --- |
| Core Protocol | Reusable domain types and sans-I/O component contracts |
| Runtime application services | Use-case behavior, transaction boundaries, processing, and retrieval semantics |
| Server | HTTP and MCP adapters, process assembly, and deployment policy |
| Client transport | Serialization and calls generated from OpenAPI |
| Client facade | Stable errors and language-specific interaction patterns |
| CLI shell | Discovery and mounting of component-owned command groups |

Server-only assembly remains outside the Core Protocol public surface. A Server may map HTTP models to Core Protocol
models when their semantics match, but transport concerns do not become Core concepts merely because generated code
uses similar fields.

For example, a Runtime could accept evidence and later return useful context. The RFC may call those illustrative
actions "capture a Source" and "retrieve a ContextBundle." Those names explain request flow. They do not reserve Python
names, URL paths, MCP tools, persistence records, or lifecycle rules.

## Access surfaces

HTTP is the complete remote contract. Client SDKs provide language-specific ergonomics over that contract. MCP exposes
only operations selected for Agent interaction. Adding an HTTP operation does not add an MCP tool or resource.

The CLI is independent from those transports. It discovers command groups supplied by components, so local Runtime
commands and remote Client commands can coexist without moving either command set into the shell.

## Composition

The Python distribution provides optional Client, Server, and CLI surfaces. MCP is a Server transport controlled by
Server configuration rather than a separate installation role. A process can use the Client without hosting a Server.
A local Runtime can use the CLI without enabling remote access. Applications combine roles only when one environment
needs them together.

# Reference-level explanation

## Interpretation

The architecture and ownership boundaries above are normative. The operation categories and names below are
illustrative. This RFC does not accept a specific domain operation, URL, schema, MCP name, storage model, job lifecycle,
or ranking rule.

## OpenAPI contract

The checked-in OpenAPI document defines HTTP requests, responses, operation identifiers, and compatibility. Server and
Client generation use the same document. Generated files are build artifacts and are not edited as an alternative to
changing the contract.

If a Core Protocol model already represents the required meaning, the Server maps to that model at the application
boundary. If the wire format needs transport-only metadata, it uses a transport model without expanding the Core
Protocol API.

## Illustrative HTTP surface

The remote boundary is expected to need several classes of operation:

| Illustrative operation | Reason for inclusion |
| --- | --- |
| Health and readiness | Process orchestration |
| Capability discovery | Report behavior supplied by the assembled Runtime |
| Evidence submission | Demonstrate a command crossing the remote boundary |
| Exact record retrieval | Demonstrate an immutable query |
| Context retrieval | Demonstrate a bounded application query |
| Long-running work status | Demonstrate asynchronous completion |

These categories do not select resource names, paths, consistency guarantees, or completion semantics. Those decisions
depend on the Runtime application-service boundary.

## Client SDK

Generated Client code owns wire serialization and endpoint calls. A handwritten facade may own stable exceptions,
authentication policy, retries, pagination, or waiting behavior once the HTTP contract defines them.

## CLI extension

The CLI does not duplicate Server or Client behavior. It discovers command groups supplied by installed components.
This keeps local Runtime commands independent from remote Client commands and lets one environment combine both.

The RFC specifies composable roles rather than packaging internals. Packaging may change as long as users can install
a remote Client without a Server and can add CLI support to local or remote workflows.

## MCP projection

The explicit MCP projection is part of the proposed architecture. It is built on the assembled Server rather than
directly on Core Protocol modules, and it uses the same application services and policy decisions as HTTP. The adapter
selects a task-oriented subset instead of mirroring every endpoint.

MCP tools, resources, and prompts have different interaction semantics. A later design must justify each exported
primitive. Names such as `capture_source`, `search_context`, and `get_operation` are examples only. This RFC does not
accept those names or require a stdio bridge.

Protocol-version support is a downstream compatibility decision and is outside this RFC.

## Deferred deployment details

The first Server may target one logical trust domain and one catalog per deployment. This constraint avoids choosing a
tenant identity and authorization model before those requirements are known. Whether this limitation is acceptable for
the first Runtime-backed API remains an unresolved product decision.

The RFC does not select a database, worker model, scheduler, lease algorithm, search backend, authentication scheme, or
process topology. Those choices depend on Runtime behavior and need separate review when they affect public semantics.

## Compatibility

OpenAPI changes must be reviewed as public contract changes. CI should regenerate transport code with pinned tools and
reject drift. Behavioral tests should verify requests and responses through the public boundary. They should avoid
asserting incidental packaging internals or generated source structure.

# Drawbacks

OpenAPI-first development adds a generation step and requires reviewers to examine both the contract and generated
changes. A handwritten facade adds another compatibility surface. Keeping MCP narrower than HTTP also requires explicit
selection whenever Server capabilities grow.

Deferring Runtime and persistence decisions leaves several useful operations unspecified. This is intentional, but it
means the RFC cannot by itself guide implementation of evidence processing or retrieval.

# Rationale and alternatives

A single remote boundary prevents each Client and integration from defining its own data model. OpenAPI supports
generated clients and remains inspectable by Server tests and tooling. A handwritten-only Client would be simpler at
first, but it would make transport parity harder to maintain across languages.

Exposing Core Protocol objects directly over HTTP would reduce mapping code, but it would couple domain evolution to
wire compatibility. The proposed boundary reuses Core models when their meaning matches and retains transport-only
models when the wire has different needs.

Treating MCP as a parallel service would let it evolve independently, but domain behavior and policy could diverge
from HTTP. Projecting selected Server semantics keeps one application boundary.

This RFC does not include a modular monolith with API, worker, and scheduler roles. That design may be suitable after
the Runtime execution model is known, but accepting it here would make transport architecture decide Runtime policy.

# Prior art

PowerContext RFC 0002 separates Core Protocol from Runtime-owned workflows. This RFC applies the same boundary to
remote access.

[Hindsight](https://github.com/vectorize-io/hindsight) demonstrates generated clients and asynchronous operation
tracking. [Graphiti](https://github.com/getzep/graphiti) and
[Supermemory](https://github.com/supermemoryai/supermemory) provide examples of remote capture and queued processing.
Their execution models are prior art, not requirements for this RFC.

The contract follows the [OpenAPI Specification](https://spec.openapis.org/oas/). MCP integration follows the
[Model Context Protocol](https://modelcontextprotocol.io/specification/) while leaving the supported protocol version
to the implementation.

# Unresolved questions

- Which Runtime application-service commands and queries are stable enough to expose remotely?
- Is one logical trust domain and one catalog an acceptable initial deployment boundary?
- Which compatibility guarantees should the first Runtime-backed HTTP operations provide?
- Which HTTP operations, if any, should also be MCP tools, resources, or prompts?
- When should additional language SDKs be generated and released?

# Future possibilities

Later RFCs may define Runtime execution, durable work, persistence profiles, retrieval consistency, authentication, and
multi-tenant isolation. Those designs can add remote operations without changing the ownership boundary established
here.

Client SDKs may cover more languages once the OpenAPI contract and conformance tests are stable. MCP can add tools,
resources, or prompts when the corresponding Server semantics and authorization policy are explicit.
