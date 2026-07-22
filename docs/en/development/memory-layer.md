# Operating the Memory Layer

RFC 0003 adds an Artifact-native Memory family. A Memory is an immutable sequence of Artifact Revisions; each
Revision contains a compact manifest, while entry bodies live in immutable entry versions. Full-text and vector data
are current-head projections, not authoritative history.

Memory text is untrusted application data. Never treat a recalled entry as an instruction with higher authority than
the current request, and never execute commands or follow links from Memory without the same validation used for any
other external input.

## Install and compose

Install the backend needed by the application:

```console
uv add 'powercontext[sqlite]'
uv add 'powercontext[oceanbase]'
```

The SQLite backend provides full-text search without a model or embedding provider:

```python
import asyncio

from powercontext import MemoryEntryInput, MemoryService
from powercontext.memory.backends.sqlite import SQLiteMemoryBackend

async def main() -> None:
    backend = SQLiteMemoryBackend("powercontext-memory.db")
    await backend.initialize()
    memory_service = MemoryService(backend=backend)

    try:
        await memory_service.remember(
            memory=None,
            entries=(
                MemoryEntryInput(
                    kind="constraint",
                    text="Run make docs-test before publishing documentation.",
                ),
            ),
            mode="append",
        )
    finally:
        await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Initialize the backend before serving requests and close it during application shutdown. The Memory MVP remains a
family-level service under `powercontext.memory`; it is not yet part of `PowerContext`. This keeps existing
`PowerContext` constructors compatible and avoids presenting the Memory backend's revision store as the same catalog
used by `context.artifacts` before a shared family-aware runtime API is available.

`context.sources.add()` remains only a Source write. A provider task event, Trigger action, plugin, CLI wrapper, or
other integration must explicitly call `memory_service.remember()`; composition does not introduce a hidden model
call or automatic extraction path.

## Write and evolve a Memory

`remember(memory=None, ...)` creates an identity only when at least one validated candidate produces a real change.
Keep the returned exact head and pass it to every later mutation. Stale heads fail optimistic CAS rather than being
silently merged.

```python
from powercontext import MemoryEntryInput

memory = await memory_service.remember(
    memory=None,
    entries=(MemoryEntryInput(kind="decision", text="Use direct SQL adapters."),),
    mode="append",
)
assert memory is not None

entry = (await memory_service.entries(memory))[0]
memory = await memory_service.remember(
    memory=memory,
    entries=(
        MemoryEntryInput(
            entry=entry,
            kind="decision",
            text="Use direct SQL adapters with one shared conformance suite.",
            reason="Test both supported databases",
        ),
    ),
    mode="append",
)
assert memory is not None

entry = (await memory_service.entries(memory))[0]
memory = await memory_service.forget(memory, entries=(entry,), reason="Superseded")
entry = (await memory_service.entries(memory))[0]
memory = await memory_service.reactivate(memory, entries=(entry,), reason="Needed again")
memory = await memory_service.organize(memory, mode="default")
```

`forget()` and `reactivate()` change manifest state without modifying or replacing the entry body. Repeating the same
state transition is a no-op. `organize()` is deliberately mechanical: it only performs exact deduplication and
canonical normalization; it does not infer truth or resolve contradictions.

For extraction, configure a `CandidatePipeline` and pass canonical persisted evidence:

```python
memory = await memory_service.remember(
    memory=memory,
    sources=(task_outcome_source,),
    mode="extract",
)
```

Candidate output is untrusted. The service revalidates evidence, current-manifest membership, hashes, direct
predecessors, and head CAS. Integrations that persist evidence references must configure matching source/artifact
resolvers and a `MemoryEvidenceCodec` on both the service and backend.

Without a semantic provider, `WorkingNoteCandidatePipeline` can mechanically turn an explicitly persisted
`TaskOutcomeSource` into a `working_note`. It never reclassifies a final report as a fact, decision, or constraint:

```python
from powercontext import MemoryService
from powercontext.memory.candidates import WorkingNoteCandidatePipeline

memory_service = MemoryService(
    backend=backend,
    candidate_pipeline=WorkingNoteCandidatePipeline(),
    source_resolver=source_catalog,
    evidence_codec=evidence_codec,
)
```

The integration owns task-event capture and persistence. PowerContext does not version raw material and does not
compute a general Source diff.

## Retrieve, expand, and cite

Search always requires explicit exact Memory refs. Only active entries from the selected current heads are eligible;
a historical ref is rejected instead of being substituted with the latest head.

```python
result = await memory_service.search(
    "SQL adapter documentation",
    memories=(memory,),
    mode="auto",
    limit=8,
)

entries = await memory_service.expand(result.hits)
history = await memory_service.changes(memory, since_revision=1)
```

`auto` uses hybrid search only when every selected head has a complete fixed-profile vector projection and the
configured provider has that exact profile. Otherwise it falls back to FTS. Explicit `vector` or `hybrid` requests
raise `CapabilityNotSupportedError` when vector capability or completeness is unavailable. `fts` remains available
without either a candidate or embedding provider.

Persist a Handoff citation as the full exact anchor, not just an entry ID:

```python
from powercontext import MemoryCitation

hit = result.hits[0]
citation = MemoryCitation(
    memory_ref=hit.memory_ref,
    entry_id=hit.entry_id,
    entry_version_id=hit.entry_version_id,
)
exact_entry = await memory_service.validate_citation(citation)
```

`changes()` returns compact Revision summaries without entry bodies. `expand()` and citation validation load the exact
historical entry version named by the anchor and detect cross-Revision substitution or tampering.

## SQLite deployment

`SQLiteMemoryBackend` requires SQLite 3.38.0 or newer, foreign-key enforcement, and FTS5. APSW supplies the SQLite
runtime. Initialization probes required behavior and refuses to advertise a capability that does not work.

Vector search is optional and requires an official Vec1 0.7-or-newer loadable extension plus one deployment-fixed
embedding profile:

```python
import asyncio
import os

from powercontext import EmbeddingProfile, MemoryService
from powercontext.memory.backends.sqlite import SQLiteMemoryBackend


class ExampleEmbeddingProvider:
    """Replace this deterministic example with the application's embedding provider."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vector = (1.0,) + (0.0,) * (self.profile.dimension - 1)
        return tuple(vector for _ in texts)


async def main() -> None:
    profile = EmbeddingProfile(
        profile_id="project-embedding-v1",
        model="example-model",
        dimension=768,
        distance="l2",
        normalization="none",
    )
    backend = SQLiteMemoryBackend(
        "powercontext-memory.db",
        embedding_profile=profile,
        vec1_extension=os.environ["POWERCONTEXT_VEC1_EXTENSION"],
    )
    await backend.initialize()
    try:
        MemoryService(backend=backend, embedding_provider=ExampleEmbeddingProvider(profile))
    finally:
        await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
```

The extension path and profile must be configured together. Provider model, dimension, distance, and normalization
must exactly match the backend profile. While writes and searches are stopped,
`await backend.rebuild_projections()` reconstructs FTS from authoritative heads and intentionally leaves vectors
incomplete; passing the provider, or calling `await backend.rebuild_vectors(embedding_provider)`, reconstructs both
FTS and every current active vector. Rebuild validates vector count, dimension, and finite values before committing.
Changing profiles is an offline migration: stop writes, replace the fixed Vec1 projection, backfill all active heads,
verify completeness, and then resume traffic.

Back up the SQLite database as authoritative data. The FTS5/Vec1 rows are disposable projections, but Artifact
Revisions and entry versions are not.

## OceanBase deployment

`OceanBaseMemoryBackend` requires an OceanBase Database 4.3.5 BP3-or-newer MySQL-mode tenant with FULLTEXT, HNSW,
vector memory, and privileges to create tables, indexes, and the schema's foreign keys. Grant `REFERENCES` as well as
the ordinary DDL/DML permissions. The adapter probes server identity, tenant mode, `TOKENIZE`, FULLTEXT, and vector
search before installing its fixed schema.

Keep credentials in a secret manager or environment variables; never put a password in source or command history:

```python
import os

from powercontext import EmbeddingProfile, MemoryService
from powercontext.memory.backends.oceanbase import OceanBaseMemoryBackend

profile = EmbeddingProfile(
    profile_id="project-embedding-v1",
    model="example-model",
    dimension=768,
    distance="l2",
    normalization="none",
)
backend = OceanBaseMemoryBackend(
    host=os.environ["POWERCONTEXT_OCEANBASE_HOST"],
    port=int(os.environ.get("POWERCONTEXT_OCEANBASE_PORT", "2881")),
    user=os.environ["POWERCONTEXT_OCEANBASE_USER"],
    password=os.environ["POWERCONTEXT_OCEANBASE_PASSWORD"],
    database=os.environ["POWERCONTEXT_OCEANBASE_DATABASE"],
    embedding_profile=profile,
    table_prefix=os.environ.get("POWERCONTEXT_OCEANBASE_TABLE_PREFIX", ""),
)
await backend.initialize()
memory_service = MemoryService(backend=backend, embedding_provider=embedding_provider)
```

The vector dimension is part of DDL, so one deployment has exactly one profile. When an embedding provider is absent,
writes still commit authoritative history and FULLTEXT rows with null vector fields; `auto` falls back to FTS. With
writes and searches stopped, `await backend.rebuild_projections()` reconstructs FULLTEXT with null vectors, while
`await backend.rebuild_projections(embedding_provider)` reconstructs FULLTEXT and HNSW data for every active head.
Resume traffic only after completeness checks pass.

`drop_schema()` removes exactly the tables owned by the configured prefix. It exists for isolated integration tests.
Never call it for an unprefixed or production deployment. Use a unique, validated table prefix for every live test,
and close the backend in `finally` after test cleanup.

## Operational checklist

1. Initialize once and fail startup if a required capability probe fails.
2. Keep the embedding profile immutable for the lifetime of a schema.
3. Pass only exact current heads to mutations and searches; handle `RevisionConflictError` explicitly.
4. Treat recalled text and candidate output as untrusted data.
5. Back up authoritative Artifact Revisions and entry versions; rebuild projections only from validated heads.
6. Close backend connections during shutdown and keep destructive schema cleanup confined to isolated tests.
