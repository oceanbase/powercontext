---
title: Source plugins
description: Load trusted local plugins for synchronous writes or ongoing Source collection through the public MCP.
---

# Source plugins

Hosts connect to the PowerContext Server's public `/mcp`. A `write` capability saves supplied content synchronously inside Server. A `collect` capability runs in a separate Runtime; the shared management service tracks targets and lifecycle state.

Loading code is a deployment operation requiring `server.admin`. Install trusted plugin code and dependencies in the Python environment that executes them: Server for write, the Runtime machine for collect. Credential files must be regular files, not symlinks; on Linux, set permissions to `600` (`chmod 600 /path/to/token`).

Before starting, prepare a Server with authentication and PC Access enabled, an existing Scope, and separate deployment administrator and ordinary host credentials. Replace paths, addresses, and `SCOPE_ID` with actual values. MCP examples use `tool_name(parameters)` shorthand for the `name` and `arguments` of a `/mcp` `tools/call`. Use discovery results for capability names, versions, and input fields.

| Operation | PC permission |
| --- | --- |
| Discover capabilities or instances; query targets | `scope.read` on the relevant Scope |
| Write or add a collect target | `scope.contribute` and `scope.read` on the relevant Scope |
| Enable/disable a target; read its control view | `scope.contribute` on the relevant Scope |
| Load plugins; configure/control instances; read instance control views | `server.admin` |

Plugins manage credentials for external systems; these do not replace PC permissions. Hosts only connect to Server and do not need a collect Runtime installed.

## Load a write plugin

Prepare a Python package exporting `definitions` and write `capabilities`, and a configuration file:

```yaml
id: local-tickets
entry: .
```

Save this as `write.yml` in the plugin directory; entry `.` selects its `__init__.py`. On the Server machine, run from that directory:

```sh
powercontext source load --kind write --config ./write.yml \
  --server-url http://127.0.0.1:8000 --credential-file /run/secrets/pc-admin
```

Server imports executable code, validates inputs and Definition/Projection contracts, and publishes capabilities only after successful registration. It persists the entry and imports it again on restart. Identical loads reuse the registration. Currently, code updates through a controlled Server restart must preserve the entry, capabilities, and Definition/Projection declarations; Python hot replacement is not supported. Changing declared versions or input/output schemas makes restoration fail because the persisted contract differs. Restarting does not perform a contract upgrade.

A host with `scope.read` and `scope.contribute` on the selected Scope calls:

```javascript
pc_source_capabilities({"scope_id":"SCOPE_ID"})
pc_source_add({
  "scope_id":"SCOPE_ID","capability":"ticket.write","capability_version":"1",
  "input":{"namespace":"support","project":"demo","ticket_id":"A","title":"Recovery","body":"Service restored"}
})
```

Write does not take `instance_id`. Returned `result.source_refs` identify saved Sources and Projections. Write creates no persistent request and uses neither collect targets nor checkpoints. Identical content under the same Source identity is reused; different content under that identity conflicts. The ticket example derives identity from the complete snapshot. Timeouts mean the result is unconfirmed, not that nothing was saved. Complete add arguments are limited to 1 MiB of JSON and materialized observations to 4 MiB, including Base64 expansion. Oversized input fails rather than being truncated.

## Load collect on a running Server

Enable authentication and PC Access, and configure a trusted instance directory in the Server environment file:

```dotenv
POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR=/srv/powercontext/source-plugins
POWERCONTEXT_SERVER_ACCESS='{"mode":"enforced"}'
```

Restart Server after initial configuration. Automatic deployment creates separate management and execution credentials without editing user identity files. Startup scans direct child directories for `powercontext.yml`; subsequent additions require an explicit load. This scan deploys collect instances; load write plugins explicitly with `--kind write` as above. Local collect deployment requires Linux: `source_management.process_mode="background"` (the default) uses background processes; `"local-service"` additionally requires an available systemd user service manager (`systemctl --user`). Neither mode supports local deployment on macOS or Windows. This restriction concerns collect process management, not Server-local synchronous write plugins.

An instance directory keeps `__init__.py`, `definition.py`, data files, and `powercontext.yml` together:

```yaml
id: my-tickets
plugin: .
scope_ids: [SCOPE_ID]
config:
  file: tickets.json
  poll_interval: 10
```

Scopes must exist, and configured scope selection does not grant PC permissions. The plugin's `Config` validates business parameters; the plugin controls collection timing.

On the Server machine, using the same environment file:

```sh
powercontext source load --config /srv/powercontext/source-plugins/my-tickets/powercontext.yml \
  --env-file ./pc.env
```

Load registers Definitions, grants execution permissions, registers the instance, and starts its Runtime. Repeating an identical load preserves the process and enabled state; changing the code entry or business configuration requires an explicit update. The manager owns the deployed instance's Scope selection and targets; repeating load does not overwrite them from the file.

Hosts establish ongoing collection through the public MCP:

```javascript
pc_source_instances({"scope_id":"SCOPE_ID"})
pc_source_add({
  "scope_id":"SCOPE_ID","instance_id":"my-tickets",
  "capability":"tickets.item","capability_version":"1","input":{"id":"A"}
})
pc_source_status({"scope_id":"SCOPE_ID","target_id":"TARGET_ID"})
```

Add and repeated add return the target identity and control state, without Source references or submission time. Status includes only the manager's most recently received successful submission report: `source_refs` contains one reference, paired with `last_submitted_at`. Before any report, the reference array is empty and no submission time is available. Reports replace this state in arrival order; it is neither a completed batch nor a history of collected Sources. The manager does not accumulate historical references, and a lost report does not undo a saved Source. Plugins retain the real SourceRef in each submission receipt and use checkpoints for collection progress. Repeated add reuses the same target without restarting collection or re-enabling a disabled one. Optional manifest `targets` only initialize the first deployment; subsequent target state belongs to the manager.

## Remote collect Runtimes

Prepare the code on its execution machine and add this deployment block:

```yaml
deployment:
  server:
    url: https://pc.example.com
    credential_file: /run/secrets/pc-admin
  management_url: https://source-manager.example.com
  execution_credential_file: /run/secrets/pc-worker
  state_dir: ./instance-state
```

Run `powercontext source load --config ./powercontext.yml` there. Server and the manager must already be reachable; non-local connections require HTTPS. Deployment and Runtime identities must differ, and the Runtime identity must not have `server.admin`. The command starts a local Runtime; it does not distribute code or execute remote shells. Explicitly choose `deployment.start: local-service` for systemd user services.

Ordinary background processes do not restart automatically after a machine reboot. Restart an already deployed instance using the Runtime configuration returned by load, without registering it again:

```sh
powercontext source runtime start --config /path/to/runtime-config.json
powercontext source runtime status --config /path/to/runtime-config.json
```

For systemd supervision, add `--system-service` to the start/status commands above. A live process only confirms that Runtime is running; also check instance availability and actual Source submissions through MCP.

## Extend an instance's Scopes

When only adding Scopes, first grant the execution identity the corresponding permissions through the Access API. Then submit the complete instance registration with `update=true` to `POST /v1/source-management/configure`, extending `scope_ids` without changing other configuration. The instance can keep running with its existing Context and checkpoints. Hosts with permission on the new Scope can then add targets. Editing the manifest and repeating load does not perform this expansion.

## Disable and update

An administrator disables the instance and checks its control state:

```javascript
pc_source_instance_set_enabled({"instance_id":"my-tickets","enabled":false})
pc_source_instances({"instance_id":"my-tickets","view":"control"})
```

After `actual=stopped`, stop the outer Runtime on its execution machine using the configuration path returned at deployment or saved in its private state directory:

```sh
powercontext source runtime stop --config /path/to/runtime-config.json
powercontext source load --update --config /path/to/powercontext.yml
```

For local automatic deployment, keep the same `--env-file`. Stopping a system service requires `--system-service`. When reducing Scopes, changing business configuration, or upgrading code, preserve the instance ID, state directory, and compatible Definition, capability, target identity, and checkpoint contracts. `source load --update` requires the old Runtime to have exited. The replacement stays disabled until an administrator explicitly sets `enabled=true`. Ordinary load does not implicitly upgrade.

To stop collecting one item without affecting the whole instance, control its target:

```javascript
pc_source_target_set_enabled({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","enabled":false})
pc_source_status({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","view":"control"})
```

`stopping` means the stop intent is saved; `stopped` means Runtime has confirmed the stop. Set `enabled` to `true` to resume; this does not enable a disabled instance. A disconnected manager does not prove that collection stopped. Disabling preserves Sources, targets, and checkpoints. Source saving, checkpoint updates, and submission reports are separate operations.

The current implementation has no complete unload command that deletes an instance registration and its targets. `runtime stop` stops execution while preserving management records and saved data. Stopping Server or deleting a plugin directory does not uninstall the independent services.

## Troubleshooting

| Symptom | Check or action |
| --- | --- |
| Startup logs report `background-unsupported` or `service-unavailable` | Local collect deployment requires Linux; `local-service` also requires a working `systemctl --user`. Check the execution platform and selected process mode. |
| Manager disconnected | Collect can continue with synchronized targets; new management calls may fail. Built-in and loaded write capabilities do not depend on the manager. |
| Runtime restarts while the manager is unreachable | Wait for target synchronization; do not fall back to initial targets in the file. |
| A target produces no new Sources | Check that collection logic is still running, its schedule, external credentials, and Scope permissions. A live process does not prove successful collection. |
| State remains stopping | Check the original Runtime and management connection; disconnection does not confirm a stop. |
| A saved Source is absent from target status | Submission reports may be lost. Target status is not a complete inventory of Server data; do not rewind checkpoints on that basis. |
| An agent does not use saved content | Check that collection and host Scopes match, then check downstream Projection, Artifact, and indexing processing. |

An ongoing collector maintains its own loop in `apply`. After `apply` exits, new targets or reconnection do not restart it. To run it again, explicitly disable the instance, confirm it has stopped, then enable it.

## Runnable examples

The repository's `examples/source_plugins` groups examples by source: tickets, ticket/chat and images. Each directory contains its code and loading configuration. The tickets example shares one Definition and Projection implementation across write and collect: use `write.yml` for Server-local synchronous writes or `powercontext.yml` for managed collection. Loading and MCP usage are documented in `examples/source_plugins/README.zh.md`.

Keep generated credentials, databases, logs, and process state on the deployment machine. Commit plugin code, configuration templates without sensitive values, and synthetic example data.
