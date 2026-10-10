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

`powercontext server db-migrate status/plan/apply/verify` use the configured persistent SQLite, embedded seekdb, or
OceanBase MySQL-mode database. They do not accept arbitrary test-bundle paths. Explicit environment files are authoritative
for maintenance commands; use the deployment's environment file rather than relying on temporary shell overrides.
Frozen resources ship in the wheel for Phase A acceptance of four Artifact tables: `pc_artifacts`, `pc_artifact_heads`,
`pc_artifact_candidate_versions`, and `pc_artifact_tags`. Only empty targets and registered historical layouts are accepted.
A complete Server database containing unmanaged objects is rejected.

`ready` means this migration bundle passed its schema and data checks. Output explicitly includes
`readiness_scope=registered_bundle` and `server_ready=false`: it does not establish complete Server, index, legacy-task, or
cluster readiness. Do not substitute it for a production upgrade or initialization of the complete business database.
Full startup gates and complete historical baselines remain outside this bundle. Isolated four-table acceptance passed
through an ODP endpoint on OceanBase AI Database 4.6.3, covering coordinator exclusion, acknowledged recovery after
committed DDL, data preservation, and repeat execution. This does not establish complete Server readiness, automatic
detection of unfinished remote DDL after a crash, or full Fork restoration acceptance.

The existing `server processing-migrate` remains available. Follow
[Migrate Artifact processing state](artifact-processing-migration.md); it does not yet forward to the unified entry point.
See [Deploy the Server](deploy-server.md) for service deployment.

## Read-only inspection and one confirmation

The `deployment.env` examples below use SQLite. OceanBase commands also require the evidence-directory and fixed-host
coordination options shown in the next section. Inspect the target or preview a plan; these commands never create a missing database, parent
directory, or control table:

```bash
powercontext server db-migrate status --env-file deployment.env
powercontext server db-migrate plan --env-file deployment.env --backup auto
powercontext server db-migrate verify --env-file deployment.env
```

Plans bind target identity, source/target revisions, actual schema, frozen resources, configuration digests, backup policy,
service scope, and migration coordination. Diagnostics do not print configuration or credential values. Expected failures return stable JSON
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

## Target configuration and external evidence

Select the database using the existing [Server configuration](configuration.md). The maintenance connection does not
initialize business tables from current application metadata. OceanBase must already have a user database in a
MySQL-mode user tenant; these commands do not create a cluster, tenant, or database.

OceanBase maintenance must be able to read the tenant ID, cluster ID, and tenant creation metadata through
`effective_tenant_id()`, `oceanbase.GV$OB_PARAMETERS`, and `oceanbase.DBA_OB_TENANTS`. Unavailable permissions or incomplete
identity return `target_identity_unavailable`; a proxy hostname is not used as a substitute database identity.

seekdb inspection does not start an engine for a missing path or empty directory. A nonempty directory without a
recognized engine layout returns `unsupported_target`. Opening a valid existing engine may write native logs and other
engine housekeeping files; read-only migration commands do not perform business DDL or initialize tables.

`--evidence-dir PATH` selects persistent storage outside the target database for maintenance progress and recovery
references. Use the same directory for `status`, `plan`, `apply`, `verify`, and retries. Keep it available after a process,
container, or migration Job exits. Give each database its own directory and restrict access to the migration operator.

| Backend | Evidence directory |
| --- | --- |
| SQLite | Defaults to `<database-file>.pc-migration-state` beside the canonical target file; override with `--evidence-dir` |
| Embedded seekdb | Defaults to `<directory-name>.pc-migration-state` beside the canonical engine directory; override with `--evidence-dir` |
| OceanBase | All four commands require explicit `--evidence-dir` and `--lock-coordination single-host`; every migration Job for the same database uses this durable directory on one fixed maintenance host |

OceanBase uses an OS-backed file lock in `<evidence-dir>/<database-id>`, rather than a database named lock. Pin all
migration Jobs, retries, and inspections to one fixed maintenance host and the same canonical persistent directory.
Keep the hostname stable across replacement containers or Jobs as well; a different hostname conflicts with the durable host binding.
Several business Servers may share the database; migration commands remain on this one host. A shared filesystem,
replicated evidence directory, or identical directory name on another host does not establish supported cross-host
locking. Do not run migration Jobs concurrently on different hosts.

The plan's `coordination` records `mode=single-host`, the host name, and the canonical database-specific directory.
Changing host or directory changes the plan and requires review again. The explicit option selects this deployment
contract; it does not replace stopped-write responsibilities. Interactive `apply` includes it in the existing single
confirmation. Missing evidence returns `evidence_required`; with evidence supplied but no coordination option,
`coordination_required` is returned before contacting OceanBase.

OceanBase coordination works with the configured direct or ODP connection without using physical session IDs,
`PROCESS`, or process-list visibility. A durable run receipt stays outside the database alongside recovery evidence;
no extra control table or special Alembic revision row is added. Acquiring a released local lock does not prove previous
remote execution or background DDL has ended. PC does not automatically verify remote quiescence after interruption.

Keep `<evidence-dir>/coordinator.json`, which binds the maintenance host, its `coordinator.lock`, and the database-specific `migration.lock`,
`active-run.json`, and recovery summaries. Unsupported OS locking returns `migration_lock_unsupported`; contention
returns `migration_locked`. An unresolved previous run, damaged run evidence, or conflicting host binding returns
`recovery_required`; lock-file or maintenance-connection loss returns `migration_lock_lost`. None of these errors
authorizes deleting the coordination files or continuing on another host.

Only a normal return followed by successful primary-connection closure clears `active-run.json`. Execution interruption
or connection-close failure, including Ctrl+C after a known DDL completed, retains it. A read-only plan exposes its token as
`coordination.pending_run_id`, binds it to `plan_id`, and reports `state=recovery_required`. A DBA must first confirm
all execution and background DDL from that run have ended. Then review a new plan and explicitly supply its exact token
to `apply --acknowledge-previous-run TOKEN`. This is a manual declaration, not a PC verification, automatic approval,
schema bypass, or generic run-ID resume. `--yes` and the interactive confirmation never supply this token for you.
Missing or mismatched acknowledgement returns `recovery_required`; an acknowledgement without a pending run returns
`stale_ack`. The option is supported only by OceanBase `apply`; SQLite and seekdb reject it.

For seekdb, configure its path in `seekdb.env` and use the same commands. Before this interactive manual-backup example,
complete your backup and stop every writer:

```bash
powercontext server db-migrate status --env-file seekdb.env
powercontext server db-migrate apply --env-file seekdb.env --backup manual
powercontext server db-migrate verify --env-file seekdb.env
```

For multiple Servers sharing OceanBase, make a manual backup and stop all writers through deployment tooling. Run every
command below on the fixed maintenance host, using its one persistent directory and the same scope when planning and applying:

```bash
powercontext server db-migrate status --env-file oceanbase.env \
  --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
powercontext server db-migrate plan --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
powercontext server db-migrate apply --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host \
  --plan-id PLAN_ID --backup-confirmed --maintenance-confirmed --yes
powercontext server db-migrate verify --env-file oceanbase.env \
  --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
```

Replace `PLAN_ID` with the returned plan ID. Do not move evidence to an empty directory to bypass a recovery error.
Missing or inconsistent evidence for an interrupted migration returns `recovery_required`; selecting manual backup or
accepting no-backup risk does not authorize an unknown schema or an unproven DDL transition.

After an interrupted OceanBase run, keep every writer stopped and retain the same host and directory. Have a DBA confirm
the previous remote execution and background DDL have ended, then inspect and review the recovery plan:

```bash
powercontext server db-migrate plan --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
powercontext server db-migrate apply --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host \
  --plan-id NEW_PLAN_ID --acknowledge-previous-run PENDING_RUN_ID \
  --backup-confirmed --maintenance-confirmed --yes
powercontext server db-migrate verify --env-file oceanbase.env \
  --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
```

Use the latest plan's `plan_id` and `coordination.pending_run_id`. Reconfirm the existing manual backup; do not replace
the original recovery point with a backup of partial state. Normal upgrades still use one confirmation. Recovery requires
the additional explicit DBA declaration; PC never invents it when showing the plan.

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
an independent file, including committed WAL, and checks integrity. seekdb and OceanBase executors reuse
`ForkBackupProvider`, preferring native `FORK DATABASE`. The current executor rejects automatic `FORK TABLE`: enabling
it requires tracking same-database backup objects separately and accepting every affected dependency, cross-table
consistency, and restoration path. Until the actual engine and
schema have accepted recovery evidence, an upgrade requiring backup reports `backup_unsupported` for `--backup auto`
before migration writes. Manual backup and explicitly accepted no-backup risk remain available without Fork support.
PC neither declares acceptance on the operator's behalf nor automatically replaces unavailable Fork with physical backup
or another policy.

| Product | First `FORK TABLE` version | First `FORK DATABASE` version |
| --- | --- | --- |
| OceanBase AI Database | V4.6.2 | V4.6.2 |
| seekdb | V1.1.0 | V1.2.0 |

Version eligibility is only the start: capability checks also consider actual product, tenant mode, permissions, objects,
subsequent DDL limits, and restoration. Several table Forks do not automatically constitute one database snapshot. Fork
shares underlying storage and does not protect against disk failure. A simple-table Fork probe does not establish
recovery of this four-table bundle. Full Fork restoration acceptance remains outstanding for OceanBase; an implemented
provider is not a production-upgrade guarantee.

Retain old business tables, Fork recovery points, and SQLite backups during this upgrade. Migration success, normal
startup, and retry do not delete them. A later independent revision removes old tables; explicit maintenance removes
recovery points after checking dependencies, retention windows, and recovery obligations.

In this bundle, SQLite revision `p0003` recreates the tag table and retains its old layout and data in
`pc_retained_p0002_artifact_tags`. seekdb/OceanBase `p0003` changes the CHECK constraint in place without replacing or
deleting the business table, so it does not create the SQLite history copy. Neither path automatically deletes backup
recovery points.

## Stopping, starting, and upgrading services

Personal services provide these commands while retaining registration and settings:

```bash
powercontext service stop
powercontext service start
powercontext service restart
```

After a manual `service stop`, `service install` from the new environment can update the stopped registration without
starting the old executable. Pass the existing `--env-file` when configured. The service remains stopped with automatic
activation suppressed until an explicit `service start`; an unverified migration continues to block registration updates.

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
restarts. OceanBase replacement migration Jobs stay on the fixed maintenance host and use the same persistent
`--evidence-dir` and `--lock-coordination single-host`; the original recovery reference and active-run evidence must
survive Job replacement. Coordinate subsequent node upgrades; each node passes its schema, task, and capability checks
before traffic returns. A migration lock excludes other migrators, not unknown external clients.

Release authors provide affected objects, execution mode, binary/schema compatibility, API changes, and task formats.
Breaking public API replacements require deprecation, alternatives, and removal plans. Same-package internal entry points
can change together with callers. Retained API adapters can translate requests/responses into one current implementation
without every handler supporting two table layouts.

Legacy tasks require inspection in the read-only plan, after stopped-write locking, after migration, and before Worker
startup. Unknown formats or unobservable sources must not be reported as an empty queue. Use declared compatible
consumption, idempotent conversion, or bounded draining. Preserve task IDs, idempotency keys, Scope, retries, and domain
receipts. The Phase A bundle does not yet cover these complete business checks.

## Storage layers and recovery boundaries

Maintenance reuses configuration and uses a dedicated connection for Alembic. Frozen historical SQL does not depend on
current Repositories or application metadata. seekdb holds its canonical engine-directory lock before startup until
engine shutdown. OceanBase verifies the OS lock, host binding, durable run receipt, and maintenance connection before
and after each DDL. On normal completion it closes the primary maintenance connection before clearing its receipt and
releasing the file lock; the engine closes afterward. Execution interruption or connection-close failure retains the receipt, including a connection-close
failure. Later execution requires the exact previous-run token and a DBA's declaration that remote work has ended.
PC does not infer completion from local process exit or lock availability. Replacing the lock file, losing the connection,
or finding an unacknowledged previous run blocks continuation.
Unavailable native file locking is rejected, including runtime fallback to a soft lock; it does not reconnect or silently
weaken coordination and continue. These locks do not stop business writers.

seekdb/OceanBase DDL may commit implicitly, so one Python transaction cannot make the entire upgrade atomic. Recovery
recognizes only the registered sequence: `p0001` creates the four frozen tables and two indexes; `p0002` adds the two
nullable `MEDIUMBLOB` citation columns; `p0003` removes the tag-family CHECK. Actual schema, data checks, and persisted
maintenance evidence must establish the known completed prefix before continuing. Alembic advances the version only
after the revision's postconditions pass. Unknown objects, layouts, or partial transitions block execution instead of
being optimistically stamped.

Repositories run after schema checks. Versioned resources and controlled rebuilds for complete business full-text/vector
indexes, plus legacy-task readiness, remain outside this acceptance bundle.

One version table offers no generic run-ID resume. After interruption, inspect actual schema/data through read-only
commands. Ambiguous state returns `recovery_required`; never blindly stamp or replace the original recovery point with a
partial-state backup. External summaries record execution and backup information before DDL. A retry preserves the
original maintenance window and recovery point. Manual/skip policies can be explicitly reconfirmed when recovery
conditions allow, but they do not bypass structural verification or replace the required progress evidence.

Restoration is separate, explicit, and performed with writes stopped. Restore the database, required files, and matching
program before verification. Application rollback is not database rollback. Manual mode does not claim verified recovery;
skip cannot promise recovery of original data.
