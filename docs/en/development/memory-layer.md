# Using the Builtin Memory layer

The Builtin Memory family stores reusable entries as immutable Artifact revisions. The `builtin` extra includes the
complete runtime and both supported database integrations. Remote applications should use the Server API described in the
[remote access guide](remote-access-implementation.md).

## Select a database

Install the built-in implementation:

```bash
uv add "powercontext[builtin]"
```

SQLite is the default. `open_builtin_runtime()` owns the selected database profile and returns the same
`BuiltinRuntime` interface for either database:

```python
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    open_builtin_runtime,
)


async def save_note() -> None:
    config = BuiltinConfig(
        database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db")
    )
    async with open_builtin_runtime(config) as runtime:
        result = await runtime.memory.for_scope("project-alpha").remember(
            RememberMemoryRequest(
                entries=(
                    MemoryEntryInput(
                        kind="decision",
                        text="Use one composition root for the process.",
                    ),
                )
            )
        )
        assert result.memory_ref.revision == 1
```

The scope ID selects an isolated Source journal, Memory lifecycle, and Trigger cursor within the database.

## Write and evolve entries

`ScopedMemoryApplication.remember()` accepts explicit `MemoryEntryInput` values. Source-based extraction follows a
separate path: capture Sources, then flush the pending Source window with a configured candidate pipeline.

The result contains the new immutable Memory reference and the changed entry. Use its citation for later mutations:

```python
from powercontext.builtin.runtime import ReviseMemoryEntryRequest

memory = runtime.memory.for_scope("project-alpha")
entries = await memory.list()
current = entries.entries[0]
revised = await memory.revise(
    ReviseMemoryEntryRequest(
        citation=current.citation,
        kind=current.entry.kind,
        text="Use PowerContext as the only composition root.",
        reason="Clarify ownership.",
    )
)
```

`retire()` marks an entry inactive without deleting immutable content. `changes()` returns compact revision changes.
Expected revisions and citations preserve optimistic concurrency without requiring callers to rebuild references.

## Capacity and tombstone compaction

`await runtime.memory.for_scope(scope_id).capacity()` reports the current head's active entries, total manifest
entries, exact canonical content bytes, eligible tombstones, budget, and exceeded dimensions. The direct service
method `await service.capacity(memory)` measures the exact Revision supplied. Remote callers use
`POST /v1/memory/capacity` with `{"scope_id": "project-alpha"}`, or
`PowerContextClient.get_memory_capacity(GetMemoryCapacityRequest(scope_id="project-alpha"))`.
A Scope without a Memory returns 404; reading capacity does not create one.

`RuntimeConfig` supplies deployment-wide defaults:

| Setting | Default |
| --- | --- |
| `memory_max_active_entries` | 5,000 |
| `memory_max_manifest_entries` | 10,000 |
| `memory_max_manifest_bytes` | 4,194,304 |
| `memory_compaction_enabled` | `False` |
| `memory_compaction_min_tombstone_revisions` | 10 |
| `memory_max_history_revisions` | 100 |

The capacity defaults bound growth of each Revision; they do not guarantee append latency or cap total database size.
Retained historical manifests keep accumulating. Tune deployment budgets against representative backend measurements.

Active-entry limits cannot exceed manifest-entry limits. Explicit writes, extraction, and generic Artifact management
share the budget. A write is refused only when it exceeds a limit and increases that dimension relative to the base.
The deterministic priority is bytes, manifest entries, then active entries. HTTP returns
`409 memory_capacity_exceeded` with `dimension`, `limit`, and `observed`; the rejected write persists no content.
`manifest_bytes` includes the complete canonical Revision content, including its changes and reasons.

`forget()` and `organize()` remain available over budget. `reactivate()` checks active-entry growth only.
Compaction removes aged, untagged inactive entries from the current manifest. Enable it explicitly on `RuntimeConfig`,
or construct a `MemoryService` with `MemoryCompactionPolicy(enabled=True)`, then preview the operation:

```python
preview = await service.compact(memory, dry_run=True, limit=100)
result = await service.compact(memory, limit=100)
memory = result.memory
```

A preview works while compaction is disabled and writes no Revision. Eligibility counts completed Revision advances:
an entry deactivated at Revision 2 qualifies at Revision 12 with the default age of 10. Reactivation and a subsequent
deactivation restart the window. Only this recent window is read. No-op maintenance does not advance the Revision;
if every tombstone is too recent, explicitly configure `memory_compaction_min_tombstone_revisions=0` (or
`MemoryCompactionPolicy(enabled=True, min_tombstone_revisions=0)`) to preview and compact immediately. Zero bypasses
only the recovery window: active entries and tagged tombstones remain protected. Keep the default window unless
immediate recovery is needed, because a compacted entry cannot be reactivated.
Tags protect inactive entries; a tag added during compaction aborts the transaction with
`CapabilityNotSupportedError("compaction-tag-conflict")` so the caller can preview again.

Compaction preserves all entry bodies, prior Revisions, and exact citations. A removed entry cannot be reactivated or
listed in the current manifest. Each removal records the additive `compact` change operation; update consumers that
exhaustively enumerate change operations before enabling compaction. Compaction is available in process only.
`reclaimed_bytes` is the signed difference between complete canonical contents. New audit records or a long reason
can outweigh a small directory reduction; subsequent revisions no longer carry those compaction records.

`MemoryService.revisions()` refuses histories longer than `memory_max_history_revisions` with
`CapabilityNotSupportedError("history-window")` before loading them. Stored history and exact Revision reads remain
available; results are never silently truncated. The default 100 Revisions can already contain about 400 MiB of
canonical content near the 4 MiB budget, before object overhead. This is a read fan-out bound, not a hard memory limit;
lowered budgets and relief operations can leave Revisions above the byte budget. Increase the configurable history
limit only when the caller can afford the complete snapshots. This bound does not paginate `entries()` or `changes()`.

## Search, expand, and cite

SQLite and OceanBase both initialize a full-text index, so either database can search without an embedding model:

```python
from powercontext.builtin.runtime import SearchMemoryRequest

result = await runtime.memory.for_scope("project-alpha").search(
    SearchMemoryRequest(query="composition root", mode="fts")
)
```

Each hit contains the exact Memory revision, entry identity, and entry version used for ranking. The Runtime returns
the same citation fields through list and exact-read operations.

`mode="auto"` chooses the strongest available mode and can fall back to FTS if query embedding is temporarily
unavailable. Explicit `vector` and `hybrid` requests fail when the configured profile does not provide that
capability.

## Enable SQLite vector search

SQLite vector search is enabled when an embedding model is supplied. The `powercontext[builtin]` extra bundles
`sqlite-vec`, so no extension path or separate native-library installation is required:

```python
config = BuiltinConfig(
    database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db")
)
async with open_builtin_runtime(
    config,
    embedding_model=embedding_model,
) as runtime:
    ...
```

The SQLite profile composes FTS5 and sqlite-vec strategies. It reports `fts`, `vector`, and `hybrid` through Memory
capabilities.
Stored projections and query vectors must use the same `EmbeddingProfile`, including model name, dimension, distance,
and normalization. Changing that profile requires rebuilding projections before vector search resumes.

Call `MemoryService.rebuild_projections()` to reconstruct derived search data from authoritative Memory revisions.
Revision and entry tables remain the source of truth.

## Use OceanBase persistence

Select OceanBase with `OceanBaseConfig`. No Server or Runtime code changes:

```python
from pydantic import SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime

config = OceanBaseConfig(
    url=SecretStr(
        "mysql+aoceanbase://user:password@127.0.0.1:2881/powercontext?charset=utf8mb4"
    )
)

async with open_builtin_runtime(
    BuiltinConfig(database=config),
    embedding_model=embedding_model,
) as runtime:
    memory = runtime.memory.for_scope("project-alpha")
```

The OceanBase profile uses the same index composition as SQLite. Its full-text strategy is always available. Supplying
an embedding model adds a `VECTOR` projection and HNSW strategy, enabling `vector` and `hybrid` modes. SQLite FTS5 and
OceanBase FULLTEXT therefore serve the same Runtime and Server search calls; sqlite-vec and HNSW do the same for vector
search.

## Operational checks

Before serving requests, verify:

- the selected profile opens and initializes successfully;
- each tenant or project maps to the intended scope ID;
- scheduled extraction has a candidate pipeline;
- SQLite vector search has a matching embedding model;
- OceanBase vector search has a matching embedding model;
- capability responses match the indexes actually initialized;
- database and scheduler resources close with the process lifecycle.
