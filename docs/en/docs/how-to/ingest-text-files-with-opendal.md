---
title: Ingest text files with OpenDAL
description: Capture UTF-8 files as typed Sources through an OpenDAL storage backend.
---

# Ingest text files with OpenDAL

Use `OpenDALTextFileConnector` to capture bounded UTF-8 files from a storage backend supported by OpenDAL. Each accepted
file becomes an immutable `text-file-snapshot` Source with its path, namespace, content digest, and available provider
annotations.

## Before you begin

The OpenDAL integration requires Python 3.12 or later. Install the optional dependency:

```bash
uv add "powercontext[opendal]"
```

Choose a stable `source_namespace` for the storage location. It distinguishes identical paths and bytes captured from
different authorities. Do not put credentials in the namespace.

## Run a local filesystem binding

The following binding scans the `docs` directory below `/absolute/path/to/project` and persists its checkpoint in the
same PowerContext database as the captured Sources:

```python
import asyncio

from powercontext.builtin.connectors import OpenDALTextFileConnector
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.sources import ConnectorBinding


async def main() -> None:
    connector = OpenDALTextFileConnector.from_service(
        "fs",
        source_namespace="project-docs",
        root="docs",
        storage_options={"root": "/absolute/path/to/project"},
    )
    binding = ConnectorBinding(
        scope_id="project:example",
        binding_id="project-docs",
        connector_name=connector.name,
        connector_version=connector.version,
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db"),
    )

    async with open_builtin_contexts(config) as contexts:
        result = await contexts.run_connector(connector, binding)
        print(result.model_dump_json(indent=2))


asyncio.run(main())
```

Use a different OpenDAL service and its backend options for remote storage. `storage_options` are runtime-only and are
not copied into Source payloads or checkpoints.

## Interpret the result

An item outcome reports one of four states:

- `accepted`: the Source was durably stored;
- `replayed`: the sink recognized an already accepted Source;
- `rejected`: the provider item could not satisfy the Source Definition, such as invalid UTF-8;
- `failed`: the item could not be read or stored safely.

The checkpoint advances only after every selected item is accepted and the run completes. A rejected or failed item
leaves the previous checkpoint in place, so the next run safely retries the scan. Files whose digest matches the
committed checkpoint are skipped.

Accepted Sources enter the same scoped Source journal used by Memory extraction. When the runtime has a Memory
candidate pipeline, its normal source-window flush or schedule can consume these Sources through the shared
`powercontext.builtin.text-evidence` projection. Connector completion does not itself create Memory.

## Current limits

- The default patterns select Markdown, text, reStructuredText, and AsciiDoc files.
- A run selects at most 10,000 files and reads at most 2 MiB per file unless configured otherwise.
- Only UTF-8 content is accepted.
- Changed bytes produce a new exact snapshot Source; earlier snapshots remain readable for lineage.
- A full scan removes missing paths from the next checkpoint but does not delete Sources or claim authoritative
  deletion.
- The Connector does not provide a change feed. Schedule repeated runs to observe later changes.
