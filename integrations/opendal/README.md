# OpenDAL Connector worker

`powercontext-connector-opendal` is an independently deployed evaluation Connector worker. It owns the executable
text-file Source Definition and uses OpenDAL through `opendalfs` to acquire files. PowerContext Server only receives
the Definition manifest, projected snapshot Sources, and opaque checkpoint comparisons.

The integration validates captured immutable snapshots, Definition manifest registration, one named projection,
durable acceptance receipts, idempotent identical submission, and checkpoint compare-and-swap. It is not a complete
implementation of the standard logical Source observation model.

Install from a checkout:

```bash
uv tool install --python 3.12 --with-editable ".[client]" ./integrations/opendal
```

Run one bounded scan against a filesystem backend:

```bash
powercontext-connector-opendal \
  --base-url http://127.0.0.1:8765 \
  --scope-id project-a \
  --binding-id workspace-documents \
  --service fs \
  --storage-option root=/path/to/workspace \
  --source-namespace workspace-a
```

The process registers its immutable Definition manifest before each run. It advances the binding checkpoint only
after every accepted snapshot has a durable Server receipt and the scan completes without rejected or failed items.
Set `POWERCONTEXT_TOKEN` when the Server requires bearer authentication.

Snapshot identity is derived from the configured namespace, path, and content digest. Identical content is accepted
idempotently; changed content creates a different immutable snapshot identity. This evaluation representation does
not provide one logical Source with multiple observation IDs, current heads, deletion semantics, referenced
materialization, or exact reads from immutable external revisions.
