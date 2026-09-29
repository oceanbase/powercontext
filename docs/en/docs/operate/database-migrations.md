---
title: Database migration design
description: Understand unified migration, shared-database upgrades, backup choices, service lifecycle, and compatibility checks.
---

# Database migration design

PowerContext's unified migration design uses Alembic for schema versions and separates installing software from migrating
existing databases. This page explains the operational contract in the
[unified migration RFC](https://github.com/oceanbase/powercontext/pull/1771), tracked by
[issue #1756](https://github.com/oceanbase/powercontext/issues/1756).

## Current availability

**Unified migration is still a prototype. `server db-migrate`, `BackupProvider`, `service start/stop/restart`, and
`--manage-service` below are proposed, not released commands.** The prototype handles an isolated four-table fixture,
not business startup. Its `ready` is not complete Server readiness, and its execution records do not mean the final
single-table persistence design is implemented.

The current foreground entry point is `powercontext server run`; personal service commands provide
`install/status/uninstall`. For existing Artifact processing maintenance, follow
[Migrate Artifact processing state](artifact-processing-migration.md) using `server processing-migrate`. Do not substitute
the prototype for production migration. See [Deploy the Server](deploy-server.md) for service deployment.

## Versions and upgrade boundaries

The final framework adds one migration control table, `pc_schema_revision(version_num)`, using standard Alembic version
handling, without generic run, step, or task ledgers. Each managed schema change adds an immutable revision executed in
dependency order. A release without schema changes needs no new script. Application versions, API versions, and schema
revisions are distinct. Data tasks, validators, and impact manifests ship with the package; logs and backup records stay
outside the target database.

Installing/upgrading software does not migrate existing databases. Both local and remote databases require explicit
migration. Truly empty local databases may initialize under a lock where configuration permits; remote/shared-database
deployments use a separate initialization Job. An incompatible existing database blocks new business startup with
`migration_required`. Once enabled, all managed-schema PRs require migrations and verification. Framework development is
independent of PR #1716.

## Maintenance with one confirmation

Interactive users can go directly to proposed `apply` without assembling individual steps. This illustrates the interface
and cannot yet be executed:

```bash
powercontext server db-migrate apply --env-file deployment.env --manage-service
```

It combines target, source/target versions, affected tables/APIs, legacy tasks, downtime requirements, backup, and service
scope. Select on one screen and confirm once, followed by stopped writes, locked reinspection, backup policy, schema/data
conversion, index rebuilds, and final verification. No per-SQL confirmation is required. Read-only verification with no
persistent changes returns no changes without backup or service lifecycle actions.

Optional proposed read-only `status` and `plan` provide previews; material changes require renewed confirmation.
Automation explicitly supplies the plan and policy. For example, after manual backup and deployment-managed shutdown
of all writers:

```bash
powercontext server db-migrate plan --env-file deployment.env --backup manual
powercontext server db-migrate apply --env-file deployment.env --plan-id PLAN_ID --backup manual --backup-confirmed --maintenance-confirmed --yes
```

`--yes` accepts the plan, not a manual-backup declaration or no-backup risk. Plans bind target, versions, schema/resource
digests, compatibility, backup choice, and service scope. Missing required consent returns `confirmation_required`.
No backup choice bypasses locking, active writers, or data checks.

## Multiple Servers sharing one database

Multi-node means one shared database; this design does not cover independent per-node databases. Run one migration Job
per database.

1. Prepare the new program separately. Stop new requests and task production, then drain in-flight work and tasks that
   require the old handler according to the plan.
2. Stop all API, Worker, scheduler, and SDK writers. Suspend scaling, automatic restarts, and schedules that could launch
   old programs.
3. One new-version Job runs `apply`, takes the database-wide lock, rechecks the plan and legacy tasks, then handles backup
   and migration.
4. Start new nodes after migration returns `ready`; each checks schema, task formats, and required capabilities before
   passing readiness and accepting traffic.
5. Migration failure keeps maintenance active. If the database is ready but a node cannot start, handle startup separately
   without rerunning migration or automatically downgrading.

Migration locks exclude other migrators, not arbitrary external clients. `--maintenance-confirmed` is an operator
statement that writes are stopped. Reject execution when those conditions cannot be established. PC upgrades do not
restart the OceanBase database cluster. SQLite/embedded seekDB retain the existing single-host `all` role restriction; migration does not enable cross-host shared directories or multi-node deployment for them. Shared-database multi-node services use an already supported remote deployment topology.

## Backup choices and native methods

| Choice | Behavior | Proposed options |
| --- | --- | --- |
| PC-managed | Recommend a supported native method, warn that large data may require considerable time/space, await completion and adapter checks | `--backup auto` |
| Already backed up manually | Record only the declaration, without checking files, jobs, time, target, or recoverability; reference optional | `--backup manual --backup-confirmed`, optionally `--backup-ref BACKUP_ID` |
| No backup | Warn that original data may be unrecoverable after failure; require explicit acceptance | `--backup skip --accept-no-backup` |

Manual mode reports `user_confirmed`, not `verified`; skip reports `skipped`; genuinely empty initialization reports
`not_required`. Failed, incomplete, or insufficiently supported automatic backup stops before migration writes. Changing
policy requires renewed confirmation, never automatic skipping.

A separate `BackupProvider` is a database maintenance capability implemented by backend adapters, reusing Profile
configuration, identity, and engine lifecycle. `capabilities(context)` reports support, `create_backup(context)` starts
backup, `inspect_backup(ref)` inspects PC-managed backups, and `restore_plan(ref)` provides explicit recovery steps.
Manual and skip paths do not call it to verify user backups.

| Backend | Recommended method | Limitations |
| --- | --- | --- |
| SQLite | After stopping writes, use SQLite Online Backup API to create an independent file, checking opening and integrity | Include committed WAL; copying only the main file is insufficient. Restoration also checks extensions, indexes, and business data |
| seekDB | Version/object/recovery-validated `FORK DATABASE` for a same-instance pre-migration recovery point | Not multiple per-table forks by default; shared storage provides no disk-failure protection |
| OceanBase | Native physical backup and log archiving through configured administration or backup services, querying completion | Check jobs and log coverage; tenant restoration may affect other applications, so never default to overwriting a shared tenant |

Native references: [SQLite Backup API](https://sqlite.org/backup.html),
[OceanBase backup architecture](https://en.oceanbase.com/docs/common-oceanbase-database-10000000001168918).

seekDB V1.2.0 describes a common database snapshot, excluding some non-table objects and cross-database foreign keys.
Fork uses copy-on-write and shared underlying storage. After acceptance, it may cover failed migrations while storage
and engine remain healthy without an additional physical backup; users arrange independent disaster recovery as needed.
Multiple `FORK TABLE` operations are not the default substitute for database-wide consistency.
[seekDB V1.2.0](https://github.com/oceanbase/seekdb/releases/tag/v1.2.0),
[Fork mechanism](https://en.oceanbase.com/blog/fork-table-ready-for-agents).

Before enabling auto fork, verify the actual embedded version, index/constraint coverage, subsequent source DDL, engine
restart, and full restoration. `SeekDBConfig.database` currently fixes the database to `test`, so changing the connection
to a fork database cannot be promised. Implement restoration to the original name or separately provide configurable
targets and coordinated switching. `MERGE TABLE` does not automatically roll back schema. Unsupported coverage/recovery
returns `backup_unsupported`; users may select manual or skip. Do not copy a running directory or automatically upgrade
the database engine.

## Service shutdown and restart

An incompatible upgrade follows: stop old writers → backup policy → migration → verification → start new version →
readiness → restore traffic. Installing software is separate from replacing processes. Preserve an independently
runnable old environment when deferring the switch.

Proposed `service stop/start/restart` manages only the current user's PC-owned local service, retaining registration and
configuration. `--manage-service` displays and confirms the target executable, configuration, and original running state,
then drains/stops it and suppresses restarts. Only successful migration permits updating the confirmed launch definition
and restarting a previously running service. Previously stopped services remain stopped; failure does not restart old
services. Do not guess versions from PATH or use potentially autostarting `service install` during maintenance.

Deployment systems manage clusters and external services. For manual maintenance, first use proposed
`powercontext service stop`, run `apply` successfully, ensure the launch definition points at the new environment, then
use `powercontext service start`. `restart` does not replace migration while stopped. For foreground deployment, after
migration succeeds and the old process exits, use the existing command from the new environment:

```bash
powercontext server run --env-file deployment.env --role all
```

## Release decisions for tables, APIs, and legacy tasks

Authors ship impact manifests; operators choose deployment methods from this evidence, not merely from “changed a table”
or “internal API” labels:

| Declaration | Operational decision |
| --- | --- |
| `affected_objects` | Tables, constraints, indexes, files, large copies, resources, and irreversible transformations |
| `execution_mode` and binary/schema support | Ordinary deployment without persistent changes; stopped-write maintenance for incompatibility; online migration requires implemented and verified backend/mixed-version support |
| `api_changes` | Callers released together or independently, retained/deprecated APIs, earliest removal version/date |
| `task_formats` | Queued, running, delayed, retry, and dead-letter formats; compatible consumption, idempotent conversion, or draining |

Operators may choose stricter downtime, but cannot select online mode to override known incompatibility. Generic online
DDL is not initially promised. Same-package internal entry points can be replaced with callers; independently deployed
Workers/SDKs still need compatibility arrangements. Breaking public API replacements, such as Artifact APIs, mark old
contracts `deprecated` and specify alternatives/removal. Storage-only changes with unchanged API contracts need no
deprecation. Retained APIs can translate requests/responses to the current implementation without every handler supporting
two table layouts.

**Check legacy task formats during upgrades.** Read-only `plan` inventories queues, Leases, and payload formats, rechecking
under the lock after writes stop, after migration, and before Worker startup. Use frozen recognizers where version fields
are absent. Unknown/unobservable sources block affected migration/Workers rather than falsely reporting no tasks.
Unknown formats return `unsupported_task_format`; incomplete draining returns `legacy_tasks_pending`. Stop producers,
let old Workers drain within bounds, then stop Workers before DDL; alternatively use declared compatible consumption or
idempotent conversion. Preserve task IDs, idempotency keys, Scope/identity, retries, and domain receipts. Never delete
queues or mark unfinished work successful. Resolve retained tasks before deleting handlers; release plans coordinate
external API consumers.

## Combining existing storage layers

| Existing layer | Upgrade responsibility |
| --- | --- |
| Profile | Reuse connection configuration/engine lifecycle; split inspection/maintenance from `create_tables` side effects |
| `AsyncDatabase` | Dedicated maintenance connection and transactions; `AsyncConnection.run_sync` supplies the same connection to Alembic, keeping locks and DDL together |
| Alembic | Frozen revisions execute DDL and advance the sole version table after verification |
| Repository | Reuse stable domain operations once schema permits; historical conversions prefer frozen SQL/mappings over changing current repositories |
| Indexes | Structure definitions enter versioned resources; controlled projection rebuilding and checks replace implicit startup schema writes |
| Startup gates | Check schema, tasks, and required capabilities before composing repositories, indexes, and Workers |

For a required column, a revision first adds it as nullable, an idempotent script backfills in batches, verification permits
a later constraint-tightening revision, and affected indexes rebuild before new repositories start. seekDB/OceanBase DDL
may commit implicitly; one Python transaction cannot make the full upgrade atomic. Commit existing domain receipts with
their data batches and rebuild indexes from authoritative data.

## Interruption and recovery

After interruption, inspect through read-only `status`, `verify`, and `plan`; retry only when actual state proves it safe.
One version table offers no generic run-ID resume. Ambiguous state returns `recovery_required`. Auto retains the original
recovery point and external records rather than replacing them with a partial-state backup; manual remains unverified;
skip cannot promise original-data recovery. Every mode checks actual schema, data, and tasks without blind stamping.

Restoration is independent and explicit with writes stopped. Restore the database, required files, and matching program,
then verify. Application rollback is not database rollback. Report migration `ready` separately from startup success;
do not immediately delete recovery points. Production support remains subject to the RFC's real-backend acceptance.
