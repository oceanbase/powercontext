---
title: Interfaces
description: Choose between the Codex plugin, CLI, Python SDKs, HTTP, and MCP.
---

# Interfaces

All remote interfaces operate on the same Server and persistent Artifact storage.

| Interface | Intended use | Install |
| --- | --- | --- |
| Codex plugin | Cross-session recall and explicit Memory maintenance in Codex | `powercontext setup codex` |
| CLI | Setup, diagnostics, Server control, capability checks, and human Candidate review | `powercontext[cli,server]` |
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
powercontext ready
powercontext capabilities
powercontext candidate list --scope-id project:example
powercontext candidate list --scope-id project:example --family skill
powercontext candidate show --scope-id project:example CANDIDATE_ID
powercontext candidate approve --scope-id project:example --expected-version 1 CANDIDATE_ID
powercontext candidate reject --scope-id project:example --expected-version 1 --reason unsupported CANDIDATE_ID
powercontext candidate revise experience --scope-id project:example --expected-version 1 \
  --situation SITUATION --action ACTION --outcome OUTCOME --lesson LESSON CANDIDATE_ID
powercontext candidate revise skill --scope-id project:example --expected-version 1 \
  --name NAME --description DESCRIPTION --instructions-file instructions.md --validation CHECK CANDIDATE_ID
powercontext experience generate --scope-id project:example --source-ref content/SOURCE_ID
powercontext skill generate --scope-id project:example --origin experience \
  --artifact-ref experience/EXPERIENCE_ID@REVISION
powercontext skill show --scope-id project:example --revision 1 SKILL_ID
powercontext skill export --target codex --scope-id project:example --revision 1 \
  --destination .agents/skills/example-skill SKILL_ID
powercontext external-skill scan --scope-id project:example
powercontext external-skill list --scope-id project:example
powercontext external-skill resolve --scope-id project:example --fingerprint SHA256 EXTERNAL_SKILL_ID
powercontext external-skill import --scope-id project:example --fingerprint SHA256 \
  --mode import EXTERNAL_SKILL_ID
```

All content commands call the configured Server. The optional `server` role adds `powercontext server run`; it does
not create a second content profile inside the CLI.

Generation and revision commands accept repeatable `--source-ref TYPE/ID` and
`--artifact-ref FAMILY/ID@REVISION` options instead of serialized request files. `--target FAMILY/ID@REVISION`
automatically includes the target in Artifact evidence. Managed Skill revision accepts exactly one of inline
`--instructions` or `--instructions-file`, and `--validation` can be repeated.

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

The Client also exposes `generate_experience`, `propose_experience`, `get_experience`, `generate_skill`,
`propose_skill`, `get_skill`, `scan_external_skills`, `list_external_skills`, `resolve_external_skill`,
`import_external_skill`, and the Candidate Review methods. Review writes require `expected_version`. Approval returns
the exact Experience or managed Skill `result_artifact`; pending and rejected Candidates are not Artifact revisions.

`generate_experience` and `generate_skill` accept caller-selected exact Source and Artifact references. They return
either one pending Candidate or an explicit `no_op`. A replacement includes its exact target in `artifact_refs` and
sets `target`. Managed Skill generation also declares its provenance shape:

- `experience`: at least one approved Experience reference, with optional exact Sources;
- `source`: only exact Source references, including official or human-authored material;
- `usage`: the exact target Skill plus bounded usage Sources.

These generation operations require `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL`. The lower-level `propose_*`
operations remain available for a human or integration that already has complete typed content and exact evidence.
Neither path approves its own Candidate. After Review approval, deterministic `searchable_text` is stored on the
existing generic Artifact head and enters the backend's rebuildable FTS index, making the Experience eligible for
`PreparedContext` recall in the same scope. Pending and rejected Candidates, all managed Skills, and historical
Experience revisions remain excluded.

## Scheduled Experience incubation

An integration can capture a completed task as a Content Source with metadata `"kind": "task-outcome"`. When the
Experience schedule is configured, APScheduler scans bounded Source windows and asks the configured schema-bound
pipeline for reusable situation, action, outcome, and lesson proposals. Each proposal cites exact Sources and enters
the Review Inbox as a pending Experience Candidate.

Experience incubation has its own persisted Source cursor, independent from Memory extraction. Candidate writes and
cursor advancement commit together; a generation or write failure leaves the window available for retry. Ordinary
prompt Sources are not Task Outcomes and are ignored by this job.

Scheduling stops at the review boundary. It never approves an Experience, includes pending content in
PreparedContext, derives a managed Skill, exports a Skill for Codex, or executes instructions. Skill authoring and
export remain explicit steps after the supporting Experience is approved.

## Managed Skill export to Codex

A configured generator can produce complete managed Skill content through `generate_skill`; a human or integration
can submit already-complete typed content through `propose_skill`. The proposal contains a name, discovery
description, instructions, validation checks, and exact Source or Artifact lineage. It remains a Candidate until a
reviewer approves the exact Candidate version.

Approval creates an immutable Skill Revision. It does not install the Skill or grant execution authority. To make one
approved Revision available to Codex, export it explicitly into a new repository or user Skill directory with
`skill export --target codex`. The command writes `SKILL.md` and `powercontext.json`; the manifest records the exact
Artifact reference and rendered-content hash. It refuses to replace an existing destination, so updates require an
intentional new export rather than a silent overwrite.

Codex can discover a repository-local export under `.agents/skills/<name>/SKILL.md`. The Artifact Revision remains
the content authority; the directory is a host-local projection that can be rebuilt from the same exact Revision.

## External Agent-native Skills

External Skills remain authoritative in their original local packages. With explicitly configured Codex roots, the
Server can scan a scope-local, rebuildable Registry and report name, description, provider, Agent kind, host,
installation scope, locator, and whole-package fingerprint. Exact resolve succeeds only when the same package remains
readable on the configured host and its fingerprint still matches. It never installs a package or falls back to a
different version.

Discovery does not enter Review. An explicit `import_external_skill` request with the exact identity and fingerprint
captures a bounded `SKILL.md` snapshot as Source evidence and asks the configured model for a new managed Skill
Candidate. `mode=import` and `mode=fork` record the caller's intent; both create a new managed identity only after
Review approval and leave the external registration unchanged. Package scripts and assets are not copied into the
managed Artifact.

## Authority and gates

| Surface | Content authority | Model gate | Review gate | Current availability |
| --- | --- | --- | --- | --- |
| External Agent-native Skill | Original package | No for scan/list/resolve; yes for import/fork | No for discovery; yes after import/fork | Host-local Registry and exact resolve |
| Experience | Exact approved Artifact Revision | Yes for generate/evolve; no for typed `propose` | Yes | Exact read and approved-head FTS recall in PreparedContext |
| Managed Skill | Exact approved Artifact Revision | Yes for generate/evolve/import/fork; no for typed `propose` | Yes | Exact read and explicit Codex projection |
| Codex projection | Its source managed Skill Revision | No | No additional review | Rebuildable host-local copy |

## Core SDK

The base `powercontext` package exports Python protocols and models for applications that own their composition root.
It does not select storage, scheduling, transport, or inference on the application's behalf. Use `builtin` when you
want the supplied SQLite or OceanBase-backed implementation in the same process.

## HTTP and MCP

The Server publishes its OpenAPI document at `/openapi.json`, readiness at `/health/ready`, capabilities at
`/v1/capabilities`, and Streamable HTTP MCP at `/mcp` by default. HTTP is the complete application contract. MCP is a
curated agent-facing projection of Memory and Candidate Review operations. The five Candidate Review operations use the
same validation, `expected_version` concurrency checks, and approval transaction over HTTP and MCP.
Experience and Skill generation, exact reads, external Registry operations, and low-level proposal operations remain
HTTP-only.
`POST /v1/context/prepare` and the matching Python Client method expose final ephemeral `PreparedContext` over HTTP;
the Runtime recalls active Memory and approved Experience heads, owns their shared selection and total output budget,
and intentionally does not project the operation as an MCP tool. The public schema remains
`powercontext.prepared-context.v1`; Experience items carry an exact Artifact reference inside the prepared content.
