---
title: Migrate the Memory query index
---

The bounded Memory-entry query uses a rebuildable directory projection. A new
database is marked ready automatically. After upgrading a database that already
contains Memory revisions, the Server keeps legacy list, exact-detail, search,
and write operations available, but the new query returns
`memory_query_index_unavailable` until this migration is verified.

## Plan and apply

Use the same deployment environment file as the Server:

```shell
powercontext server memory-query-migrate --action plan --env-file .env
```

The plan is read-only and reports missing projection tables, authoritative
Memory revision count, existing directory rows, and the durable phase.

Back up the database. Stop every old Server and Memory writer, disable their
automatic restart, and keep them stopped for the maintenance window. The
command cannot establish those external conditions. Then run:

```shell
powercontext server memory-query-migrate --action apply --env-file .env \
  --migration-id rfc1656 --batch-size 100 --maintenance-confirmed
powercontext server memory-query-migrate --action verify --env-file .env
```

`apply` creates any missing feature tables one at a time and commits at most the
selected number of Memory revisions per backfill transaction. It verifies the
result before reporting success. If it stops, repeat it with the same migration
ID. Do not clear the marker or directory rows; a different ID is rejected while
an earlier migration is incomplete.

The batch size counts revisions, not entries inside a revision. Rebuilding one
large initial manifest therefore writes one derived directory row per entry in
that transaction. Those rows use set reads and executemany writes in chunks of
500; choose the revision batch size and maintenance window with the largest
stored manifest in mind.

Only restart writers and Server replicas after the command reports
`ready: true`. The separately runnable `verify` action rechecks every
authoritative revision and tag-generation row and is safe to repeat.

## What the migration changes

The migration reads immutable Memory manifests and rebuilds revision-valid
compact directory rows plus one tag generation per Memory Artifact. It never
rewrites Memory Artifacts, entry versions, tags, or entry bodies. The directory
remains derived state and is not used as an authority until full verification
succeeds.

Memory writes from the new version maintain their own directory deltas, but a
rolling deployment with old and new writers during this offline migration is
unsupported. SQLite migration requires a persistent database; in-memory SQLite
has no upgrade state to preserve.
