---
title: Interfaces
description: Choose between the Codex plugin, CLI, Python SDKs, HTTP, and MCP.
---

# Interfaces

All remote interfaces operate on the same Server and persistent Artifact storage.

| Interface | Intended use | Install |
| --- | --- | --- |
| Codex plugin | Cross-session recall and explicit Memory maintenance in Codex | `powercontext setup codex` |
| CLI | Setup, diagnostics, Server control, capability checks, and human Candidate review | `powercontext[cli,client,server]` |
| Python Client SDK | Typed async calls to a running Server | `powercontext[client]` |
| Core SDK | In-process Source, Artifact, Trigger, and composition contracts | base package |
| HTTP | Service integration from any language | `powercontext[server]` |
| MCP | Agent tools for Memory and Candidate Review | enabled by Server |

## Codex plugin

The project-context skill tells Codex when to search, remember, revise, or retire Memory. The prompt hook recalls
relevant entries and captures user input as Source evidence. MCP tools perform explicit operations. The plugin never
starts or embeds the Server.

## CLI

```text
powercontext setup codex
powercontext doctor
powercontext server run
powercontext client ready
powercontext client capabilities
powercontext client candidate list --scope-id project:example
powercontext client candidate show --scope-id project:example CANDIDATE_ID
powercontext client candidate approve --scope-id project:example --expected-version 1 CANDIDATE_ID
powercontext client candidate reject --scope-id project:example --expected-version 1 --reason unsupported CANDIDATE_ID
powercontext client candidate revise revision-request.json
powercontext builtin capabilities
```

CLI commands appear only when their owning extras are installed.

## Python Client SDK

Use the Client SDK when the Server owns persistence:

```python
import asyncio

from powercontext.http import PrepareContextRequest, RememberMemoryRequest, SearchMemoryRequest
from powercontext.client import PowerContextClient


async def main() -> None:
    async with PowerContextClient("http://127.0.0.1:8000") as client:
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id="project:example",
                kind="decision",
                text="Keep the public API asynchronous.",
            )
        )
        result = await client.search_memory(
            SearchMemoryRequest(
                scope_id="project:example",
                query="public API",
            )
        )
        print([hit.text for hit in result.hits])
        prepared = await client.prepare_context(
            PrepareContextRequest(scope_id="project:example", query="public API")
        )
        print(prepared.content)


asyncio.run(main())
```

Mutation responses include an exact citation. Pass that citation back when revising, retiring, or reading an immutable
entry version.

The Client also exposes `propose_experience`, `get_experience`, `list_artifact_candidates`,
`get_artifact_candidate`, `approve_artifact_candidate`, `reject_artifact_candidate`, and
`revise_artifact_candidate`. Review writes require `expected_version`. Approval returns the exact Experience
`result_artifact`; pending and rejected Candidates are not Artifact revisions.

## Core SDK

The base `powercontext` package exports Python protocols and models for applications that own their composition root.
It does not select storage, scheduling, transport, or inference on the application's behalf. Use `builtin` when you
want the supplied SQLite or OceanBase-backed implementation in the same process.

## HTTP and MCP

The Server publishes its OpenAPI document at `/openapi.json`, readiness at `/health/ready`, capabilities at
`/v1/capabilities`, and Streamable HTTP MCP at `/mcp` by default. HTTP is the complete application contract. MCP is a
curated agent-facing projection of Memory and Candidate Review operations. The five Candidate Review operations use the
same validation, `expected_version` concurrency checks, and approval transaction over HTTP and MCP.
`propose_experience` and `get_experience` remain HTTP-only.
`POST /v1/context/prepare` and the matching Python Client method expose final ephemeral `PreparedContext` over HTTP;
the Runtime owns selection and total output budgeting, and the operation is intentionally not projected as an MCP tool.
