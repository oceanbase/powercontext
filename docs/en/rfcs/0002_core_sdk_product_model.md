- Proposal Name: `core_sdk_product_model`
- Start Date: 2026-07-07
- RFC PR: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/pull/2)
- Tracking Issue: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/issues/2)
- Appendix I: [Types and Interfaces](0002_appendix_types_and_interfaces.md)
- Appendix II: [Execution and Integration Guidelines](0002_appendix_advanced_execution_and_integration.md)

# Summary

RFC 0001 defines PowerContext as a work-context layer for humans and agents. This RFC defines only the product and
architecture boundaries of the first Python Core SDK:

- Source stores stable references to external work material.
- An Artifact is maintainable derived context that evolves through immutable Revisions.
- Core records the Sources and upstream Artifact Revisions used directly by an Artifact Revision.
- Memory and Handoff are Artifact Families with fixed Core compositions.
- SQLAlchemy, fsspec, and Pydantic AI retain their native objects and lifecycles instead of being copied into parallel
  Core abstractions.

RFC 0001 treats Trigger as a product concept. This RFC does not define its public SDK shape. Internal conditions and
dispatch mechanisms used by the first implementation for background derivation are implementation policies, not a
public Trigger contract.

Only the parent document is normative. Appendix I records the current API sketch, and Appendix II gives integration
guidance. Neither appendix is normative.

# Motivation

Ordinary users need a short path for Source ingestion and context retrieval. That does not require Core to take
ownership of current-session state, agent frameworks, scheduling, workflows, or storage backends.

PowerContext supplies durable, cross-session context, but it is not the authoritative state of the current session. The
agent harness still owns current messages, tool state, immediate context, and the model-call lifecycle.

# Guide-level explanation

## Everyday path

For ordinary callers, everyday ingestion can stop after committing a Source:

```python
pc = await PowerContext.open("powercontext.db", model=model)
async with pc:
    source = await pc.sources.add(source_input)
```

A successful `Sources.add()` call guarantees only that the Source has been committed. It does not guarantee that Memory
or another Artifact has been produced. Core may use builtin policies to group committed Sources and derive Artifacts
later, but when a policy runs, which inputs it selects, and whether it creates a new Revision are not postconditions of
`Sources.add()`.

Before a model call, the agent harness may use best-effort retrieval of the Memory or other Artifacts visible at that
time. It must not assume that recently committed Sources have already been processed.

## Explicit generation

When the host must await Artifact generation in the current workflow, it can invoke the domain operations explicitly:

```python
from powercontext import GitCommit, PowerContext

pc = await PowerContext.open("powercontext.db", model=model)
async with pc:
    source_input = await pc.sources.resolve(GitCommit(repository=repository, revision="HEAD"))
    source = await pc.sources.add(source_input)
    decision = await pc.artifacts.add(
        "decision",
        {"summary": "Use SQLite through SQLAlchemy."},
        sources=(source,),
    )
    memory = await pc.memory.remember(
        sources=(source,),
        artifacts=(decision,),
    )
    artifacts = () if memory is None else (memory,)
    handoff = await pc.handoff.prepare(
        "Continue the implementation",
        sources=(source,),
        artifacts=artifacts,
    )
    document = pc.handoff.render(handoff, audience="human")
```

Explicit generation is for workflows with a timing requirement. It is not a required step in ordinary Source
ingestion.

# Reference-level explanation

## Product objects

| Object | Semantics | Boundary |
| --- | --- | --- |
| Source | Stable reference and index information for external work material | The provider or host continues to own the original body |
| Artifact | Derived context that can be searched and maintained | Evolves through immutable Revisions |
| Memory | Artifact Family for reuse in later tasks | Does not replace current-session state |
| Handoff | Artifact Family for the next participant | Human and agent views come from the same Revision |

Memory and Handoff inherit Artifact identity, Revision, and lineage semantics. An Artifact Family may define its own
content model and user actions. Their services share Artifact read operations but keep Family-specific writes and
search behavior.

## Core invariants

- Source commit is idempotent on `(source_type, uri)` within one Catalog.
- An Artifact Revision is immutable after commit, and `ArtifactRef` always addresses an exact Revision.
- `revise()` performs optimistic concurrency against the base Revision and rejects stale writes.
- Every Revision records the Sources and upstream Artifact Revisions used directly by that computation.
- Lineage is derived from complete persisted objects supplied by the caller. It is not inferred from traces, call
  graphs, or workflow topology.
- An initial Memory calculation with no changes returns `None`. An unchanged existing Memory returns its current
  Revision.
- The Handoff `objective` comes from the call argument and cannot be rewritten by model output.

## Public actions

| Scope | Actions |
| --- | --- |
| Source | `resolve`, `read`, `add`, `get`, `list` |
| Artifact | `add`, `revise`, `get`, `revisions`, `list`, `search` |
| Memory | `remember`, `forget`, `organize`, `get`, `revisions`, `list`, `search` |
| Handoff | `prepare`, `get`, `revisions`, `list`, `render` |

Appendix I records the current prototype signatures. PMC review may still change those signatures before this RFC is
accepted.

## Composition root and upstream objects

- `await PowerContext.open()` targets local use and creates and owns a SQLite `AsyncEngine`.
- `await PowerContext.from_backends()` accepts a caller-owned SQLAlchemy `AsyncEngine` and fsspec
  `AbstractFileSystem`.
- PowerContext exposes the injected filesystem unchanged. It adds no file facade or generic storage lifecycle.
- A file-backed Artifact Family defines the product semantics of its paths or URIs. Artifact itself is unaware of file
  storage.
- `model` and `embedding_model` use native Pydantic AI model objects or names. Core defines no separate abstraction for
  API keys, base URLs, or provider configuration.

The first Catalog implementation uses SQLite and defines only schema version `1`; schema migration is outside this RFC.

## Extension boundary

Typed `SourceProvider[T]` is the only open-ended Core data-acquisition extension in the first version. A provider resolves
native input into `SourceInput` and reads a transient textual representation of a committed Source when Core needs it.
Providers are registered and fixed when `PowerContext` is constructed.

Memory extraction, Memory consolidation, and Handoff generation use fixed internal Core pipelines. Callers may select
models, but the first version defines no public protocol for replacing those generation pipelines.

Each Catalog backend owns the retrieval implementation that matches its capabilities. Search modes and ranking behavior
in the current prototype are not yet a stable cross-backend contract.

## Background processing and Trigger

Core may use builtin policies to derive Artifacts after Source commits, for example by initiating processing after an
accumulation or periodic condition. Exact thresholds, batches, target Artifact identities, concurrency, and failure
handling remain internal policies that require further validation.

These internal conditions do not implement the user-facing Trigger concept from RFC 0001 and do not establish a public
hook or scheduler extension contract.

# Drawbacks

- Background derivation is best-effort and provides no Source-to-Artifact read-after-write guarantee.
- Different Artifact Families have different domain actions, so callers must understand each Family's semantics.
- Retrieval capabilities may differ between Catalog backends.
- Each agent framework still needs a small native adapter.

# Rationale and alternatives

Core exposes product objects through concrete Family services, accepts native upstream objects, and opens only the typed
Source provider extension point. Memory and Handoff use fixed generation pipelines. Core therefore needs to maintain
only the work-context invariants.

Repositories, schedulers, agent protocols, and generation graphs remain with their upstream systems. Their capabilities
and lifecycles are not stable enough to justify another Core abstraction yet.

# Unresolved questions

- How should builtin background derivation select Source batches and target Artifact identities, and expose failures?
- Does `Artifacts.search()` have sufficiently stable semantics across Families?
- Which Memory search guarantees belong across backends, and which modes should remain backend-specific capabilities?
- Should `producer` remain a public Artifact write argument?
- When will the product Trigger from RFC 0001 have sufficiently clear user actions and lifecycle to justify a separate
  SDK contract?

# Future possibilities

Later RFCs may define Artifact Families, Catalog backends, remote fsspec backends, and separate Trigger or durable
processing designs. Each proposal must start from a concrete product action and explain why an upstream abstraction
cannot carry it directly.
