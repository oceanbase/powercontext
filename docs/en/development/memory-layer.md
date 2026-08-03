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

## Enable SQLite Vec1

SQLite vector search is enabled only when both a Vec1 0.7 or newer loadable extension and an embedding model are
supplied. PowerContext does not install or build the native extension; provide a compatible library for the target
operating system and architecture:

```python
from pathlib import Path

config = BuiltinConfig(
    database=SQLiteConfig(
        url="sqlite+aiosqlite:///powercontext.db",
        vec1_extension=Path("/opt/sqlite-extensions/vec1"),
    )
)
async with open_builtin_runtime(
    config,
    embedding_model=embedding_model,
) as runtime:
    ...
```

The SQLite profile composes FTS5 and Vec1 strategies. It reports `fts`, `vector`, and `hybrid` through Memory
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
OceanBase FULLTEXT therefore serve the same Runtime and Server search calls; Vec1 and HNSW do the same for vector
search.

## Operational checks

Before serving requests, verify:

- the selected profile opens and initializes successfully;
- each tenant or project maps to the intended scope ID;
- scheduled extraction has a candidate pipeline;
- Vec1 configuration includes a matching embedding model;
- OceanBase vector search has a matching embedding model;
- capability responses match the indexes actually initialized;
- database and scheduler resources close with the process lifecycle.
