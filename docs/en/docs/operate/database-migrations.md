---
title: Database migrations
description: Understand migration commands, backup choices, service lifecycle, and current acceptance boundaries.
---

# Database migrations

Unified migration uses Alembic and adds only `pc_schema_revision(version_num)` as its control table. Installing software
and migrating a database are separate operations. Managed schema changes ship as immutable revisions executed explicitly.
The design follows [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771) and
[issue #1756](https://github.com/oceanbase/powercontext/issues/1756).

## Current availability

This branch provides `powercontext server db-migrate status/plan/apply/verify`. They read the same deployment settings as
the Server and do not accept arbitrary test-bundle paths. Frozen resources ship in the wheel for Phase A acceptance of a
four-table Artifact migration. Only persistent SQLite databases with registered schema shapes are supported. A complete
Server database containing unmanaged objects returns `unknown_baseline`; unified seekdb and OceanBase migration returns
`unsupported_backend`.

`ready` means this migration bundle passed its schema and data checks. Output explicitly includes
`readiness_scope=registered_bundle` and `server_ready=false`: it does not establish complete Server, index, legacy-task, or
cluster readiness. Do not substitute it for a production upgrade or initialization of the complete business database.
Full startup gates and historical baselines require acceptance across all three backends before integration.

The existing `server processing-migrate` remains available. Follow
[Migrate Artifact processing state](artifact-processing-migration.md); it does not yet forward to the unified entry point.
See [Deploy the Server](deploy-server.md) for service deployment.

## Read-only inspection and one confirmation

Inspect the target or preview a plan. These commands never create a missing database, parent directory, or control table:

```bash
powercontext server db-migrate status --env-file deployment.env
powercontext server db-migrate plan --env-file deployment.env --backup auto
powercontext server db-migrate verify --env-file deployment.env
```

Plans bind target identity, source/target revisions, actual schema, frozen resources, configuration digests, backup policy,
and service scope. Diagnostics do not print configuration or credential values. Expected failures return stable JSON
categories and a nonzero exit status.

Within the supported acceptance scope, interactive users can invoke `apply` directly. PC-managed backup is the default.
The command displays the plan, backup method, maintenance impact, and stopped-write responsibility, then asks for one
confirmation before execution. It does not ask about each SQL statement:

```bash
powercontext server db-migrate apply --env-file deployment.env --backup auto
```

That confirmation declares all other APIs, Workers, SDK clients, schedules, and automatic restarts stopped. Shared-database
operators also coordinate subsequent node upgrades. PC cannot discover arbitrary external clients. Material plan changes
block execution and require a new review.

Automation explicitly supplies the policy, plan, maintenance declaration, and policy-specific consent. After making a
manual backup and stopping every writer:

```bash
powercontext server db-migrate plan --env-file deployment.env --backup manual
powercontext server db-migrate apply --env-file deployment.env --backup manual \
  --plan-id PLAN_ID --backup-confirmed --maintenance-confirmed --yes
```

`--yes` accepts the plan; it does not declare a manual backup, accept no-backup risk, or confirm all nodes stopped writing.
`--shared-database` binds shared-database responsibilities to the plan and does not enable cross-host SQLite deployment.
When the bundle is already verified and no persistent change is needed, `apply` returns `changed=false` without backup or
service lifecycle actions. Switching the application version remains a separate deployment operation.

## Backup choices

| Policy | Behavior | Options |
| --- | --- | --- |
| PC-managed | Use a supported native method, await completion and checks; warn that large data may need considerable time and free space | `--backup auto` |
| Manually backed up | Record only the declaration without checking files, jobs, time, target, or recoverability | `--backup manual --backup-confirmed`, optionally `--backup-ref BACKUP_ID` |
| No backup | Explicitly accept that failure may make original data unrecoverable | `--backup skip --accept-no-backup` |

Automatic backup reports `completed`, manual reports `user_confirmed`, skip reports `skipped`, and genuinely empty
initialization reports `not_required`. Unsupported, failed, or incomplete auto backup stops before migration writes.
It returns `backup_unsupported` or a backup failure category and never silently skips. Changing policy requires a new plan
confirmation.

A separate `BackupProvider` reuses backend settings and maintenance connections. SQLite uses Online Backup API to create
an independent file, including committed WAL, and checks integrity. The seekdb/OceanBase cluster design prefers native
`FORK DATABASE`. `FORK TABLE` is eligible only when every affected object and dependency, cross-table consistency, and
restoration path have passed adapter acceptance. PC does not automatically replace unavailable remote Fork with physical
backup.

| Product | First `FORK TABLE` version | First `FORK DATABASE` version |
| --- | --- | --- |
| OceanBase AI Database | V4.6.2 | V4.6.2 |
| seekdb | V1.1.0 | V1.2.0 |

Version eligibility is only the start: capability checks also consider actual product, tenant mode, permissions, objects,
subsequent DDL limits, and restoration. Several table Forks do not automatically constitute one database snapshot. Fork
shares underlying storage and does not protect against disk failure. Remote unified migration has not completed full
acceptance; an implemented provider is not a production-upgrade guarantee.

Retain old business tables, Fork recovery points, and SQLite backups during this upgrade. Migration success, normal
startup, and retry do not delete them. A later independent revision removes old tables; explicit maintenance removes
recovery points after checking dependencies, retention windows, and recovery obligations.

## Stopping, starting, and upgrading services

Personal services provide these commands while retaining registration and settings:

```bash
powercontext service stop
powercontext service start
powercontext service restart
```

They manage only the current user's PC-owned local service, not a cluster or other clients. `stop` suppresses automatic
restart during maintenance; `start` restores startup. One `restart` cannot replace stopped-write migration.

The complete managed-upgrade design drains requests, stops the service and automatic restarts, takes the migration lock,
reinspects, applies backup policy, migrates, verifies, switches the launch definition to the accepted new executable, and
starts a previously running service with readiness checks. Migration failure keeps it stopped; a previously stopped
service stays stopped. Database verification and service startup are reported separately. Resolve startup failures without
rerunning a successful migration.

`--manage-service` always covers only the accepted local service. It cannot replace `--maintenance-confirmed` for other
writers and shared nodes. The Phase A partial bundle does not establish complete Server readiness: when persistent changes
are needed, `--manage-service` returns `service_unsupported` rather than switching a business service.

For manual deployment, stop the old service first. After a supported complete migration succeeds, ensure the launch
definition points to the new environment before starting it. For foreground deployment, exit the old process and use the
existing command from the new environment:

```bash
powercontext server run --env-file deployment.env --role all
```

## Shared databases, APIs, and legacy tasks

For multiple Servers sharing one database, run one migration Job per database. Deployment tooling stops traffic and task
production, drains requests and tasks requiring old handlers, stops all writers, and suspends scaling and automatic
restarts. Coordinate subsequent node upgrades; each node passes its schema, task, and capability checks before traffic
returns. A migration lock excludes other migrators, not unknown external clients.

Release authors provide affected objects, execution mode, binary/schema compatibility, API changes, and task formats.
Breaking public API replacements require deprecation, alternatives, and removal plans. Same-package internal entry points
can change together with callers. Retained API adapters can translate requests/responses into one current implementation
without every handler supporting two table layouts.

Legacy tasks require inspection in the read-only plan, after stopped-write locking, after migration, and before Worker
startup. Unknown formats or unobservable sources must not be reported as an empty queue. Use declared compatible
consumption, idempotent conversion, or bounded draining. Preserve task IDs, idempotency keys, Scope, retries, and domain
receipts. The Phase A bundle does not yet cover these complete business checks.

## Storage layers and recovery boundaries

Maintenance reuses configuration and keeps Alembic and locking on a dedicated connection. Frozen historical SQL does not
depend on current Repositories or application metadata. Repositories run after schema checks; versioned resources and
controlled rebuilds manage full-text/vector indexes. seekdb/OceanBase DDL may commit implicitly, so one Python transaction
cannot make the entire upgrade atomic.

One version table offers no generic run-ID resume. After interruption, inspect actual schema/data through read-only
commands. Ambiguous state returns `recovery_required`; never blindly stamp or replace the original recovery point with a
partial-state backup. External summaries record execution and backup information.

Restoration is separate, explicit, and performed with writes stopped. Restore the database, required files, and matching
program before verification. Application rollback is not database rollback. Manual mode does not claim verified recovery;
skip cannot promise recovery of original data.
