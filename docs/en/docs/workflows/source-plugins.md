---
title: Source plugins
description: Provide your own Source types with Source plugins, and collect continuously through the Server's runner or runners on devices, by assigned targets.
---

# Source plugins

Source plugins live in the same plugin directory as Artifact plugins (`POWERCONTEXT_SERVER_PLUGINS_DIR`): `plugin.py`
assigns a `SourcePlugin` to `plugin`, and the directory name is the plugin name. A Source plugin can:

- **Only provide Source types**: it declares `definitions`; callers submit content through the existing Source write API,
  and the Server runs `resolve` and the Projections before storing it.
- **Collect**: it also declares `collect`, `target`, and `setup`. Runners collect: the Server has its own runner named
  `server`, and devices run `powercontext source-agent`. Users assign collection targets to a runner, and the runner
  fetches its targets on a schedule and submits the results.

Source plugins load, update, and unload like Artifact plugins; see [Write a plugin](../develop/plugins.md). Runnable
examples are in `examples/source_plugins/` in the repository.

## Permissions

| Operation | Required permission |
| --- | --- |
| List runners and their capabilities (`pc_source_runners`) | Authenticated |
| Read a Scope's targets and status (`pc_source_status`) | `scope.read` on the Scope |
| Add, change, disable, or delete targets of a device runner | `scope.contribute` on the Scope |
| Add, change, disable, or delete targets of the `server` runner | `server.admin` |
| Sync a device runner and submit Sources and checkpoints | The runner itself, and `scope.contribute` on the target Scope |
| Delete a runner record | `server.admin` |

The `server` runner reaches external systems with credentials from the Server environment, so deciding what it collects
is an operator decision and requires `server.admin`. Plugins read their own credentials for external systems (for
example from environment variables); those do not replace PowerContext permissions.

## Only provide Source types

`examples/source_plugins/images` declares one Source type and nothing else:

```python
plugin = SourcePlugin(version="1.0.0", definitions=(ImageDefinition(),))
```

Once the directory is in the plugin directory, callers submit through the generic Source API:

```bash
curl -X POST "$SERVER/v1/scopes/$SCOPE/sources" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type": "example-image", "content": {"media_type": "image/png", "data_base64": "..."}}'
```

The Server validates the input, runs `resolve` and the Projections in the plugin's host process, and deduplicates by
Source identity.

## Collect on the Server

`examples/source_plugins/tickets` collects tickets from a JSON file on the runner's machine:

```python
plugin = SourcePlugin(
    version="1.0.0",
    definitions=(TicketDefinition(),),
    collect=Collect(schedule=Interval(seconds=10)),
    target=TargetSpec(
        description="Collect tickets from a JSON file on the runner",
        parameters=TicketFile,
        identity={"file": ("strip",)},
    ),
    setup=setup,
)
```

An administrator assigns a target to the `server` runner (MCP examples use the `tool(arguments)` shorthand):

```javascript
pc_source_runners()
pc_source_add({"scope_id": "SCOPE_ID", "runner": "server", "capability": "tickets",
               "parameters": {"file": "/srv/data/tickets.json"}})
```

A new target is collected right away, and then on the plugin's `schedule`. Each collection runs in a Supervisor Worker
for the enabled `server` targets of one Scope; the returned Sources, each target's new checkpoint, and its status are
stored in one transaction.

## Collect on a device

Put a copy of the same plugin on the device and run the runner with a credential that has `scope.contribute` on the
target Scopes:

```bash
export POWERCONTEXT_SOURCE_AGENT_TOKEN=...      # the runner's credential
powercontext source-agent --server https://memory.example.com --plugins /opt/robot/plugins
```

- The runner is named after its credential's subject unless `--name` gives a name; devices sharing a credential must
  each set one. The name `server` is reserved for the Server's own runner. A name is bound to the credential subject of
  its first sync, and other subjects cannot sync with it.
- The runner syncs every 30 seconds (`--interval`): it reports the plugins and versions it loaded and fetches every
  target assigned to it across Scopes. New targets are collected right away; the others follow the plugin's `schedule`.
- For each target the runner submits the Sources first and then the new checkpoint and status, with the old checkpoint
  as the expected value. If anything fails in between, the checkpoint does not advance; the next collection repeats the
  same content, and the Server deduplicates it by Source identity.
- Every Source plugin used on devices must also be in the Server's plugin directory: capability descriptions, parameter
  schemas, and Projections follow the version the Server loaded.

Assign a target to a device:

```javascript
pc_source_add({"scope_id": "SCOPE_ID", "runner": "robot-7", "capability": "ticket-chat",
               "parameters": {"file": "/var/lib/robot/chats.json"}})
```

## Manage targets

```javascript
pc_source_status({"scope_id": "SCOPE_ID"})
pc_source_target_set_enabled({"scope_id": "SCOPE_ID", "target_id": "...", "enabled": false})
pc_source_target_delete({"scope_id": "SCOPE_ID", "target_id": "..."})
```

- **Deduplication**: the Server validates `parameters` against the plugin version it loaded and computes the target key
  from the `identity` parameters after their declared normalization steps. When a target with the same key exists for
  the same Scope, runner, and capability, identical parameters return the existing target and different ones a
  conflict.
- **Changes**: parameters outside the identity and enablement can change; identity parameters cannot
  (`identity_immutable`). To collect another object, add a target, so a checkpoint always belongs to one external object.
- **Disabling and deleting**: a disabled target is no longer synced to its runner and keeps its checkpoint, resuming
  where it left off; deleting also deletes the checkpoint, and collected Sources stay.
- **Status**: each target records the latest attempt, the latest success, consecutive failures, and the latest error. A
  runner's `last_seen_at` later than a target's `updated_at` means the runner has fetched the latest configuration.

## The collection contract

```python
async def collect(self, request: CollectRequest) -> CollectResult:
    result = CollectResult()
    for target in request.targets:
        ...
        result.add(target, items, checkpoint=new_checkpoint, has_more=False)
        # When only this target fails: result.fail(target, code="...", message="...")
    return result
```

- One call covers one Scope and at most `max_targets` enabled targets; with more targets, they rotate.
- Returning more items than `max_items` in total, or a target that was not requested, fails the whole call without
  truncation. Targets missing from the result keep their checkpoints.
- `fail` fails only that target: its checkpoint stays and its status records the error, while the other targets in the
  call still commit. A `collect` that raises fails the whole call.
- Put blocking reads in `await asyncio.to_thread(...)`. On a device `ctx.storage` is unavailable; `ctx.center` exists
  only on a device, and `ctx.center.call_plugin(method, path, json=..., content=...)` calls the plugin's own routes on
  the Server, for example to upload a large file before submitting a Source that refers to it.

## Versions and upgrades

`SourcePlugin` declares `version` (PEP 440) and an optional `min_compatible_version` (default: `version`). When a
device's plugin version satisfies `Server min_compatible_version <= device version <= Server version`, the Server accepts
its syncs, Sources, checkpoints, and `call_plugin` requests; otherwise it returns `plugin_version_incompatible`, sync
withholds that plugin's targets, and `pc_source_runners` shows it as incompatible.

- **Upgrade the Server first, then devices.** Device versions newer than the Server's are rejected too. With several
  Server instances, finish upgrading all of them first.
- Target parameters follow the Server version's schema and may carry fields older devices do not know, so parameter
  models must not set `extra="forbid"`. When new parameters must take effect on every device, raise
  `min_compatible_version`.
- The Server calls `upgrade(ctx)` every time it loads a plugin version (see [Write a plugin](../develop/plugins.md));
  devices do not call `upgrade`, and plugins check and migrate their own local files in `setup`.

## Migrating from the previous Source plugin framework

`/v1/source-management/*`, `powercontext source load`, `powercontext.yml`, and `write.yml` are removed:

- Rewrite a write plugin as a `SourcePlugin` that declares `definitions`, and put it in the plugin directory.
- Rewrite a collect plugin as a `SourcePlugin` that declares `collect`, `target`, and `setup`; target identity computed
  on the device becomes the `identity` declaration.
- Before upgrading, unload every loaded write plugin on the previous version; the migration refuses to upgrade while the
  database still lists write plugins.
