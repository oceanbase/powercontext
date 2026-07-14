- Proposal Name: `core_sdk_types_and_interfaces`
- Start Date: 2026-07-10
- RFC PR: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/pull/2)
- Tracking Issue: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/issues/2)
- Parent RFC: [RFC 0002: Core SDK Product Model](0002_core_sdk_product_model.md)
- Related Appendix: [Execution and Integration Guidelines](0002_appendix_advanced_execution_and_integration.md)

# Status

This API sketch is not normative. It shows PMC how RFC 0002 appears in the current prototype. Names, parameters,
defaults, and return types may change before the RFC is accepted. If the appendix and implementation differ, source
code describes the prototype and the parent RFC describes the design under review.

# Summary

The prototype exposes `PowerContext`, its four domain services, persisted value objects, and typed Source providers.
Memory and Handoff generation use fixed internal pipelines. Internal generation requests, change sets, scheduling
conditions, and Catalog records are not public types.

# Guide-level explanation

Ordinary callers only need to create `PowerContext` and commit a Source:

```python
pc = await PowerContext.open("powercontext.db", model=model)
async with pc:
    source = await pc.sources.add(source_input)
```

This path does not guarantee immediate Artifact generation. Applications call `memory.remember()` or
`handoff.prepare()` explicitly only when the current workflow must await generation.

# Prototype interface sketch

## Package root

Ordinary callers import the composition root, main value objects, builtin inputs, and stable product errors from the
`powercontext` package root. Provider authors may also import `SourceInput` and `SourceProvider` from the package root.
Domain services are obtained through `PowerContext` attributes and are not normally constructed by callers.

## Composition root

```python
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from fsspec import AbstractFileSystem
from pydantic_ai.embeddings import EmbeddingModel, KnownEmbeddingModelName
from pydantic_ai.models import KnownModelName, Model
from sqlalchemy.ext.asyncio import AsyncEngine

from powercontext import SourceProvider


class PowerContext:
    filesystem: AbstractFileSystem
    sources: Sources
    artifacts: Artifacts
    memory: Memories
    handoff: Handoffs

    @classmethod
    async def from_backends(
        cls,
        *,
        engine: AsyncEngine,
        filesystem: AbstractFileSystem,
        model: Model | KnownModelName | str | None = None,
        embedding_model: EmbeddingModel | KnownEmbeddingModelName | str | None = None,
        source_providers: Iterable[SourceProvider[Any]] = (),
    ) -> Self: ...

    @classmethod
    async def open(
        cls,
        path: str | Path,
        *,
        model: Model | KnownModelName | str | None = None,
        embedding_model: EmbeddingModel | KnownEmbeddingModelName | str | None = None,
        source_providers: Iterable[SourceProvider[Any]] = (),
    ) -> Self: ...

    async def close(self) -> None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
```

`open()` creates and owns the SQLite engine. `from_backends()` does not take lifecycle ownership of the injected engine
or filesystem. `filesystem` is the native fsspec object and is not wrapped by a PowerContext facade.

`model` and `embedding_model` accept native Pydantic AI model inputs. Standard credentials may come from the
environment. For explicit credentials, endpoints, or clients, callers construct and pass the upstream Model object.

## Value objects

| Type | Main fields | Semantics |
| --- | --- | --- |
| `SourceInput` | `source_type`, `uri`, `observed_at`, `metadata` | Provider-resolved Source input that has not been committed |
| `Source` | `id`, the `SourceInput` fields, and `created_at` | Committed stable reference to external material |
| `ArtifactRef` | `artifact_id`, `revision` | Exact Artifact Revision reference |
| `Artifact[T]` | `id`, `revision`, `family`, `content`, `producer`, `created_at`, `source_ids`, `dependencies` | Concrete Revision snapshot |
| `ArtifactSummary` | Identity, latest Revision, and timestamps | Lightweight `list()` result |
| `ArtifactHit` | `artifact_ref`, `family`, `score`, `excerpt` | Generic Artifact search result in the current prototype |
| `Memory` | `Artifact[MemoryContent]` | Memory Family Revision snapshot |
| `MemoryHit` | `memory_ref`, `entry_id`, `text` | Ordered Memory search result |
| `Handoff` | `Artifact[HandoffContent]` | Handoff Family Revision snapshot |

`Metadata` and Artifact content use Pydantic's `JsonValue` serialization boundary. A frozen dataclass does not promise
recursive freezing of containers supplied by callers.

## Domain services

```python
from collections.abc import Sequence


class Sources:
    async def resolve(self, value: object) -> SourceInput: ...

    async def add(self, source_input: SourceInput) -> Source: ...

    async def get(self, source_id: str) -> Source: ...

    async def list(
        self,
        *,
        source_type: str | None = None,
        limit: int = 100,
    ) -> tuple[Source, ...]: ...

    async def read(self, source: Source) -> str: ...


class Artifacts:
    async def add(
        self,
        family: str,
        content: ContentT,
        *,
        producer: str = "powercontext",
        sources: Sequence[Source] = (),
        dependencies: Sequence[Artifact[object]] = (),
    ) -> Artifact[ContentT]: ...

    async def revise(
        self,
        artifact: Artifact[object],
        content: ContentT,
        *,
        producer: str = "powercontext",
        sources: Sequence[Source] = (),
        dependencies: Sequence[Artifact[object]] = (),
    ) -> Artifact[ContentT]: ...

    async def get(self, artifact: str | ArtifactRef) -> Artifact[object]: ...

    async def revisions(self, artifact_id: str) -> tuple[Artifact[object], ...]: ...

    async def list(
        self,
        *,
        family: str | None = None,
        limit: int | None = 100,
    ) -> tuple[ArtifactSummary, ...]: ...

    async def search(
        self,
        query: str,
        *,
        family: str | None = None,
        limit: int = 10,
    ) -> tuple[ArtifactHit, ...]: ...


class Memories:
    async def remember(
        self,
        *,
        memory: Memory | None = None,
        sources: Sequence[Source] = (),
        artifacts: Sequence[Artifact[object]] = (),
    ) -> Memory | None: ...

    async def forget(self, memory: Memory, entry_ids: Sequence[str]) -> Memory: ...

    async def organize(self, memory: Memory) -> Memory: ...

    async def get(self, memory: str | ArtifactRef) -> Memory: ...

    async def revisions(self, memory_id: str) -> tuple[Memory, ...]: ...

    async def list(self, *, limit: int | None = 100) -> tuple[ArtifactSummary, ...]: ...

    async def search(
        self,
        query: str,
        *,
        memory: ArtifactRef | None = None,
        limit: int = 10,
        mode: MemorySearchMode = "auto",
    ) -> tuple[MemoryHit, ...]: ...


class Handoffs:
    async def prepare(
        self,
        objective: str,
        *,
        sources: Sequence[Source] = (),
        artifacts: Sequence[Artifact[object]] = (),
    ) -> Handoff: ...

    async def get(self, handoff: str | ArtifactRef) -> Handoff: ...

    async def revisions(self, handoff_id: str) -> tuple[Handoff, ...]: ...

    async def list(self, *, limit: int | None = 100) -> tuple[ArtifactSummary, ...]: ...

    def render(
        self,
        handoff: Handoff,
        *,
        audience: Literal["human", "agent"],
    ) -> str: ...
```

Memory and Handoff values inherit Artifact fields and reference semantics. Their services expose `get`, `revisions`,
and `list`. Memory uses `remember`, `forget`, and `organize` for writes, while Handoff uses `prepare`. Search may differ
by backend and Family.

`get(id)` reads the latest Revision; `get(ref)` reads an exact Revision. Normal semantic writes pass complete persisted
objects so Core can derive and validate lineage.

An initial no-op from `remember()` returns `None`; a no-op for existing Memory returns the existing Revision. `render()`
is a pure projection: it makes no model call and creates no Artifact.

## Typed Source provider

```python
from typing import Protocol, TypeVar


SourceT = TypeVar("SourceT")


class SourceProvider(Protocol[SourceT]):
    @property
    def input_type(self) -> type[SourceT]: ...

    @property
    def source_type(self) -> str: ...

    async def resolve(self, value: SourceT) -> SourceInput: ...

    async def read(self, source: Source) -> str: ...
```

Providers use exact input-type and `source_type` routing. `resolve()` may perform upstream I/O but does not commit the
Source. `read()` returns the transient textual representation needed by one Core generation. Duplicate input types or
`source_type` values should fail during construction.

# Behavioral guidelines

- Except for the native `filesystem` and pure projections, Core public I/O operations use async APIs.
- `Sources.add()` only commits a Source and provides no Source-to-Artifact read-after-write guarantee.
- A file-backed Family uses `PowerContext.filesystem` directly and defines paths or URIs in its own content model.
- Memory and Handoff generation are fixed internal pipelines; internal requests and change sets are not extension
  contracts.
- Unless Core explicitly translates an exception to a stable product error, upstream exceptions retain their upstream
  types and causes.

# Provisional prototype details

Current SQLite code exposes these provisional shapes:

- Generic `Artifacts.search()` semantics and scores.
- `MemorySearchMode = Literal["auto", "lexical", "semantic", "hybrid"]`.
- Capability-error granularity when a search capability is unavailable.
- Whether ordinary Artifact writers continue to supply `producer`.
