---
title: "Desktop local Server setup"
description: "Approved installation, durable recovery and independent service management in Desktop."
---

# Desktop local Server setup

The complete local workflow lives under Settings & diagnostics → Local Server. The first-use entry also offers existing-Server connection, which never mutates local installation. Installation and service rules remain owned by independent Ops; Desktop translates the typed contract and never executes renderer-supplied commands or plans.

## Plans and execution

Every install/repair/service change requires preflight, review and explicit execution. Native-owned plans expire after ten minutes and revalidate resource/binding/state identity. The preview shows actual program/config/data locations, endpoint, Server version, required bytes, login-start setting and start/readiness effects. New personal installations follow RFC 1733: loopback, SQLite/user data and model-free basic Memory. Existing choices and data are preserved. The login-start choice is offered only where the OS adapter supports it; unsupported choices are not silently substituted.

Progress phases are observations, not persistence evidence or an ETA. The UI separates installation, configuration, registration, process, liveness and readiness. It keeps long operations in the app-level controller across page changes. Passive refresh only reinspects; readiness is invoked by an approved start/install or explicit connection handoff.

## Recovery

The native reference store adds optional approved `operation`/`options` to its existing version-1 records; Ops journals remain authoritative for outcomes and attempts. Existing records load without fabricated metadata and remain query-only. Older bridge binaries that reject these additive fields cannot read newly written references; do not downgrade a controlling bridge during recovery. Preserve the store and use the matching/newer bridge.

The selected operation is queried on reopen. Only that selection is queried, avoiding thousands of automatic helper processes for retained history. Status and cancellation remain available during an active mutation. Cancellation does not imply rollback. Terminal failure or confirmed executor interruption can offer a fresh preflight/resume using the original action/options and expected attempt. Unknown execution never automatically resumes. A lost reply refreshes durable references; it never reruns apply. Foreign bindings, ownership, occupied ports, damaged resources and incompatible databases remain distinct producer errors with supported next actions.

## Connection and removal

Explicit connection handoff reinspects native ownership and resources and uses the actual endpoint. The pinned resource's `qualification_ref` must equal a bundled Desktop compatibility profile ID for the current HTTP contract; its resource digest must match inspection. Distribution owners set this mapping only after qualification. The resource `source_refs` must contain `git+<repository>@<qualified Server commit>` and `wheel-sha256:<qualified Server wheel digest>`, matching that profile's original evidence; the same API contract alone cannot qualify another build. An `unqualified-candidate:...` resource never establishes compatibility. Discovery must report the pinned Server package version. Default Scope is resolved with `get_default_scope` and selected by its returned exact ID. No raw Scope ID or copied local token is required. Existing remote/authenticated profiles remain independent; protected configurations can use the ordinary connection editor. Saved profiles are reused only for the same endpoint and qualification; the app does not silently rewrite independently saved targets or historical qualification choices. The existing 20-profile limit remains; remove unused Desktop profiles through Connections when necessary, without stopping the Server or deleting data.

Stop, registration removal, Desktop removal and data deletion have different effects. This UI offers stop and registration removal, preserves configuration/data, and provides no data-deletion action. OS-managed service lifetime is independent of Desktop. Reopen after Quit reinspects authoritative operation state and does not activate a private session.

## Validation boundary

UI tests use mocked native IPC. Native connection tests use synthetic HTTP discovery and Scope responses. Bridge tests use disposable helper processes and private reference stores. These checks establish local contract behavior, not signed Windows 11/macOS clean-machine installation, login/reboot, IME or installed screen-reader/credential acceptance. Those require exact published resources and final packages under #2108/#2109/#2112; retain #2117 until its complete installed acceptance is linked.
