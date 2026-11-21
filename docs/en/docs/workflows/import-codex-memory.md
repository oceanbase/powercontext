---
title: Import Existing Codex Memory
description: Preview and collect selected derived files under the Codex memories directory as Source snapshots.
---

# Import Existing Codex Memory

The Codex Memory Source plugin reads already-derived text under `CODEX_HOME/memories` and stores each file version as an
immutable `codex-memory-snapshot` Source. It does not create reviewed Memory or execute file contents. Sources remain
untrusted evidence until an Agent explicitly uses them.

## Preview the selected files

From a PowerContext checkout, run:

```sh
uv run python -m integrations.codex.memory_source_plugin preview \
  --codex-home "$HOME/.codex" --path .
```

`--path` is relative to `memories/`; use `MEMORY.md` for one file or a subdirectory for a subset. The preview reports relative
paths, prospective Source IDs, skipped empty files, and bounded failure reasons. It does not contact the Server.

## Add the plugin and collect

Copy `integrations/codex/memory_source_plugin/` into the Server plugin directory as `codex-memory-history/`. The Server
process or device runner that will read the files must have `CODEX_HOME` set to the Codex home on that machine. The plugin is
loaded and targets are managed through the current [Source plugin workflow](source-plugins.md).

Register a target in the destination Scope with `pc_source_add`:

```javascript
pc_source_add({
  "scope_id": "SCOPE_ID",
  "runner": "server",
  "capability": "codex-memory-history",
  "parameters": {"path": "."}
})
```

Use a device runner instead of `server` when the files are on another machine. The runner must have the same plugin installed.
Adding the target starts collection; the plugin snapshots changed files and records progress in the target checkpoint. To do a
one-time import, wait for the target's status to report success and disable it:

```javascript
pc_source_target_set_enabled({"scope_id":"SCOPE_ID", "target_id":"TARGET_ID", "enabled":false})
```

Inspect imported Sources through the Scope's Source APIs. Re-enabling the target resumes tracking; deleting it removes its
checkpoint but keeps already collected Sources.
