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

When enabling or changing an embedding profile, stop normal service and rebuild the current projection using the
[Atomic Memory migration guide](../operate/atomic-memory-migration.md). Rebuilding preserves Artifact identities,
content revisions, and state versions. Do not change a profile ID or dimension just to bypass a mismatch.

Atomic Memory currently uses exact L2 distance for ordinary vector search and extraction threshold enumeration.
Search limits returned results; extraction returns every eligible threshold match. Work grows with eligible vector
count and dimension, independently of the response limit.

For timeouts, batch size, storage settings, and exact defaults, see [Configuration](../operate/configuration.md).
