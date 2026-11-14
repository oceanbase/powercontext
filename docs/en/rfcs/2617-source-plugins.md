---
title: Source plugins and collection
description: Source plugins provide Source types and collect continuously from external systems; collection targets live on the central Server, and collection runtimes on the Server or on devices pull and sync them and report results, replacing the old Source plugin framework's management service and reverse channel.
---

- Proposal Name: `source_plugins_and_collection`
- Start Date: 2026-11-12
- RFC PR: [oceanbase/powercontext#2617](https://github.com/oceanbase/powercontext/pull/2617)

# Summary

Building on the [plugin interfaces RFC #2549](https://github.com/oceanbase/powercontext/pull/2549), this proposal defines
standalone Source plugins: a plugin provides Source types and, when needed, collects continuously from external systems.
Collection can run on the central Server or on devices such as robots and edge machines, and results are always reported to
the central Server.

- **Source plugins**: a directory with `plugin.py`, which assigns a `SourcePlugin` declaration to `plugin`. A plugin that
  provides only `definitions` extends the Source types; a plugin that declares `collect` reads from external systems per
  collection target.
- **Collection runtimes (runners)**: the processes that run `collect`. The central Server has a built-in runner named
  `server`; a process started on a device with `powercontext source-agent` is also a runner, named after the subject of its
  credential.
- **Targets live centrally**: targets are stored on the central Server in `pc_source_collect_targets`. Each belongs to a
  Scope and names the runner and the plugin that collect it. Users add, change, and remove them through the Source
  management APIs or the Dashboard without logging in to the device.
- **Pull sync**: runners sync with the central Server on a schedule, fetching every target assigned to them and reporting
  the plugins they loaded with their versions. The central Server never pushes to devices; there is no reverse channel.
- **Deduplication**: a plugin declares which parameters form a target's unique key and how they are normalized, so the
  central Server recognizes duplicates as targets are added.
- **Results**: Sources and checkpoints go through core's existing remote ingestion APIs; each target's latest status is
  reported together with its checkpoint.
- **Versions**: a Source plugin declares its version and its minimum compatible version explicitly. The version loaded on
  the central Server is the specified version; the central Server accepts a device's requests, and hands them to its own
  copy of the plugin, only when the device's plugin version is within the range the central version declares. Data
  upgrades use the `upgrade(ctx)` hook from RFC #2579 and run only on the central Server.
- **Large content**: the framework does not prescribe a transfer method; the same Source plugin can also run on the central
  Server, provide a route that receives files, and read them back in its Projections.

This proposal replaces the plugin contracts and management mechanisms of the
[Source plugin framework RFC #2012](2012-source-plugin-system.md): the management service, instance registration, reverse
channel, and `source load` command are removed, and `pc_source_write_plugins` is dropped when it holds no data.

This document describes the target design; it does not mean the mainline or a release already provides it. Interface
names and code examples illustrate the contract; the implementation determines the final syntax.

# Motivation

The old Source plugin framework (#2012) carries a lot of management for plugins: a separate management service with its own
SQLite (instances, capabilities, targets, desired state, commit records), one Runtime process per plugin instance, poll /
reply reverse channels and capability catalog sync between the service and Server, plus deployment commands and instance
start/stop tools. Deployers run several processes and maintain two databases; plugin authors must understand instances,
channels, and the deployment flow.

RFC #2549 changes Artifact plugins to a model where plugins implement interfaces and core calls them when needed; Source
plugins should work the same way. One requirement is supported by the old framework but not by RFC #2549's initial edge
design:

- **Scenarios such as embodied AI configure collection for a fleet of devices centrally.** Devices usually sit behind NAT and
  cannot be reached from outside, but they can reach the central Server. Operators need to decide on a central panel which
  device collects what into which Scope, instead of logging in to each device.
- **Configuring on a panel needs immediate deduplication and parameter validation**: when a target is added, users need to
  know whether it already exists and whether its parameters are valid, not find out after the device runs it.

Goals:

1. A plugin author writes only `plugin.py`, without learning instances, channels, or the deployment flow.
2. Targets are stored centrally; users manage any runner's targets on the central Server or the Dashboard, and adding or
   removing targets restarts nothing.
3. Devices only need to reach the central Server; the central Server never connects to devices.
4. Disconnects, restarts, and resubmission are idempotent and produce no duplicate Sources.
5. Deployers run only Server, plus one collection process per device when collecting on devices.

# Guide-level explanation

## 1. Plugin shape

Source plugins live in the same plugin directory as Artifact plugins and have the same shape:

```python
plugin = SourcePlugin(version="1.2.0", definitions=(CameraEventDefinition(),), collect=Collect(...), target=..., setup=setup)
```

| Interface | Required | Description |
| --- | --- | --- |
| `version` | Yes | The plugin version, compared under PEP 440 |
| `min_compatible_version` | No | The lowest device version the central Server still accepts while running this version; defaults to `version` |
| `definitions` | Yes | A sequence of `SourceDefinition`, the same as a plugin's own Source types in RFC #2549 |
| `target` | For collection | The Pydantic model of target parameters and the identity expression; see below |
| `setup(ctx) -> SourceCollector` | For collection | The factory, returning an object that implements `collect` |
| `collect(request) -> CollectResult` | For collection | Reads external data per target and checkpoint, returning Sources and new checkpoints |

A plugin that provides only `definitions` extends the Source types: callers submit through the existing Source write API,
and core runs `resolve` and Projections in the central host process, as for a plugin's own Source types in RFC #2549.

## 2. Example: robot camera events

```python
from pydantic import BaseModel, Field

from powercontext.plugins import (
    Collect, CollectedItem, CollectRequest, CollectResult, Interval, SourcePlugin, TargetSpec,
)


class CameraTarget(BaseModel):
    camera: str = Field(description="The camera name on the device, such as front")
    min_interval_seconds: int = Field(default=5, ge=1)


class CameraCollector:
    def __init__(self, ctx):
        self.ctx = ctx

    async def collect(self, request: CollectRequest) -> CollectResult:
        result = CollectResult()
        for target in request.targets:
            since = (target.checkpoint or {}).get("since", 0)
            events = await read_local_events(target.parameters["camera"], since)
            items = [CollectedItem(definition="camera-event", value=event) for event in events]
            result.add(target, items, checkpoint={"since": events[-1].sequence if events else since})
        return result


async def setup(ctx):
    return CameraCollector(ctx)


plugin = SourcePlugin(
    version="1.2.0",
    min_compatible_version="1.0.0",
    definitions=(CameraEventDefinition(),),
    collect=Collect(schedule=Interval(seconds=30)),
    target=TargetSpec(
        description="Collect the device camera's events",
        parameters=CameraTarget,
        identity={"camera": ("strip", "lower")},
    ),
    setup=setup,
)
```

The plugin name comes from its directory name (assumed here to be `camera-events`) and is also the name of this collection
capability.

## 3. Collection runtimes

- **Server**: the central Server has a built-in runner named `server`, which runs the collection plugins in its plugin
  directory in Workers on the Supervisor's schedule.
- **Edge**: on a device, run

  ```text
  powercontext source-agent --server https://memory.example.com
  ```

  It loads only the Source plugins in the plugin directory and connects to the central Server with its credential. The
  runner is named after the credential's subject by default (for example the service account `robot-7`); `--name` sets
  another name, and devices that share a credential must each set one. The name `server` is reserved for the central
  Server's own runner; a name is bound to the credential subject of its first sync, and no other subject can sync under
  it afterwards.
- **Sync**: a runner calls the central sync API on a schedule (every 30 seconds by default) to do three things:
  1. report the plugins it loaded, each plugin's version and load error, and when it was last seen;
  2. fetch every target assigned to it, across all Scopes;
  3. compare them with the targets it is running, start new ones, and stop removed or disabled ones.
- A runner needs no Scope list: the targets assigned to it carry their Scopes. After a new Scope is created and a target is
  assigned, collection starts at the next sync.

## 4. Managing targets

Callers use the Source management APIs (HTTP and MCP):

```text
pc_source_runners()
pc_source_add(scope_id="robot-7-data", runner="robot-7", capability="camera-events",
              parameters={"camera": "front"})
pc_source_status(scope_id="robot-7-data")
pc_source_target_set_enabled(scope_id="robot-7-data", target_id="...", enabled=false)
pc_source_target_delete(scope_id="robot-7-data", target_id="...")
```

- **Choosing a runner and capability**: `pc_source_runners` lists every runner with the plugins and versions it loaded,
  whether those versions are compatible with the central Server, and when it was last seen. A capability's description,
  parameter format, and identity expression come from the plugin loaded on the central Server. The Dashboard builds its
  form from them: choose a Scope, then a runner, then a capability, then fill in the parameters; a capability has the same
  form on every runner.
- **Validation and deduplication**: the central Server validates `parameters` against the format of the plugin version it
  loaded and computes the target key from the identity expression. When a target with the same key already exists for the
  same Scope, runner, and capability, the existing target is returned; the same key with different parameters is a
  conflict.
- **Status**: each target's latest attempt time, latest success time, consecutive failures, and latest error are reported
  by the runner together with its checkpoint. A `last_seen_at` later than the target's `updated_at` means the runner has
  fetched the latest configuration.
- **Changing**: fields of `parameters` outside the identity expression can change, and targets can be enabled or
  disabled; fields in the identity expression cannot change. To collect another object, add another target, so a
  checkpoint always belongs to the same external object.
- **Disabling and deleting**: a disabled target leaves the sync results while its checkpoint is kept, and resuming
  continues from where it stopped; deleting also deletes its checkpoint, while collected Sources are kept.
- **Permissions**:
  - Adding, changing, and removing targets of a device runner needs Source write permission on the Scope; a device can
    submit results only to Scopes where its credential has `scope.contribute`.
  - Adding, changing, and removing targets of the `server` runner needs `server.admin`: the server runner reaches external
    systems with credentials from the Server environment, so deciding what it collects is an operator decision that
    cannot be left to a user with write permission on some Scope. The server runner submits results inside the Server and
    needs no further permission.
  - Listing runners needs only authentication; a runner can only sync and report its own targets; deleting a runner
    record needs `server.admin`.

## 5. Submitting results and recovering from disconnects

A runner collects, per Scope, the enabled targets assigned to it:

- On the Server, core saves the returned Sources and each target's new checkpoint in one transaction.
- On a device, the runner first submits Sources through `POST /v1/source-observations`, then submits the new checkpoint
  through `POST /v1/connector-checkpoints/commit` with the old checkpoint as the expected value, together with the target's
  status. If anything fails midway, the checkpoint does not advance and the next call collects again from the old
  checkpoint; resubmitted Sources have the same identity and content, and the central Server deduplicates them.
- Every device request carries the plugin version; the central Server checks it as described in section 8 and hands the
  request to the same plugin loaded on the central Server.

## 6. Large content

How images, audio, and other large content reach the central Server is up to the plugin; the framework supports three
approaches:

| Approach | How | Suits |
| --- | --- | --- |
| Inline | Encode it into the Source content, bounded by the Server's request body limit | Thumbnails, short audio |
| The plugin's object storage | The runner uploads to object storage the plugin names; the Source records the URI and digest | Deployments that already have object storage |
| Through the central plugin | The same Source plugin also loads on the central Server, receives files through RFC #2579 plugin routes, and stores them in its own storage; the Source records a reference | Devices that should not reach object storage directly |

In the third approach one plugin package has two roles: on the device it collects and uploads; on the central Server it
receives files and runs `resolve` and Projections. The plugin on the device calls its own central routes through
`ctx.center.call_plugin(...)`; the runner supplies the credential and address, which the plugin never sees. The device
uploads the file first, then submits the Source that references it; the central Server runs the Projections when it
writes the Source, so they can read the file from plugin storage, and their results are saved with the Source like any
other Projection. Delivery to Artifact plugins reads only the saved results and never reruns Projections, so cleaning up
a file later cannot fail a consumer's window. Files are stored by content digest and never change once written, so
Projection results are deterministic.

## 7. Operations

- Every Source plugin a device uses must also be deployed in the central Server's plugin directory. The central Server
  registers a plugin's definition manifests (the declarations of Source types and Projections) when it loads the plugin,
  as in RFC #2549, with no separate registration command. Devices need only `scope.contribute` on the Scopes they serve.
- When a runner goes offline, its record and targets remain; whether it is online is judged from `last_seen_at`. When a
  device is retired, users delete its targets and record.

## 8. Versions and upgrades

The plugin version loaded on the central Server is the specified version for its capability, and device versions are
compared with it; the plugin is responsible for data format compatibility.

- **Compatible range**: when a device's plugin version satisfies
  `central min_compatible_version <= device version <= central version`, the central Server accepts its sync, Source
  submission, checkpoint commit, and `call_plugin` requests and hands them to its own plugin; otherwise it rejects them
  with `plugin_version_incompatible`. At sync, targets of an incompatible plugin are not delivered, while the runner's other
  plugins sync as usual; the runner list shows which devices need upgrading. Requests for a plugin the central Server has
  not loaded are rejected too.
- **Upgrade the central Server first, then devices**: a device version newer than the central one is also rejected,
  because the central Server cannot judge whether newer code is compatible. After the central upgrade, older devices
  within the range keep working and can upgrade one by one. With several central instances, each checks against the
  version it loaded, so during a rolling upgrade one device's requests may be accepted by one instance and rejected by
  another; finish upgrading every central instance before upgrading devices. Runners treat
  `plugin_version_incompatible` as retryable and retry at the next sync.
- **What compatibility means**: by declaring `min_compatible_version`, a plugin promises that the new central version can
  process Sources submitted by devices within the range; a new version that still needs to read an old version's Sources
  keeps declaring the old definitions (RFC #2579). The only thing a device reads from the central Server is target
  parameters. They are filled in under the central version's format and may carry fields an older device does not know, so
  device-side parameter models must ignore unknown fields (Pydantic's default; do not set `extra="forbid"`). When a new
  parameter must take effect on every device, raise `min_compatible_version`. Checkpoints are written and read by the same
  device and never cross between the central Server and devices.
- **Data upgrades**: the central Server calls `upgrade(ctx)` each time it loads a plugin version, under the rules of RFC
  #2579. Devices do not call `upgrade`: core keeps no plugin data on devices, and a plugin checks and migrates its own
  local files in `setup(ctx)`.

# Reference-level explanation

## 1. Declarations

```python
class TargetSpec(BaseModel):
    description: str                        # the capability description shown on the panel
    parameters: type[BaseModel]             # target parameters; core takes their JSON Schema
    identity: dict[str, tuple[Normalizer, ...]]   # the parameters forming the unique key and their normalization steps

class Collect(BaseModel):
    schedule: Interval | Cron
    max_targets: int = 50                   # maximum targets per invocation
    max_items: int = 200                    # maximum Sources returned per invocation
    timeout_seconds: float = 120
    max_workers: int = 1

class SourcePlugin(BaseModel):
    version: str                            # PEP 440
    min_compatible_version: str | None = None   # defaults to version
    definitions: Sequence[SourceDefinition]
    collect: Collect | None = None
    target: TargetSpec | None = None        # required when collect is set
    scopes: tuple[str, ...] | None = None   # Scope allowlist of the Server runner
    setup: Callable[[PluginContext], Awaitable[SourceCollector]] | None = None
```

- The plugin name is the capability name and must match `^[a-z][a-z0-9-]{0,63}$`.
- `version` and `min_compatible_version` must be valid PEP 440 versions with `min_compatible_version <= version`;
  otherwise the plugin fails to load.
- Normalization steps come from a fixed set: `strip`, `lower`, `collapse_spaces`, `remove_prefix:<value>`, and
  `remove_suffix:<value>`. The central Server and runners share one implementation. The target key is the SHA-256 of the
  capability name plus each parameter's normalized value, ordered by parameter name.
- Parameters in the identity expression must be string fields of `parameters`; parameters not listed do not take part in
  identity.

## 2. Collection contract

```python
class CollectTarget(BaseModel):
    target_id: str
    parameters: dict[str, JsonValue]
    checkpoint: JsonValue | None            # the target's last committed checkpoint; None the first time; read-only

class CollectRequest(BaseModel):
    scope_id: str
    reason: Literal["schedule", "target_added"]
    targets: Sequence[CollectTarget]
    max_items: int

class CollectedItem(BaseModel):
    definition: str                         # the name of a definition in definitions
    value: BaseModel                        # an instance of that definition's input_class

class CollectResult(BaseModel):
    def add(self, target: CollectTarget, items: Sequence[CollectedItem], *, checkpoint: JsonValue | None,
            has_more: bool = False) -> None: ...
    def fail(self, target: CollectTarget, *, code: str, message: str) -> None: ...   # only this target failed

class SourceCollector(Protocol):
    async def collect(self, request: CollectRequest, /) -> CollectResult: ...
```

- Each invocation covers one Scope and at most `max_targets` enabled targets; with more, targets rotate, and targets that
  returned `has_more` go first in the next invocation.
- Returning more than `max_items` items in total, or a target absent from the request, is a failure; nothing is truncated.
  Targets absent from the result keep their checkpoint.
- Source identity is determined by `resolve`. Repeated collection of the same external state with the same identity creates
  no duplicate Source; the same identity with different content is a conflict, and the invocation fails.
- A plugin reports a single target's failure with `fail`: that target's checkpoint stays, its status columns record the
  failure, and the other targets' results in the same invocation commit as usual. When `collect` raises, returns an
  invalid result, or the submission fails, the whole invocation fails and enters the backoff of the Supervisor (on the
  Server) or the runner (on a device).

## 3. Persistence

| Data | Handling |
| --- | --- |
| `pc_source_runners` | Added: `runner` (primary key), `capabilities` (canonical JSON: each plugin's name, version, and load error), `capabilities_digest`, `last_seen_at` |
| `pc_source_collect_targets` | Added: primary key `(scope_id, target_id)`; `runner` (indexed), `capability`, `target_key`, `parameters` (canonical JSON), `enabled`, `updated_at`, `updated_by`; unique `(scope_id, runner, capability, target_key)`; written by the runner: `last_attempt_at`, `last_success_at`, `consecutive_failures`, `last_error_code`, `last_error_message` |
| `pc_connector_checkpoints` | Kept; target checkpoints, keyed by `(scope_id, target_id)` |
| `pc_sources`, Projections, the Source journal, `pc_source_definition_manifests` | Kept |
| `pc_source_write_plugins` | Dropped; plugins that provide only definitions come from the plugin directory |
| The old Source plugin management service's SQLite | Removed with the management service, not migrated |

- Write frequency: `capabilities` is written only when its digest changes; `last_seen_at` once per runner per sync; status
  columns at each collection, in the same transaction as the checkpoint (on a device, in the checkpoint commit request).
  User changes to targets write only the user columns and never conflict with runner writes.
- The two new tables and the drop ship as one new migration revision; the migration resources of released versions stay
  unchanged. The migration preflight refuses to upgrade while `pc_source_write_plugins` still holds data; the deployer
  uninstalls the old plugins on the old version first.
- Export and restore include both new tables; `last_seen_at` in `pc_source_runners` and the status columns of
  `pc_source_collect_targets` are not exported and restore empty.

## 4. APIs

| API | Permission | Description |
| --- | --- | --- |
| `GET /v1/source-runners` (MCP `pc_source_runners`) | Authenticated | Every runner, its capabilities, whether their versions are compatible with the central Server, and when it was last seen; capability descriptions and parameter formats come from the central plugin |
| `POST /v1/source-runners/{runner}/sync` | The runner itself | The request carries the capability list (plugin versions and load errors); the response returns every enabled target of a compatible version assigned to the runner with its checkpoint |
| `DELETE /v1/source-runners/{runner}` | `server.admin` | Deletes a runner record; a conflict while it still has targets |
| `POST /v1/scopes/{scope_id}/source-targets` (MCP `pc_source_add`) | Source write on the Scope; `server.admin` when the runner is `server` | Creates a target: `runner`, `capability`, `parameters`; validated and deduplicated |
| `GET /v1/scopes/{scope_id}/source-targets` (MCP `pc_source_status`) | Scope read | Targets, their configuration, and status |
| `PATCH /v1/scopes/{scope_id}/source-targets/{target_id}` (MCP `pc_source_target_set_enabled`) | As for creation | Changes fields of `parameters` outside the identity expression, or enables and disables |
| `DELETE /v1/scopes/{scope_id}/source-targets/{target_id}` (MCP `pc_source_target_delete`) | As for creation | Deletes a target and its checkpoint |
| `POST /v1/source-observations` | Existing | Requests from a runner gain the plugin name and version, checked against the compatible range |
| `POST /v1/connector-checkpoints/commit` | Existing | The request gains an optional `status`: latest attempt, success, error category, and message; requests from a runner gain the plugin name and version, checked against the compatible range |

"The runner itself" means the authenticated subject equals the runner name, or the subject recorded when the runner first
reported.

Requests sent through `call_plugin` carry the plugin version too.

Removed: the instance, configuration, channel, and write-plugin loading APIs under `/v1/source-management/*`; MCP
`pc_source_instances` and `pc_source_instance_set_enabled`; the `source load` command; and the `powercontext.yml` /
`write.yml` manifests. The central Server registers definitions when it loads a plugin; there is no separate definition
registration command.

## 5. Device-side context

On a device, `PluginContext` adds `center`:

```python
class PluginCenter(Protocol):
    async def call_plugin(self, plugin: str, method: str, path: str, /, *, json: JsonValue | None = None,
                          content: bytes | None = None, content_type: str | None = None) -> PluginResponse: ...
```

It calls central plugin routes (`/v1/plugins/{plugin}/api{path}`) with the runner's credential and can reach only the
plugin's own routes. A central Source plugin may declare RFC #2579 routes, and its Projections may read plugin storage
through `ctx.storage` when the Source is written (section 6).

On devices, core keeps no plugin data, so `ctx.storage` is unavailable and raises when accessed; the plugin manages its
own local files. `ctx.center` exists only on devices, which lets a plugin tell which side it runs on.

## 6. Mapping from the old framework

| Old framework | This proposal |
| --- | --- |
| The management service and its SQLite | Two central tables |
| Instance registration (by an administrator) | Runners report themselves when they sync, named after their credential subject |
| Reverse poll / reply channels | Runners pull and sync |
| Instance `scope_ids` | Targets carry their Scope |
| `default_capabilities` default instances | Not provided; users choose the runner on the panel |
| `identify` calls on the device | A declarative identity expression computed centrally |
| The built-in `content.inline` capability | The existing Source write API |
| Write plugins (`pc_source_write_plugins`) | Source plugins that provide only `definitions` |
| The tickets, ticket_chat, and images examples in `examples/source_plugins` | Rewritten as Source plugin examples |

# Drawbacks

- **Two more tables**: migrations, export and restore, and both database backends must support them.
- **Limited normalization**: identity uses only fixed normalization steps, so complex rules such as URL normalization cannot
  be expressed; plugins can merge identical objects themselves while collecting.
- **Configuration takes effect with a delay**: up to the sync interval, 30 seconds by default.
- **Devices sharing a credential must be named by hand**: two devices with the same name get the same targets, which core
  cannot tell apart; only alternating reports in the status show it.
- **The central Server must be able to load every Source plugin on devices**: libraries used only on devices cannot be
  imported at the top of `plugin.py`, or the central load fails; splitting dependencies by side is left to the later
  plugin installation and dependency proposal.
- **The central Server must upgrade first**: when devices upgrade before it, requests from the newer devices are rejected
  until the central Server upgrades.

# Rationale and alternatives

| Option | Trade-off |
| --- | --- |
| Targets stored locally on the device (RFC #2549's initial design) | No new table, but the central Server can neither see nor manage device targets; embodied scenarios would need logging in to each device |
| Targets stored as Artifacts of a built-in family (like Scope Prompt management) | No new table, with revisions, restore, and export ready-made; but finding a device's targets means searching across Scopes without an index |
| Each device configures a home Scope that lists the Scopes it serves | No new table, but assigning a new Scope to a device means first finding its home Scope, which still needs a scan |
| The central Server pushes configuration through a reverse channel (the old framework) | Configuration applies immediately, but it needs long-lived connections, leases, and instance registration, the main source of the old framework's complexity |
| Targets with a revision and an "applied revision" | Helps concurrency control and showing what is applied; but runners fetch everything on each sync and time-based checks are enough, which also avoids clock and incremental-fetch problems |
| `identify_target` written as plugin code | More expressive, but the central Server cannot run it for a plugin on a device, so duplicates are found only afterwards |
| Each runner reports its own parameter format and targets are validated against their runner's format | The central Server need not upgrade first; but a capability has different forms on different devices, targets must be revalidated one by one after a format change, and there is no way to judge whether a device version is compatible |
| The plugin provides a parameter migration function and core rewrites target parameters | Incompatible formats could upgrade automatically; but runners would write back parameters users filled in, conflicting with user edits, and raising `min_compatible_version` already constrains device versions |
| Devices also call `upgrade(ctx)` | Symmetric with the central Server; but core keeps no plugin data on devices, and running the same `upgrade` on both sides would require the plugin to tell which side it is on |
| No deduplication | Duplicate targets only collect twice, and Sources are still deduplicated by identity; but panel configuration needs an immediate hint, and lists and statistics need unique targets |

# Prior art

- **AWS IoT Device Shadow and Azure IoT Hub Device Twin**: the cloud stores the desired state, devices report the actual
  state, and devices connect to the cloud to sync.
- **Kubernetes spec and status**: users write the desired state, controllers write observations, and the two are stored
  separately.
- **The Airbyte connector protocol**: a connector receives the previous state and emits records and a new state, which the
  platform stores.
- **Existing PowerContext foundations**: the remote ingestion APIs `POST /v1/source-observations` and
  `/v1/connector-checkpoints/*`, definition manifest registration, RFC #2549's plugin host processes and Supervisor
  scheduling, and RFC #2579's plugin routes.

# Unresolved questions

| Question | Notes |
| --- | --- |
| Visibility of the runner list | Every authenticated user can see the runner list and capabilities today. Whether it should be limited by Scope |
| Targets per runner | Fetching everything on each sync suits a few to a few dozen targets per device; whether a Server runner collecting hundreds or thousands of accounts needs paging or incremental sync |
| Default runners | Whether plugins or deployers may name a default runner per capability to reduce choices on the panel |

# Future possibilities

- Plugin installation and dependencies: installing from PyPI, a Git URL, or a wheel, with plugins on devices downloading the
  version the central Server specifies during sync, and dependencies declared separately for the central Server and devices.
- Push notification: an optional long-lived connection besides sync, so configuration applies sooner.
