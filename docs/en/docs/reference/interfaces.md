---
title: Interfaces
description: Choose between Agent integrations, the CLI, Python SDKs, HTTP, and MCP.
---

# Interfaces

All remote interfaces operate on the same Server and persistent Artifact storage.

| Interface | Intended use | Install |
| --- | --- | --- |
| Codex plugin | Cross-session recall and explicit Memory maintenance in Codex | `powercontext setup codex` |
| Pydantic AI adapter | Memory tools, automatic context preparation, and optional trajectory capture | `powercontext-pydantic-ai` |
| DeepSeek Harness plugin | Cross-session recall and explicit Memory maintenance in DeepSeek Harness | `powercontext setup dsh` |
| LangChain middleware | Bounded recall and completed-turn Source capture in `create_agent` | `powercontext-langchain` |
| LangGraph adapter | Memory tools and bounded recall inside a LangGraph graph | `powercontext-langgraph` |
| Pi package | Cross-session recall, native Memory/Handoff tools, and skills in Pi | `powercontext setup pi` |
| CLI | Setup, diagnostics, Server control, capability checks, and human Candidate review | `powercontext[cli,server]` |
| Python Client SDK | Typed async calls to a running Server | `powercontext[client]` |
| Core SDK | In-process Source, Artifact, Trigger, and composition contracts | base package |
| HTTP | Service integration from any language | `powercontext[server]` |
| MCP | Agent tools for Memory and work continuity | enabled by Server |

## Codex plugin

The project-context skill tells Codex when to search, remember, revise, retire, delegate, hand off, acknowledge, or
record an outcome. The prompt hook recalls relevant entries and captures user input as Source evidence. MCP tools
perform explicit operations. The plugin never starts or embeds the Server.

## Work continuity

The Server exposes one high-level loop across HTTP, the Python Client, and MCP:

```text
create_work_contract
  -> work
  -> handoff_current_work
  -> continue_handoff + acknowledge_handoff
  -> record_task_outcome
```

`create_work_contract` records the objective, scope, completion criteria, authority notes, and consequential open
questions for newly delegated work. `handoff_current_work` captures caller-inspected state and returns a temporary
Prepared Handoff; it does not publish a milestone. Call `commit_handoff` separately when the user wants a durable
milestone.

The receiver calls `continue_handoff` with a prepared, exact, or latest selection. When starting from latest, the
returned exact Revision is shown and inspected before acknowledgement. `acknowledge_handoff` accepts prepared or exact,
never latest. It refuses acceptance when any Handoff evidence is unavailable or when live-state, capability, and
authorization are not all `confirmed`. A receiver can instead record `needs_clarification` or `declined`. The receipt
and its three confirmations are untrusted observations; they grant no identity, tool, or execution authority.

`record_task_outcome` preserves `succeeded`, `partial`, `blocked`, `failed`, `cancelled`, or `unknown` and exact check
states. To cover a committed Handoff result, `handoff_receipt_ref` identifies the active accepted exact Receipt; an
unlinked Outcome in the same scope does not cover it. The operation stores a `task-outcome` Source that existing
Experience incubation can inspect, but does not generate or approve an Experience by itself. Integrations call it only
at a real completion or interruption boundary, not solely because a prompt, Stop event, or Session ended.

Claims and checks are either `declared` with no evidence or `verified` with exact same-scope citations. A readable
citation proves identity and availability, not freshness. Current instructions, live workspace state, capabilities,
and authorization still take precedence over all Work and Handoff records.

Each Handoff Report JSON Workstream projection also returns `handoff_revision_count`,
`handoff_history_truncated`, and `handoff_history`. History contains at most the latest 20 Revision summaries through
the frozen selection in ascending Revision order; the page presents them latest-first and refreshes every five
seconds. Unsent edits or an active Handoff action pause automatic refresh. The Codex scope resolver can bind the
current Git workspace once to a fixed Workstream scope. That binding takes precedence over Git remote and path
derivation, but remains below explicit scope configuration.

## DeepSeek Harness plugin

The project-context skill tells DeepSeek Harness when to search, remember, revise, or retire Memory. Before each model
step the plugin recalls relevant entries and captures user input as Source evidence. Named `pc_*` tools perform explicit
HTTP operations. The plugin never starts or embeds the Server.

## Pydantic AI adapter

The independent `powercontext-pydantic-ai` distribution contributes three Memory tools through the public Python
Client and can automatically prepend bounded `PreparedContext`. Optional capture stores redacted, bounded visible
model and completed tool events, performs checkpoint Flush, and flushes remaining Sources after the run. MCP needs no
adapter package but does not provide automatic context preparation, capture, or Flush. See
[Configure Pydantic AI](../how-to/configure-pydantic-ai.md).
## LangGraph adapter

`powercontext-langgraph` connects a LangGraph graph to a running Server through the public Python Client. It supplies
three components: `powercontext_tools()` returns `BaseTool` instances for model-initiated Memory read and write;
`PowerContextRecall` is a node or `pre_model_hook` that prepends one bounded `PreparedContext` as a system message
labelled untrusted historical evidence; and `PowerContextScope` is a dataclass for the graph `context_schema` that
carries the scope and per-run connection overrides. The recall node and tools read the active scope from the LangGraph
runtime and otherwise fall back to `POWERCONTEXT_LANGGRAPH_*` environment settings.

Scope resolution prefers an explicit `scope_id`, then a Git-remote-derived scope, and otherwise raises — the inverse of
the Codex resolver, because a deployed graph's working directory rarely identifies the project. `TOKEN` is a bare token
that the Client composes into `Authorization: Bearer`, unlike the `POWERCONTEXT_*_AUTHORIZATION` header used by the
Codex, Claude Code, and DeepSeek Harness plugins. Recall and the tools fail open: on Server unavailability the graph
still reaches its end and the tools return a short unavailable string. This release covers Memory read and write and
bounded recall only; automatic capture, checkpointing, and Handoff are out of scope. The adapter deliberately does not
implement `BaseStore`, whose get, upsert-by-key, and delete operations the Memory model does not provide. It never
starts or embeds the Server.

## LangChain middleware

`PowerContextMiddleware` uses LangChain's `AgentMiddleware` API. It injects one bounded PreparedContext into each
current model request without changing agent state. Automatic capture is disabled by default; pass `auto_capture=True`
to capture the latest user message and final plain-text or structured answer as Content Source evidence after a
successful run. Source-to-Memory activation remains a Server responsibility. Recall and capture fail open, and neither
path starts or embeds the Server. It ships independently as `powercontext-langchain`; the LangGraph adapter remains a
separate node-and-tool integration.

## Pi package

The native Pi package supplies the `project-context` skill, named `pc_*` Memory and Handoff tools, and `/pc`
diagnostics. Before each normal agent start, it requests one strict, bounded PreparedContext value and independently
captures an eligible user prompt as Source evidence. It does not synchronize Pi transcripts. Recall, capture, and
boundary flushing fail open; explicit durable writes require interactive confirmation.

## CLI

```text
powercontext setup codex
powercontext setup dsh
powercontext setup pi
powercontext doctor
powercontext doctor codex
powercontext doctor dsh
powercontext doctor pi
powercontext server run
powercontext ready
powercontext capabilities
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

`powercontext doctor` checks the package and Server without requiring an integration. `powercontext doctor codex`
checks the Codex CLI and PowerContext plugin explicitly. `powercontext doctor dsh` checks the DeepSeek Harness CLI
and that dump-config lists the plugin id `powercontext-dsh`. `powercontext doctor pi` checks the Pi executable and
that Pi lists the PowerContext package.

The `candidate` command group exposes the human Review Inbox. See [Review Candidates](../how-to/review-candidates.md)
for the ordered workflow to list, inspect, revise, approve, or reject Candidates.

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

For the relationship between evidence, Candidate versions, approved Revisions, recall, and export, see
[Experience and Skill lifecycle](../explanation/experience-and-skill-lifecycle.md).

## Scheduled Experience incubation

An integration can capture a completed task as a Content Source with metadata `"kind": "task-outcome"`. When the
Experience schedule is configured, APScheduler scans bounded Source windows and asks the configured schema-bound
pipeline for reusable situation, action, outcome, and lesson proposals. Each proposal cites exact Sources and enters
the Review Inbox as a pending Experience Candidate.

Experience incubation has its own persisted Source cursor, independent from Memory extraction. Candidate writes and
cursor advancement commit together; a generation or write failure leaves the window available for retry. Ordinary
prompt Sources are not Task Outcomes and are ignored by this job.

Scheduling stops at the review boundary. It never approves an Experience, includes pending content in
PreparedContext, derives a managed Skill, exports a Skill for an Agent target, or executes instructions. Skill authoring and
export remain explicit steps after the supporting Experience is approved.
Setup and verification steps are in
[Create and review an Experience](../how-to/create-and-review-experience.md).

## Managed Skill export to Agent targets

A configured generator can produce complete managed Skill content through `generate_skill`; a human or integration
can submit already-complete typed content through `propose_skill`. The proposal contains a name, discovery
description, instructions, validation checks, and exact Source or Artifact lineage. It remains a Candidate until a
reviewer approves the exact Candidate version.

Approval creates an immutable Skill Revision. It does not install the Skill or grant execution authority. To make one
approved Revision available to Codex or Claude Code, export it explicitly into a configured repository, user, or plugin
Skill target. The projection writes `SKILL.md` and `powercontext.json`; the manifest records the Agent kind, exact
Artifact reference and rendered-content hash. It refuses to replace an existing destination, so updates require an
intentional new export rather than a silent overwrite.

Codex can discover a repository-local export under `.agents/skills/<name>/SKILL.md`. The Artifact Revision remains
the content authority; Claude Code uses `.claude/skills/<name>/SKILL.md` for the equivalent project target. Both
directories are host-local projections that can be rebuilt from the same exact Revision.
See [Create and export a managed Skill](../how-to/create-and-export-skill.md) for the procedure.

## External Agent-native Skills

External Skills remain authoritative in their original local packages. With explicitly configured Agent targets, the
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
| Managed Skill | Exact approved Artifact Revision | Yes for generate/evolve/import/fork; no for typed `propose` | Yes | Exact read and explicit Agent projection |
| Agent projection | Its source managed Skill Revision | No | No additional review | Rebuildable Codex or Claude Code host-local copy |

## Core SDK

The base `powercontext` package exports Python protocols and models for applications that own their composition root.
It does not select storage, scheduling, transport, or inference on the application's behalf. Use `builtin` when you
want the supplied SQLite or OceanBase-backed implementation in the same process.

## HTTP and MCP

The Server publishes its OpenAPI document at `/openapi.json`, readiness at `/health/ready`, capabilities at
`/v1/capabilities`, and Streamable HTTP MCP at `/mcp` by default. HTTP is the complete application contract. MCP is a
curated agent-facing projection of Memory and Candidate Review operations. The five Candidate Review operations use the
same validation, `expected_version` concurrency checks, and approval transaction over HTTP and MCP.
Readiness is `ready` with HTTP 200 when all checks pass, `degraded` with HTTP 200 when only configured inference checks
fail, and `not_ready` with HTTP 503 when the Runtime or database fails. Dependency checks use `ready`, `unavailable`,
`timeout`, or `misconfigured`; an intentionally unbound Runtime reports `not_ready` for the `runtime` check.
Experience and Skill generation, exact reads, external Registry operations, and low-level proposal operations remain
HTTP-only.
`POST /v1/context/prepare` and the matching Python Client method expose final ephemeral `PreparedContext` over HTTP;
the Runtime recalls active Memory and approved Experience heads, owns their shared selection and total output budget,
and intentionally does not project the operation as an MCP tool. The public schema remains
`powercontext.prepared-context.v1`; Experience items carry an exact Artifact reference inside the prepared content.
