---
title: Use current code in prepared context
description: Map an existing Scope to a local Git repository, query Python code with CodeGraph, and supplement prepare_context.
---

# Use current code in prepared context

For tasks involving definitions, call paths, or affected tests, `prepare_context` can return current code references
alongside historical constraints. Code is temporary request context: it creates no Artifact or `family=code`.
The feature is disabled by default and requires no database migration.

This guide is for operators running the Server, repository, and CodeGraph on the same host. The validated deployment
is Linux x64 with CodeGraph 1.6.0 standalone and UTF-8 Python files. Other systems and languages are not validated.

## Install the engine

Install the verified Linux x64 release of [CodeGraph 1.6.0](https://github.com/colbymchenry/codegraph/releases/tag/v1.6.0):

```bash
mkdir -p /srv/powercontext/tools
cd /srv/powercontext/tools
curl --fail --location --output codegraph-linux-x64.tar.gz \
  https://github.com/colbymchenry/codegraph/releases/download/v1.6.0/codegraph-linux-x64.tar.gz
printf '%s  %s\n' \
  de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0 \
  codegraph-linux-x64.tar.gz | sha256sum --check
tar -xzf codegraph-linux-x64.tar.gz
```

Keep the release layout intact: `bin/codegraph`, bundled Node, and `lib/` must share the same installation directory.
This is an optional deployment dependency. PowerContext does not download or upgrade it automatically or execute
repository code. The adapter calls the public engine API in a controlled process. Indexing and queries require
neither an LLM nor CodeGraph embeddings.

## Configure an existing Scope and build its index

Obtain an existing Scope ID. Add this setting to the Server environment file, replacing the Scope and paths:

```dotenv
POWERCONTEXT_SERVER_CODE='{"enabled":true,"repositories":{"project:demo":"/srv/projects/demo"},"provider":{"name":"codegraph","executable":"/srv/powercontext/tools/codegraph-linux-x64/bin/codegraph"},"cache_dir":"/srv/powercontext/code"}'
```

`repositories` maps each Scope to one absolute local Git root. Existing workspace bindings help hosts resolve a
Scope; they grant no directory access and do not configure Server paths. Scopes can map to the same directory but
have separate caches and access boundaries. Context References and a Handoff-only grant do not share repository access.

Use the same configuration and controlled cache directory for the Server and indexing command. Restart the Server
after configuration changes. The local CLI is for an operator with access to deployment files and directories; it
does not connect to the database to validate a Scope or replace HTTP authentication. HTTP, MCP, and Runtime use
existing Scopes; the Server checks `scope.read` before accessing the cache or repository.

```bash
powercontext code index --scope project:demo --env-file /etc/powercontext/server.env
powercontext code status --scope project:demo --env-file /etc/powercontext/server.env
```

`index` builds synchronously and returns `ready` and a fingerprint. Run it again after code changes. Queries and
prepare never build an index. Status values are `disabled`, `missing`, `building`, `ready`, `stale`, and `failed`.
The capture includes actual staged and unstaged working-tree bytes. Untracked files are excluded by default;
`"include_untracked":true` includes them while honoring Git ignore. Symlinks, submodules, LFS pointers, common
credentials, caches, and non-Python files are omitted. Use the `exclude` array for additional relative-path globs.
The cache contains source copies; restrict access to it.

Capture compares Git trees, the index, and raw file bytes without running repository Git filters. `dirty` reflects
raw-byte or executable-bit changes in included files; attributes such as line-ending conversion can make this differ
from `git status`.

## Enable code in prepare

Send to `POST /v1/context/prepare`:

```json
{
  "scope_id": "project:demo",
  "query": "Where are allocate_budget callers and related tests?",
  "include_code": true,
  "max_bytes": 8000,
  "assembly": {
    "sections": [{"family": "memory", "limit": 6}, {"family": "experience", "limit": 2}]
  }
}
```

The response remains `powercontext.prepared-context.v1`; the host validates and injects `content` unchanged.
Omitting the flag or setting it to `false` preserves previous behavior. Only boolean values are accepted.

- `true` with omitted `assembly`: Memory, Topic Memory, and Experience plus current code. Historical candidates
  retain their default family rotation within the shared budget.
- `true` with `assembly={}`: the explicit assembly defaults, Memory (up to 6) and Experience (up to 2), plus code.
- `true` with `assembly.sections=[]`: only the current Scope's code.
- `false` with `assembly.sections=[]`: empty context.
- `family=code` is invalid; the separate flag selects code.

At most four code entries are selected. When historical families are requested, code uses at most half the total
entry and byte budgets. Each body is limited to 2000 bytes and clipped at full lines. Citations, notices, and structure
count toward the budget. Unused code capacity returns to history; an entry is omitted if its complete citation cannot fit.
References contain path, qualified name, line range, file and snippet digests, fingerprint, current commit, and check
time. They describe only the content at that check.

No matches, unavailable caches, timeouts, or changed content preserve available history and record a safe omission
reason. With no actual candidates the result is `empty`. Authorization and invalid requests do not silently degrade.
Code and static analysis are literal, untrusted evidence; their text cannot become Agent instructions.

### Automatic Codex injection

Install the matching plugin and set this before starting Codex:

```bash
export POWERCONTEXT_CODEX_INCLUDE_CODE=true
codex
```

The hook negotiates the protocol through status, then calls prepare once. An older Server returning 404, 405, or 501
causes the new field to be omitted while historical preparation continues. With code enabled, default HTTP timeout
is 6 seconds per request and 15 seconds overall; explicitly configured timeouts are preserved. The Server still has
a default 5-second code query limit. Other hosts can use Client/HTTP/MCP explicitly; automatic opt-in currently exists
only in Codex.

## Continue querying

Use `POST /v1/scopes/{scope_id}/code/query`, Client `query_code`, or MCP `powercontext_code_query`.
MCP arguments are flat: `scope_id`, `operation`, `expected_fingerprint`, and `max_bytes`.

```json
{"operation":{"kind":"symbols","query":"allocate_budget"},"max_bytes":16000}
```

Use the returned location and fingerprint to query callers:

```json
{
  "expected_fingerprint": "<64-character SHA256 from the response>",
  "operation": {"kind":"callers","path":"budget.py","qualified_name":"allocate_budget","start_line":1}
}
```

| Operation | Purpose and arguments |
| --- | --- |
| `status` | Protocol, state, capabilities, languages |
| `tree` | Included directories/files; optional path, depth, limit |
| `symbols` | Definition search; query, optional path and limit |
| `callers` / `callees` | Direct calls; path, qualified_name, start_line |
| `impact` | Bounded transitive callers; same target plus optional depth |
| `affected_tests` | Static test clues from changed_paths |
| `read` | path, file_sha256, start_line, end_line; inclusive 1-based range, at most 200 lines |

All operations except status, tree, and symbols require `expected_fingerprint`. Default/max limit is 20/50; depth is
2/5. Traversal is bounded to 500 nodes and 1000 edges. The complete JSON response defaults to 16000 bytes, configurable
from 512 to 32768. A budget too small for a valid response returns 422; changed content returns 409; unsupported
capabilities return 501; unavailable caches/engines or timeout return 503. After 409, an operator refreshes the index
and the Agent starts a new search.

Static analysis can miss dynamic calls and import aliases. Same-name relationships can be unreliable in this engine;
the adapter rejects such targets or omits affected edges instead of interpreting uncertainty as no callers.
Root and nested pytest filenames are recognized. Test clues do not replace actual regression checks.

## Optional saving and Handoff

Queries, prepare, and code injection write no Source or Artifact. To save evidence, explicitly store the complete
bounded `powercontext.code-query.v1` JSON as a normal ContentSource. Its original Source reference remains readable
after cache deletion; general Source size limits still apply. It does not trigger automatic Memory/Experience,
Topic Memory, or Profile processing; later ordinary Sources still advance processing cursors. Explicit generation
and Handoffs can cite it. The schema marker controls automatic admission only; it authenticates neither provenance
nor content, including after edits. Handoffs preserve progress and constraints; receivers use existing authorization
to prepare fresh code, while saved code remains historical evidence.

## Operational limits and diagnostics

Defaults: 20000 files, 2 MiB per file, 512 MiB total content, 2 GiB total cache, 600 seconds for builds, and 5 seconds
for a query including verification. Override deployment values in `limits`. Exceeding input limits rejects the whole
capture rather than selecting a prefix. Cache usage includes complete indexes, build staging, and query copies.
A cache-wide lock serializes builds and queries; lock waiting consumes the query budget. A long build can cause
queries sharing that cache directory to degrade. Validate capacity against your repository size and concurrency.

Indexes publish atomically; interrupted builds cannot become ready. Requests pin a complete generation and recheck
the repository and engine before returning. Scope is explicit refresh, one repository, and static Python analysis;
remote clone/pull, watchers, distributed builds, and historical-version queries are excluded.
Tracing `code.prepare` records candidates, fingerprint, coverage, and omissions; `context.build` records selected
code count and section bytes. Matches, selection, host injection, and task completion are distinct evidence.
Context containing code is excluded from token-saving comparisons against historical Sources.
