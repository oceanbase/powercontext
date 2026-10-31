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

This extra supports in-process Runtime initialization, writes, and reads without the `server` extra.
Install the `server` extra to use the Server's optional Casbin authorization provider.

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
With the built-in extraction and reconciliation Prompts, a candidate that has no related memory or earlier candidate in
its window is kept as extracted without a reconciliation request; a custom Prompt or an injected pipeline reconciles
every candidate. Extraction owns the decision to keep a fact. The built-in reconciler's `noop` without a target drops a
candidate only while comparing it with related items, so such a candidate never reaches that second check.

On SQLite, seekdb, and OceanBase, `flush()` compares each new candidate with the relevant memories under the same
retrieval and comparison rules. Sources and Prompts are authorized as the Source window enters processing; recalled
memories are authorized as they enter the comparison set. A permission change during the window prevents publication,
but inputs already admitted to that window can finish its comparisons. A retry must obtain fresh authorization.
Changed Source or Artifact dependencies are rechecked before the cursor advances.

Successful publication makes the new memories and their direct Source lineage readable together. If an Artifact ID
collides or a dependency changes, the window does not publish partial results or advance its cursor. A multi-create
call returns the created memories with their current content, state, and lineage; each returned memory remains subject
to its own read authorization.

Custom Atomic and Topic Memory indexes must support publication of a complete set of prepared projections. An index
that lacks this capability is rejected before publication writes.

The built-in extraction and reconciliation instructions preserve each fact's supporting Source language. Chinese
facts stay in Chinese and English facts stay in English, including when they share a processing window. Mixed-language
facts retain their natural-language framing and exact identifiers, names and values. An incoming translation alone
does not revise an unchanged published fact. Automatic reconciliation rejects a `revise` whose final kind and text
exactly match its target when `noop` can retain all published identities; a new description or additional support alone
does not justify a revision. The model can correct the decision within its request budget. An uncorrected invalid
decision prevents publication and cursor advancement. Different wording still requires the model to judge meaning;
this check does not provide cross-language semantic search or restrict explicit record edits. Source content remains
untrusted evidence, not output-policy configuration.

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
