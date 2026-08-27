# OpenDAL Connector worker

`powercontext-connector-opendal` is an independently deployed Connector worker. It owns the executable text-file
Source Definition and uses OpenDAL through `opendalfs` to acquire files. PowerContext Server only receives the
Definition manifest, projected Source observations, and opaque checkpoint comparisons.

Install from a checkout:

```bash
uv tool install ./integrations/opendal
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
after every accepted Source observation has a durable Server receipt and the scan completes without rejected or
failed items. Set `POWERCONTEXT_TOKEN` when the Server requires bearer authentication.
