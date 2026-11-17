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
| Register a runner | Authenticated |
| Add a target to a device runner, change its parameters, or enable it again | `scope.contribute` on the Scope, and being the principal that registered the runner or having `server.admin` |
| Disable or delete a target of a device runner | `scope.contribute` on the Scope |
| Add, change, disable, or delete targets of the `server` runner | `server.admin` |
| Transfer a target to another runner (`pc_source_target_transfer`) | As for adding it to the destination runner; `server.admin` when the source runner is `server` |
| Sync a device runner and submit Sources and checkpoints | The subject that registered the runner, and `scope.contribute` on the target Scope |
| Change a runner's display name | `server.admin` |
| Delete a runner record | The subject that registered it, or `server.admin` |

A device runner collects with its device's credential, network position, and local files, so the principal that
registered it decides what it collects: a user with only write permission on a Scope cannot assign targets to another
user's device, but can disable or delete targets that write into their Scope. A runner shared by a team gets its targets
from the account that runs it or from `server.admin`.

The `server` runner reaches external systems with credentials from the Server environment, so deciding what it collects
is an operator decision and requires `server.admin`. Plugins read their own credentials for external systems (for
example from environment variables); those do not replace PowerContext permissions. A target's parameters are stored
centrally, shown to the Scope's readers, and exported with the Scope, so they cannot hold credentials: a target with a
value under a parameter named `api_key` or `password`, or ending in `_token`, `_secret`, and the like, is refused with
`invalid_parameters`. Absolute paths on the runner are fine as parameters.

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

- **Name**: a runner registers with the Server when it first starts. The Server decides its name, which is unique across
  the deployment and never changes; targets are assigned to runners by name. The name `server` is reserved for the
  Server's own runner. Administrators can also give a runner a display name.
  - By default the runner suggests a name (`--suggest-name`, the host name unless given). The Server uses it when nobody
    has it and assigns another one otherwise, such as `robot-7-2`. The runner keeps the decided name in its state
    directory (`--state-dir`, the PowerContext data directory by default) and reuses it after a restart. It exits with an
    error when the state directory is not writable.
  - Deployments that keep no state, such as containers and CI, set the name with `--name robot-7`: the name is registered
    when it does not exist, reused when the same credential registered it, and the start fails when another credential
    did. This way reads and writes no state directory. The default way also works with the state directory on a
    persistent volume.
  - One credential can register several runners, but each device should have a credential of its own. Only the subject
    that registered a runner syncs and submits under its name.
  - After the state is lost (a reinstall, a cleared directory), a default start registers a new name; move the old
    targets to it with `pc_source_target_transfer`, or start with the same credential and `--name` set to the old name.
    Delete the state file before cloning a machine image, or two devices use the same name.
  - A device that moves to a credential of another subject cannot keep its name: register a new runner, transfer the
    targets, and delete the old record.
- The runner syncs every 30 seconds (`--interval`): it reports the plugins and versions it loaded and fetches every
  target assigned to it across Scopes. New targets are collected right away; the others follow the plugin's `schedule`.
- For each target the runner submits the Sources first and then the new checkpoint and status, with the old checkpoint
  as the expected value. If anything fails in between, the checkpoint does not advance; the next collection repeats the
  same content, and the Server deduplicates it by Source identity.
- When a target is changed, disabled, transferred, or deleted while it is being collected, the Server refuses that
  checkpoint commit (`target_changed`). The runner discards the result and collects again under the new configuration
  after its next sync; Sources already submitted stay.
- A plugin that fails to load, a `collect` that raises, or an item of the wrong shape affects only that plugin or target;
  the other plugins and targets keep collecting. The Server shows only fixed error codes and the error's type name; the
  full error is in the device's log.
- Every Source plugin used on devices must also be in the Server's plugin directory: capability descriptions, parameter
  schemas, and Projections follow the version the Server loaded.

Targets can be assigned only to registered runners, so start the runner once and then assign it a target:

```javascript
pc_source_add({"scope_id": "SCOPE_ID", "runner": "robot-7", "capability": "ticket-chat",
               "parameters": {"file": "/var/lib/robot/chats.json"}})
```

## Manage targets

```javascript
pc_source_status({"scope_id": "SCOPE_ID"})
pc_source_target_set_enabled({"scope_id": "SCOPE_ID", "target_id": "...", "enabled": false})
pc_source_target_transfer({"scope_id": "SCOPE_ID", "target_id": "...", "runner": "robot-8"})
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
- **Transferring**: a target can be reassigned to another registered runner. Its ID stays the same and its checkpoint is
  kept, so the new runner continues where the old one stopped. Add `"reset_checkpoint": true` when the new device's data
  differs, to collect from the beginning. A target for the same object already on the destination runner is a conflict.
- **Deleting a runner**: a runner that still has targets cannot be deleted; transfer or delete its targets first.
- **Configuration version**: every change of parameters or enablement, every transfer, and every checkpoint reset
  increases the target's `config_version`. A runner submits results with the version it collected under, and a commit
  with another version is refused. A change, transfer, or deletion applies only to the state the request read: when two
  requests change one target at the same time, the later one gets `409 conflict` and is repeated after reading the
  target again.
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

- One call covers one Scope and at most `max_targets` enabled targets; with more targets, they rotate: targets whose
  last call returned `has_more` go first, then the least recently attempted.
- Returning more items than `max_items` in total, or a target that was not requested, fails the whole call without
  truncation. Targets missing from the result keep their checkpoints.
- `fail` fails only that target: its checkpoint stays and its status records the error, while the other targets in the
  call still commit. A `collect` that raises fails the whole call: every target of the call is recorded as
  `collect_failed`, with only the error's type name as the message.
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
- When the database lists write plugins (after `source load --kind write`), the migration refuses to upgrade with
  `unsupported_plugin_data` and leaves the database unchanged. The previous version has no operation that removes these
  registrations, so such a database currently has no supported in-place upgrade path: keep using the previous version,
  or move to a new database; do not delete the records by hand to bypass the check. Databases that never loaded a write
  plugin are unaffected.
- Collection instances and configuration of the previous management service are not migrated; configure them again
  through the target APIs above. Collected Sources and their type definitions are kept, and a rewritten plugin that
  keeps declaring definitions of the same name and version keeps the saved Sources readable.
