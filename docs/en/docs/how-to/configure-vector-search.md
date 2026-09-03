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

For timeouts, batch size, storage settings, and exact defaults, see [Configuration](../reference/configuration.md).
