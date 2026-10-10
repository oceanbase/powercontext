# Using the Builtin Memory layer

Builtin Memory stores each durable fact, preference, or decision as an independent `atomic-memory` Artifact. Each
memory has its own identity, revisions, state, tags, and access relationships. The `builtin` extra includes the
complete runtime and both supported database integrations. Remote applications should use the Server API described in
the [remote access guide](remote-access-implementation.md).

## Select a database

Install the built-in implementation:

```bash
uv add "powercontext[builtin]"
```

SQLite is the default. `open_builtin_runtime()` owns the selected database profile and returns the same
`BuiltinRuntime` interface for either database:

```python
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime


async def save_note() -> None:
    config = BuiltinConfig(
        database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db")
    )
    async with open_builtin_runtime(config) as runtime:
        assert runtime.atomic_memory is not None
        result = await runtime.atomic_memory.for_scope("project-alpha").create(
            (AtomicMemoryContent(kind="decision", text="Use one composition root for the process."),)
        )
        assert result.primary.ref.revision == 1
```

The scope ID selects an isolated Source journal, Memory set, and Trigger cursor within the database.

## Write, extract, and change memories

`ScopedAtomicMemoryApplication.create()` writes explicit memories in one all-or-nothing batch and returns one record per
input. Source-based extraction follows a separate path: capture Sources, then call `flush()` to process the pending
Source window with the configured extraction pipeline. `cursor()` reports the processed Source position.

Every memory changes independently. `forget()` deactivates one memory and `merge()` replaces several memories with a
new one. `forget()` takes the expected content revision and state version, and `merge()` takes the exact records read
by the caller, so a stale caller fails instead of overwriting a concurrent change. `restore()` applies the token
returned by `preview_restoration()`:

```python
memory = runtime.atomic_memory.for_scope("project-alpha")
current = (await memory.list()).items[0]
await memory.forget(
    current.ref.artifact_id,
    expected_revision=current.ref.revision,
    expected_state_version=current.state.state_version,
)
```

`list()` pages memories by state, and `get()` reads one memory, optionally at an exact revision.

## Search

SQLite and OceanBase both initialize a full-text index, so either database can search without an embedding model:

```python
result = await runtime.atomic_memory.for_scope("project-alpha").search("composition root", mode="text")
```

Each hit identifies the exact memory revision used for ranking. `mode="auto"` chooses the strongest available mode
and can fall back to text search if query embedding is temporarily unavailable. Explicit `vector` and `hybrid`
requests fail when the configured profile does not provide that capability.

## Legacy Memory HTTP operations

The Server keeps five legacy entry points, each translated onto Atomic Memory:

| Operation | Behavior |
| --- | --- |
| `POST /v1/memory/remember` | Creates one memory. `expected_revision` is rejected. |
| `POST /v1/memory/search` | Searches active memories; `fts` maps to text search. |
| `POST /v1/memory/entries/list` | Lists memories; `include_inactive` adds forgotten, merged, and retired ones. |
| `POST /v1/memory/entries/get` | Reads the memory migrated from a legacy `target` collection and entry ID. |
| `POST /v1/memory/flush` | Processes the next Source window. |

Collection revisions, citations, capacity, changes, revise, retire, entry tags, and `memory` access targets have no
Atomic equivalent and return `422 legacy_memory_operation_unsupported` before any work begins.

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

The SQLite profile composes FTS5 and sqlite-vec strategies and reports `text`, `vector`, and `hybrid` search modes.
Stored projections and query vectors must use the same `EmbeddingProfile`, including model name, dimension, distance,
and normalization. Changing that profile requires rebuilding the derived search index before vector search resumes.
Artifact revisions and states remain the source of truth.

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
    memory = runtime.atomic_memory.for_scope("project-alpha")
```

The OceanBase profile uses the same index composition as SQLite. Its full-text strategy is always available. Supplying
an embedding model adds a `VECTOR` projection and HNSW strategy, enabling `vector` and `hybrid` modes. SQLite FTS5 and
OceanBase FULLTEXT therefore serve the same Runtime and Server search calls; sqlite-vec and HNSW do the same for vector
search.

## Operational checks

Before serving requests, verify:

- the selected profile opens and initializes successfully;
- each tenant or project maps to the intended scope ID;
- scheduled extraction has an extraction pipeline;
- SQLite vector search has a matching embedding model;
- OceanBase vector search has a matching embedding model;
- capability responses match the indexes actually initialized;
- database and scheduler resources close with the process lifecycle.
