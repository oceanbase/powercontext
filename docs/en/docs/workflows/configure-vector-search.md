---
title: Configure vector search
description: Enable embedding-backed vector and hybrid Memory search, then verify the Runtime capability.
---

# Configure vector search

Vector search needs an embedding model, a stable profile ID, and the model's output dimension.

## 1. Set one embedding profile

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=embedding-model-v1
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1024
```

Replace the example values with one supported provider model, a profile ID you keep stable for that model, and its
documented output dimension. `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION` defaults to `unit`.

The output dimension is always required. It fixes the local vector index width and validates returned vectors. By
default that value is also sent as the `dimensions` request field. Some OpenAI-compatible services reject the field,
including SiliconFlow `BAAI/bge-m3`. Keep the documented output dimension (1024 for bge-m3) and disable sending:

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_SEND_DIMENSIONS=false
```

## 2. Start the Server

```bash
powercontext server run
```

With SQLite, PowerContext loads the bundled sqlite-vec extension when it opens the database. Startup fails if the
installed extension is incompatible with the platform or SQLite build.

## 3. Verify the capability

```bash
powercontext capabilities
```

The result reports the enabled search modes. Without an embedding profile, SQLite full-text search remains available.

The capability flag reports a loaded channel; inspect Source extraction, projection writes, and actual search hits
separately. See [Configure models](../get-started/configure-models.md). Search identities are in
`hits[].memory.artifact`, and vector hits include `vector` in `matched_by`. Explicit vector/hybrid modes fail when
vectors are unavailable rather than silently falling back.

When enabling or changing an embedding profile, stop normal service and migrate every derived projection that shares it.
Use the [Atomic Memory migration guide](../operate/atomic-memory-migration.md) for an Atomic-only rebuild. For a managed
Topic Memory profile change that also requires Atomic rebuilding, use the coordinated option below instead of an
independent Atomic-first migration. Rebuilding preserves Artifact identities, content revisions, and state versions. Do
not change a profile ID or dimension just to bypass a mismatch.

## 4. Reindex Topic Memory

Topic Memory can move to the configured target profile without deleting its Artifacts. The command re-embeds every
active Topic title/summary and active chunk, then activates the new frozen retrieval shape. Authoritative Artifact
content, Topic and chunk identities, revisions, and text remain unchanged.

Stop every API, host, and Worker and pause all Topic writes before applying the migration. Inspect the existing database
and target profile first:

```bash
powercontext server topic-memory-reindex --action dry-run
```

Dry-run is the default. It does not initialize the embedding provider, call the model, create staging tables, or modify
the database. Apply the same configured target after the inspection is ready:

```bash
powercontext server topic-memory-reindex --action apply --maintenance-confirmed --batch-size 25
```

Each staging batch commits independently. Committed Topic and chunk counts are written as JSON progress to stderr, and
the final JSON report is written to stdout; use `--no-progress` to suppress progress lines. If the command is
interrupted or inference fails, keep the old service profile and rerun the same apply command with the same target to
resume complete staged revisions. Apply initializes the provider only when pending Topic vectors or a requested Atomic
rebuild need inference; activating complete staging or finishing recovery does not require provider credentials. Do not
change the target profile midway through a run or manually delete its `pc_topic_memory_reindex*` tables.

A failure before activation preserves the old shape and active vectors. OceanBase and SeekDB retain the previous derived
vector tables until the activated vectors and shared storage declaration validate. If post-activation validation or
cleanup fails, rerun the same apply command in maintenance mode so it can validate and finish recovery before discarding
the previous generation.

For managed Server storage, Atomic Memory and Topic Memory share the declared embedding profile. By default, this
command does not re-embed Atomic Memory: incompatible Atomic projections or an incompatible OceanBase Atomic vector
column block activation. If the managed migration must rebuild Atomic Memory too, explicitly add
`--rebuild-atomic-memory` to both dry-run and every apply or recovery invocation:

```bash
powercontext server topic-memory-reindex --action apply --maintenance-confirmed \
  --rebuild-atomic-memory --batch-size 25
```

The option rebuilds the current Atomic projection from retained authoritative history with the same target provider; it
does not change Atomic Artifact identities, revisions, or state. Topic staging commits first, the Atomic rebuild commits
separately, and both retained-evidence and current Atomic projections are verified before Topic activation or recovery
cleanup. If later Topic activation fails,
authoritative data and the old Topic generation remain, but Atomic may already use the target profile while the shared
declaration and active Topic shape still use the old one. Keep service stopped and resume the same apply command with
the same option; do not restart the old service profile or switch to an Atomic-only migration.

Atomic Memory currently uses exact L2 distance for ordinary vector search and extraction threshold enumeration.
Search limits returned results; extraction returns every eligible threshold match. Work grows with eligible vector
count and dimension, independently of the response limit.

For timeouts, batch size, storage settings, and exact defaults, see [Configuration](../operate/configuration.md).
