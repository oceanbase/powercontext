---
title: Integration catalog
description: Inspect the profiles used to build native integrations.
---

# Integration catalog

The distribution profiles are the integration catalog. Inspect language, native events, operations, effects, and
failure policy from the same inputs used by the builder:

```bash
uv run python scripts/build_agent_distributions.py --list
powercontext doctor integrations --json
```

The first command reports declared bindings; the second reports installed host status. For current tools and permissions,
inspect the native host catalog. See [plugin architecture and distribution](../../development/plugin-distribution.md)
for templates, setup, and adding a host.
