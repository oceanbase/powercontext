---
title: Ingest text files with OpenDAL
description: Capture UTF-8 files as typed Sources with an independent OpenDAL Connector worker.
---

# Ingest text files with OpenDAL

`powercontext-connector-opendal` is deployed independently from PowerContext Server. It owns OpenDAL credentials,
provider configuration, the executable Source Definition, and file reads. The Server stores only a declarative
Definition manifest, materialized Source observations, named projections, and opaque checkpoints.

This integration is an evaluation Connector. It validates captured immutable snapshot ingestion, Definition
manifest registration, one named projection, durable acceptance receipts, and checkpoint compare-and-swap. It does
not implement the full logical Source observation model described by the Source Definition RFC.

## Before you begin

The integration requires Python 3.12 or later. Start PowerContext Server, then install the worker from a checkout:

```bash
uv tool install --python 3.12 --with-editable ".[client]" ./integrations/opendal
```

Choose a stable `source_namespace` that distinguishes storage authorities. Do not put credentials in the namespace,
Source payload, or checkpoint. If Server authentication is enabled, provide its bearer token through the
`POWERCONTEXT_TOKEN` environment variable. Set `POWERCONTEXT_SCOPE_ID` to an existing ID returned by `create_scope`.

## Run a binding

This independent process scans `/absolute/path/to/project/docs`. The `binding_id` identifies checkpoint continuity;
the `scope_id` determines which Scope owns accepted Sources:

```bash
powercontext-connector-opendal \
  --base-url http://127.0.0.1:8765 \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --binding-id project-docs \
  --service fs \
  --storage-option root=/absolute/path/to/project \
  --root docs \
  --source-namespace project-docs
```

For remote storage, replace the OpenDAL service and pass its `--storage-option KEY=VALUE` arguments. These options
remain inside the worker process and are never sent through the ingestion API.

On every run, the worker idempotently registers the `text-file-snapshot` Definition manifest, reads the binding
checkpoint, submits changed snapshot Sources, and compare-and-swaps the checkpoint after all accepted submissions
have durable receipts. Use cron, a Kubernetes Job, or another external scheduler to run the command periodically.

## Embed the lifecycle in a worker

Use the generic remote lifecycle when a deployment needs custom supervision or schedules multiple bindings:

```python
import os

from powercontext.client import PowerContextClient, RemoteConnectorWorker
from powercontext.sources import ConnectorBinding, SourceDefinitionRegistry
from powercontext_connector_opendal import (
    TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,
    OpenDALTextFileConnector,
)

connector = OpenDALTextFileConnector.from_service(
    "fs",
    source_namespace="project-docs",
    root="docs",
    storage_options={"root": "/absolute/path/to/project"},
)
binding = ConnectorBinding(
    scope_id=os.environ["POWERCONTEXT_SCOPE_ID"],
    binding_id="project-docs",
    connector_name=connector.name,
    connector_version=connector.version,
)
registry = SourceDefinitionRegistry((TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,))

async with PowerContextClient("http://127.0.0.1:8765") as client:
    result = await RemoteConnectorWorker(client=client, registry=registry).run(connector, binding)
```

## Runtime semantics

Each processed item is `accepted`, `rejected`, or `failed`. Submitting the same snapshot identity and payload again
has the same accepted result. The checkpoint advances only when the run completes without rejected or failed items.
Otherwise the prior checkpoint remains, and the next run safely retries from it. Files whose digest matches the
committed checkpoint are skipped.

Accepted snapshot Sources enter the target Scope's Source journal. The worker computes the declared
`powercontext.text-evidence` named projection, and the Server validates its schema before acceptance. This evaluation
does not establish a general Memory-consumption contract. A Connector run does not create Memory directly.

## Limits

- The default patterns select Markdown, text, reStructuredText, and AsciiDoc files.
- A run selects at most 10,000 files and reads at most 2 MiB per file by default.
- Only UTF-8 content is accepted.
- Snapshot identity is derived from `source_namespace`, path, and content digest. Changed content therefore creates a
  different immutable snapshot identity. This evaluation identity is not the standard logical Source identity with
  multiple observation IDs.
- A full scan removes missing paths from the next checkpoint but does not delete Sources or claim authoritative
  deletion.
- The Connector does not provide a change feed; an external scheduler must run the worker again to observe changes.
- The Connector does not implement current heads, logical multi-observation history, deletion semantics, referenced
  materialization, or exact reads from immutable external revisions.
