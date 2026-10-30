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

`powercontext server db-migrate status/plan/apply/verify` default to `--bundle server` and use the configured persistent SQLite, embedded seekDB, or OceanBase MySQL database. Explicit environment files are authoritative; arbitrary bundle paths are not accepted.

The full Server framework registers two installed-release sources for the PowerContext 1.4.1 target (stored compatibility IDs shown below):

- **1.3.0:** `s1300 → s1400 → s1401 → s1402 → v1_4_1_004 → v1_4_1_005 → v1_4_1_006`.
- **1.4.0:** `r1400 → s1400 → s1401 → s1402 → v1_4_1_004 → v1_4_1_005 → v1_4_1_006` through a separate frozen source bridge.

Application release numbers and schema revisions are distinct. The 1.4.0 release is not the `s1400` checkpoint:
its existing Candidate schema is current, but its Topic attempt storage still needs the registered bridge.
Both source paths preserve existing references and history and reach the same managed target. Their original
resource checksums remain independently verifiable, including after a later registered revision is appended.
Other releases and extra database objects are not inferred to be compatible; they need exact source registration
and acceptance before writes. Older Memory collection conversion is outside these paths.

Both sources register Code enabled or disabled on all three backends and a separate profile for unit-normalized L2
vectors. SQLite and OceanBase keep Code in a separate local cache. Embedded seekDB Code uses three registered tables;
maintenance preserves existing graph rows and creates the registered tables for a new Code-enabled database.
Native source and profile registrations must pass the installed-release gate on their actual backends.

Only successful schema, domain, projection, and task validation returns `state=ready`, `readiness_scope=server`, and `server_ready=true`. Publication also requires installed-artifact acceptance on all three backends. An implemented path is not evidence that a release passed acceptance; consult the installation guide and release gate for support status.

Stop all writers and drain queued/running 1.3.0 Dream tasks before upgrading: the target cannot execute the old prompt contract. Terminal task payloads and pending Processing counters are preserved; old worker leases are fenced. Registered capability additions retain the old automatic-processing choices instead of replacing ownership with the current configuration.

For the 1.3.0 path, old Candidate tables are retained as `pc_retained_dream_candidate_heads` and `pc_retained_dream_candidate_versions` for migration-copy verification. They and the backup are not live write targets; subsequent business changes are not compared against old snapshots. New historical Artifact provenance fields remain unknown, and existing Source references are preserved. Projection rebuilds and verification scan their tables; budget downtime and capacity for backups and retained copies.

The 1.4.0 path preserves its already-current Candidate heads, versions and payloads without running the 1.3
Candidate conversion. It creates the same two retained tables empty to share the target structure; those empty
tables do not represent fabricated 1.3 history. Its preflight validates the current Processing declaration, Dream
payload identities and pending intents. It preserves compatible current tasks rather than applying the 1.3-only
Dream drain rule, and fences old worker leases during the explicit maintenance window.

Revision `s1401` registers `pc_memory_artifact_archive` in the managed target schema on all three backends. It stores frozen Memory history for offline maintenance and has no public Artifact read path. Fresh databases start with an empty archive; repeat migration preserves its bytes, and runtime admission verifies its registered structure. This revision does not register additional historical source layouts.

Revision `s1402` registers portable restore receipts and preserves microsecond precision for the portable Artifact,
Profile, Topic publication, and tag timestamps on MySQL-compatible backends. These schema changes run through explicit
maintenance for existing managed storage. Runtime startup does not add the receipt table or alter timestamp columns.

Revision `v1_4_1_004` registers `pc_access_owner_recovery` for durable Artifact and Candidate ownership intents. Both installed-release source paths and vector profiles include this table. Existing managed databases require explicit maintenance; fresh databases create the registered empty table. Pending intents retain their original deployment, principal and idempotency identity across Server restarts. Earlier revision resources and checksums remain unchanged. Unregistered database layouts are rejected rather than adopted from a table name alone. This registration does not establish installed-release or external-Provider upgrade acceptance.

Revision `v1_4_1_005` adds nullable `pc_atomic_memory_current.description` metadata for text and vector profiles. Existing managed databases require explicit migration; startup does not alter this column. The additive revision leaves historical Artifact bytes unchanged and existing descriptions null until a new write or explicit projection rebuild copies authoritative metadata.

Revision `v1_4_1_006` registers `pc_artifact_source_locators`, an optional relation keyed by the existing Artifact Revision and Source-edge ordinal. Existing managed databases require explicit migration before Server startup. The migration preserves existing lineage rows, exact identities and historical payloads without rewriting them or backfilling locators. Atomic Memory embeddings remain unchanged; no re-embedding is required. See [Source citation locators](../workflows/sources.md) for locator behavior.

An interrupted [portable restore](portable-archive.md) keeps normal startup and workers blocked until its projections
are complete. Retry `powercontext archive restore <bundle> --yes` with the same validated bundle. Only this explicit
recovery path can open an incomplete projection set, after validating the registered layout and the configured vector
profile; it does not start workers. Recovery without a pending restore receipt still performs full readiness checks.

Ordinary Server startup checks readiness without repairing an existing database. Empty local persistent SQLite/seekDB storage initializes through the same revision chain; OceanBase requires an explicit maintenance job first. In-memory SQLite remains transient runtime storage. A changed schema or inconsistent projection requires maintenance, not restart-based repair.

Server identity initialization checks registered tables without issuing DDL on managed storage. Creating the initial
identity value and explicitly rotating a stopped clone's identity retain their existing transactional behavior.

Vector maintenance requires the exact historical embedding profile: profile ID, model, dimension, distance and normalization. It preserves historical embeddings and Topic retrieval shape without calling an embedding provider. The registered SQLite transition adds and backfills Atomic Memory's vec0 helper and transactional triggers; native backends retain the registered vector columns and HNSW parameters. `pc_server_storage_profile` stores the complete embedding configuration, not another revision or recovery journal. Generic storage readers validate this declaration, and Server startup uses the configured or injected model's actual profile. Missing managed helpers and profile changes are rejected before startup repair or writes.

Empty persistent SQLite/seekDB Server storage with vectors initializes through the selected profile of the same chain. Existing unmanaged storage requires explicit maintenance and an exact registered source layout. Changing configuration cannot bypass admission, enable vectors on no-vector managed storage, or implicitly add vector/Code objects. Enabling embedded Code on an already managed database without its tables requires a separately registered configuration transition; startup and the standalone Code CLI reject it. The catalog retains twelve combinations of the 1.3.0 source, three backends, vectors enabled or disabled, and Code enabled or disabled. Embedded seekDB requires pylibseekdb 1.4.0.post2 or newer with the recovered-transaction lock fix. Automatic CI and publication require all twelve configurations, including both embedded seekDB Code configurations; none are waived. Main workflow dispatch with `upgrade_selection=seekdb-code` runs a diagnostic subset and cannot authorize publication. The 1.4.0 bridge remains registered but is outside CI upgrade acceptance; passing this gate does not certify that path. Code cases build an index with each installed source release, preserve its cache files and embedded graph rows, and query the original fingerprint and symbols after the upgrade. Vector cases preserve nonempty historical Atomic and Topic embeddings, verify semantic reads and new writes through the installed target, reject profile drift, and compare fresh/upgraded storage. SQLite also tests missing-helper rejection and backup restoration. A successful no-vector case does not establish vector support; each configuration needs passing evidence for the actual target artifact before upgrade support can be claimed.

`--bundle artifacts` retains the partial Artifact prototype chain starting at `p0001`; `--bundle topic-memory-recovery` covers only registered Topic recovery layouts. Both report `server_ready=false`. Neither establishes full Server readiness, and their revisions cannot be stamped as full Server revisions.

MySQL-compatible schema reflection supports arithmetic CHECK expressions with binary `+`, `-`, `*`, `/`, `%`,
unary signs, and nested grouping, preserving arithmetic, comparison, and boolean precedence. Unsupported CHECK
operators/functions remain rejected; their errors identify the reflected table, named constraint, and unsupported
construct instead of misreporting balanced parentheses. Parsing an expression does not register a historical layout
or bypass migration/data-integrity checks.

The existing `server processing-migrate` delegates managed databases to `db-migrate --bundle processing`.
Databases without a registered revision retain their domain-only migration command.
Follow [Migrate Artifact processing state](artifact-processing-migration.md).
See [Deploy the Server](deploy-server.md) for service deployment.

`server atomic-memory-migrate` delegates managed databases and recognized complete 1.3.0 Server baselines to
`db-migrate --bundle server`, including its reviewed-plan, backup and recovery requirements. It preserves domain-only
conversion for unmanaged legacy Memory layouts. Decision files for that conversion cannot modify the registered Server
chain. The compatibility command returns the shared Server plan/result for managed storage; an already-ready apply is a
read-only no-op. See [Migrate to Atomic Memory](atomic-memory-migration.md).

## Migration names and software versions

The target release is **PowerContext 1.4.1**. Migration names use
`v<major>_<minor>_<patch>_<sequence>`, with a three-digit sequence within the release.
Sequence `000` identifies a registered historical baseline; upgrade steps start at `001`.

| Migration name | Software release | Step | Compatible stored revision | Meaning |
| --- | --- | --- | --- | --- |
| `v1_3_0_000` | 1.3.0 | Baseline | `s1300` | Released 1.3.0 Server storage |
| `v1_4_0_000` | 1.4.0 | Baseline | `r1400` | Released 1.4.0 Server storage |
| `v1_4_1_001` | 1.4.1 | 1 | `s1400` | Complete Server storage transition |
| `v1_4_1_002` | 1.4.1 | 2 | `s1401` | Offline Memory history archive |
| `v1_4_1_003` | 1.4.1 | 3 | `s1402` | Portable restore receipts and timestamp adaptation |
| `v1_4_1_004` | 1.4.1 | 4 | `v1_4_1_004` | Durable owner recovery intents |
| `v1_4_1_005` | 1.4.1 | 5 | `v1_4_1_005` | Nullable Atomic Memory description projection |
| `v1_4_1_006` | 1.4.1 | 6 | `v1_4_1_006` | Optional Source citation position locators |

Both registered source paths reach `v1_4_1_006`; only 1.3.0 is selected for CI upgrade acceptance:

```text
1.3.0: v1_3_0_000 -> v1_4_1_001 -> v1_4_1_002 -> v1_4_1_003 -> v1_4_1_004 -> v1_4_1_005 -> v1_4_1_006
1.4.0: v1_4_0_000 -> v1_4_1_001 -> v1_4_1_002 -> v1_4_1_003 -> v1_4_1_004 -> v1_4_1_005 -> v1_4_1_006
```

`status` and `plan` show `source_migration`, `target_migration` and `pending_migrations`
before the existing machine fields. `apply` and `verify` show `migration`. Each descriptor
contains `name`, `software_version`, `sequence` and the compatible `revision` ID. For example:

```json
{"revision":"v1_4_1_006","name":"v1_4_1_006","software_version":"1.4.1","sequence":6}
```

The software version describes the registered database state, not the currently running binary
or the source of an interrupted maintenance window. A release without storage changes may reuse
the same database revision. Names do not prove upgrade acceptance; the full release gate still applies.

The existing `s1300`, `r1400` and `s1400`–`s1402` IDs remain in version tables, plan IDs,
frozen resources and recovery evidence. Their release-based names are display aliases, not
additional executable revisions or accepted replacement IDs for stamping a database. This keeps
existing plans and original backups usable. Partial prototype bundles keep their own opaque IDs.

For a new Server migration, use the release-based name directly as its Alembic `revision` and
register its schema, domain steps and recovery checkpoints as usual. `down_revision` must reference
the actual preceding ID (`v1_4_1_006` when appending directly to the current head), even if that ID has
a display alias. The framework follows this registered chain, not lexical name ordering. Already
registered names and release assignments stay immutable; a version bump must not rename history.

## Initialization and maintenance ownership

The following inventory identifies the owners of schema changes and projection repair. "Managed" means that the
database contains `pc_schema_revision`, including an unknown revision. Passing a partial domain check does not admit
that database for Server use. Full profile admission checks the registered Server layout and domain state first.

| Entry point or helper | Managed storage | Unmanaged storage |
| --- | --- | --- |
| `SQLiteProfile.open`, `SeekDBProfile.open`, `OceanBaseProfile.open`; `schema.create_tables` | Full profile readiness checks precede use; selected tables must already exist | Retain selected-table creation and domain admission |
| Runtime composition: Skill distribution, tag families, Scope search, revision metadata and Artifact head columns | Skip compatibility DDL and backfills; the registered Server chain owns these objects | Retain the existing compatibility helpers |
| Runtime Atomic Memory authority/current indexes and SQLite FTS helpers | Validate current data; repair through `db-migrate --bundle atomic-projection` after layout admission | Retain domain initialization and offline projection repair |
| Runtime Experience/Skill searchable text and FTS indexes | The registered Server domain step builds and validates projections; startup does not rebuild them | Retain domain initialization and projection recovery |
| Runtime Topic Memory indexes, recovery tables and retrieval shape | Initialize with `validate_only=True`; partial Topic recovery rejects managed storage | Retain domain initialization; registered partial recovery covers only its declared layouts |
| Processing schema and ownership configuration | Validate at startup; change configuration through `db-migrate --bundle processing` | Retain domain bootstrap and `processing-migrate` |
| `ServerIdentityRepository.initialize` | Check the identity table without DDL; identity value creation and explicit rotation are ordinary transactions | Retain concurrent-safe identity table initialization |
| Offline Memory archive | The Server chain registers its structure at `s1401`; runtime admission checks it | Legacy offline Memory conversion owns its archive and conversion receipts |
| `atomic-memory-migrate` and `processing-migrate` | Delegate to the shared Server or registered maintenance plan | Retain their domain-only behavior; recognized historical Server storage must use the full upgrade first |
| Vector objects | The selected Server profile owns schema, helper backfill and exact configuration admission; changing embedding spaces requires a separate transition | A complete released vector layout can be explicitly migrated; standalone unmanaged SDK storage retains domain initialization |
| Embedded seekDB Code objects | The registered Code profile creates and validates the three tables and full-text index; Code startup never repairs missing managed objects | Recognize the complete released Code-enabled layout for explicit migration; retain standalone unmanaged Code bootstrap |
| Separate Code cache on SQLite/OceanBase | Preserve existing cache files; main database follows the shared chain | Code service owns local cache creation and indexing |

These helpers remain available to their owning runtime or maintenance operations; calling a low-level helper directly
does not replace profile admission, a reviewed maintenance plan, or backup and recovery requirements. Future changes to
managed objects belong in the registered revision/domain steps and validators, rather than another startup helper.
Read-only SQLite inspection and dedicated maintenance connections can inspect historical storage without business
initialization; successfully opening an inspection connection does not establish Server readiness.

Code's embedded seekDB store adds `pc_code_generations`, `pc_code_nodes`, `pc_code_edges` and `ix_code_search` to the
deployment database. Other Code deployments use a local SQLite cache. These are derived indexes owned by the Code
service; the frozen extension registers their schema for maintenance and full Server admission. A partial or changed
extension fails validation. Its existence does not permit implicit managed-schema expansion.
Code caches on SQLite/OceanBase do not change the main database layout.

## Processing configuration maintenance

`--bundle processing` registers an offline ownership-configuration operation on a complete, current Server database.
It does not advance `pc_schema_revision` or adopt an older schema. Complete the full Server upgrade first.
Use the deployment's new configuration and a new migration ID for each mode switch:

```bash
powercontext server db-migrate plan --bundle processing --env-file .env \
  --migration-id global-to-dedicated-1 --backup auto
powercontext server db-migrate apply --bundle processing --env-file .env \
  --migration-id global-to-dedicated-1 --backup auto --plan-id PLAN_ID \
  --maintenance-confirmed --yes
powercontext server db-migrate verify --bundle processing --env-file .env \
  --migration-id global-to-dedicated-1
```

The operation uses the same target lock, backup policies, backend connections, and stopped-writer declarations as
schema migration. OceanBase still requires one fixed maintenance host, `--evidence-dir`, and
`--lock-coordination single-host`; recovery requires the exact previous-run acknowledgment when reported.
Local service switching is not part of this operation. Stop all writers and automatic restarts before applying.

All mode-switch phases commit together. Failure before commit rolls back the ownership declaration and lease
invalidation together. Reopen the plan after an interruption and resume with the original configuration, migration ID,
backup policy, and evidence directory; the original maintenance window and recovery point are retained.
Successful retry does not invalidate leases again. Full Server validators run before and after the change.
An already matching configuration is a read-only no-op. Both Processing and Atomic Memory projection maintenance
validate the original migration evidence and recorded revision checksums before accepting work. They inspect the
stored embedding profile and reject vector storage before creating a plan, backup or maintenance journal, even
when embedding settings are omitted from the command configuration. Both Processing and Atomic Memory projection maintenance
validate the original migration evidence and recorded revision checksums before accepting work. They inspect the
stored embedding profile and reject vector storage before creating a plan, backup or maintenance journal, even
when embedding settings are omitted from the command configuration.

## Atomic Memory projection maintenance

`--bundle atomic-projection` repairs the current Atomic Memory rows and SQLite FTS helper on a current managed
Server database in the registered no-vector configuration, including Code with a separate cache on SQLite/OceanBase. It preserves the Server revision,
immutable content/history, lifecycle, formal tags, permissions and Source/Processing state.

```bash
powercontext server db-migrate plan --bundle atomic-projection --env-file .env --backup auto
powercontext server db-migrate apply --bundle atomic-projection --env-file .env \
  --backup auto --plan-id PLAN_ID --maintenance-confirmed --yes
powercontext server db-migrate verify --bundle atomic-projection --env-file .env
```

The operation shares Processing maintenance's target lock, backup, stopped-writer declarations and durable recovery
transaction. Plan and verify are read-only. Apply repairs only derived rows and commits after full Server validation;
invalid authority or an unregistered layout is rejected. A ready projection is a read-only no-op. Interruption before
commit rolls back the repair; resume from a new plan with the original artifact, configuration, backup policy and
evidence directory to reuse its recovery point. Finish any incomplete maintenance operation before starting another.
OceanBase requires the same fixed-host coordination and previous-run acknowledgment as other maintenance.

Inspection and repair scan the relevant Atomic history, heads, tags, lineage and current projection. Keyset batches
bound row buffering; this is an offline whole-projection operation. It does not register vector-profile changes or
embedded seekDB Code configurations. The compatibility command `server atomic-memory-rebuild-projection` delegates managed
databases to this bundle and supports `--action plan/apply/verify` with the same confirmation and backup options.

## Topic Memory recovery bundle

Select `--bundle topic-memory-recovery` for the versioned `powercontext.topic-memory-recovery.v1` conversion. It recognizes
the pre-recovery budget layout and the attempts-table layout from implementation #1966, preserves consumed allowance,
and retains terminal blocks and accepted receipts. It verifies only its registered Topic objects in an existing Server
database, including shared processing-lease fencing; other business tables remain untouched. Its output still reports `server_ready=false`.

This partial bundle is restricted to unmanaged storage. Any database containing `pc_schema_revision` is rejected before
backup or writes, including an unknown revision. Use `--bundle server` or a registered managed maintenance operation.

Stop all APIs, Workers, schedulers and SDK writers first, and suspend automatic restarts. Review and apply one plan:

```bash
powercontext server db-migrate plan --bundle topic-memory-recovery --env-file deployment.env --backup auto
powercontext server db-migrate apply --bundle topic-memory-recovery --env-file deployment.env --backup auto --plan-id PLAN_ID --yes --maintenance-confirmed
powercontext server db-migrate verify --bundle topic-memory-recovery --env-file deployment.env
```

SQLite uses a whole-database native backup. OceanBase also requires `--evidence-dir` and `--lock-coordination single-host`;
choose a supported backup policy using the declarations below. Keep the recovery point and evidence directory after an
interruption and resume with the original options. SQLite rolls back an incomplete conversion. MySQL-compatible DDL
resumes only a recognized prefix recorded in the external evidence; previous remote-run acknowledgment rules apply.

Current-attempt metadata moves into the budget; only terminal blocks and receipts remain in `pc_topic_memory_work_blocks`.
Existing IDs, acceptance timestamps/generations and costs are preserved. Old attempts lacked trustworthy range provenance:
their original number becomes `legacy_source_through`, while exact `source_through` is null. Cumulative
`budget_source_through` is copied only from the same still-present allowance, otherwise null. No current allowance is
used to reinterpret an older receipt. The old attempts table is removed only after verification; the recovery point
retains its successful/superseded rows. Unknown or inconsistent layouts are rejected before writes. Normal startup
requires the target format; it does not upgrade an existing database. Exact block lookup and accepted-retry replay have
no automatic expiry.

Empty recovery initialization creates blocks before budgets. SQLite commits both tables atomically. Only a frozen empty
block-table prefix without other business state can resume initialization. The known Access bootstrap and independent
Code graph tables may already exist and remain untouched; other business tables or receipts are rejected before writes,
except for the registered pristine SQLite Atomic maintenance output described below.
Missing budget storage is never silently recreated beside retained recovery evidence.

On SQLite, `server atomic-memory-migrate --action apply --maintenance-confirmed` can initialize a fresh database before
normal Server startup. Its registered output contains the exact Atomic current table, FTS5 tables/triggers and Memory
archive, with no Atomic rows or archived evidence. Normal startup can complete this initialization atomically, and
`--bundle topic-memory-recovery` also supports `plan/apply/verify` for this state. The offline path backs up the existing
database before adding Topic tables; it does not treat the maintenance output as an empty database. Missing or altered
objects, unexpected business tables and retained Atomic evidence are rejected before writes. This continuation is
specific to the registered SQLite layout and does not register additional OceanBase or seekDB baselines.

The conversion revokes every old processing lease once by advancing its generation, expiring it in UTC, and assigning
the maintenance-window holder. Repeating an interrupted conversion does not advance that holder again. This follows
the existing processing-maintenance fence transition and requires all workflows sharing those leases to be stopped.
Known attempt owners remain in budgets; unknown finish times stay null. Inspection can finalize stopped work even
when processing remains disabled and no new Supervisor starts. A pre-upgrade Worker cannot regain publication rights.

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

Configuration binding includes the target's canonical Processing ownership mode, capabilities and declared automatic
bindings. Changing those declarations requires a new plan; an accepted plan cannot be applied under a different
Processing declaration. Plan binding alone does not validate or migrate historical Processing state; the full Server
bundle separately validates the stored declaration and pending intents through its registered domain steps.

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
recognized engine layout returns `unsupported_target`. An unconfigured database name whose old `test` database still
holds PowerContext tables returns `explicit_database_required` instead of selecting either database implicitly; select
the name explicitly and rerun the command. For an existing engine missing the selected database, `status` and `verify`
return `database_missing` with its name and provisioning guidance. `plan` instead returns an `uninitialized` plan bound
to the selected database without creating it; after the operator accepts that plan, `apply` may create the database and
initialize its registered schema. Denied access returns `database_access_denied`.
Writable seekDB maintenance that cannot create its configured database
returns `database_creation_required`, and a permission denial that leaves existence unproven returns
`database_access_denied`; both keep the administrator statement and provisioning guidance in the payload. Opening a
valid existing engine may write native logs and other engine housekeeping files; read-only migration commands do not
perform business DDL or initialize tables.

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

The installed-release SQLite acceptance suite restores the generated automatic backup to a separate database.
It compares the complete source schema and row digests, reads historical records with the installed source Server,
then upgrades the restored copy and verifies target reads, retrieval and new writes. Code-enabled cases preserve
their separate immutable caches as well. This exercise is specific to SQLite; it does not verify seekDB or
OceanBase backup restoration. Keep the original recovery point until your own restored deployment is verified.

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

Full Server `db-migrate status`, `plan`, `apply`, and `verify` also inspect the selected evidence directory for
unfinished Processing configuration and Atomic Memory projection maintenance on all three backends. They return
`recovery_required` until the original domain operation completes, even if the schema already matches the target.
Resume that operation with its original configuration and recovery evidence; full migration cannot bypass it.

Local startup probes an existing SQLite/embedded seekDB maintenance lock without creating or changing its file.
An observed active lock blocks new runtimes with `migration_locked`. SQLite SDK and background admission also
check the default adjacent migration evidence directory: unfinished maintenance, changed recorded revision checksums,
and missing required retained tables block startup. A current-layout database without that journal is still accepted
when its schema and domain checks pass; it has no recorded historical retention obligation. If maintenance uses a
custom evidence directory, run explicit verification with that directory before starting services. Remote OceanBase
coordinator locks are not observable from arbitrary service hosts. Deployment orchestration must keep all writers
stopped and prevent restarts throughout maintenance; these checks do not fence existing clients or future maintenance.

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

For `--bundle server`, `--manage-service` binds the owned local registration and target executable to the reviewed plan.
Use the registered environment file and data directory; a different effective Server configuration is rejected before
stopping the service. Stop incoming traffic and drain work first. Apply suppresses automatic restarts, stops the local
service, takes the migration lock, reinspects, applies the backup policy, migrates and verifies, then switches the launch
definition to the accepted executable. It starts a previously running service and checks readiness; a previously stopped
service stays stopped. Migration failure keeps the old registration stopped and protected against automatic activation.

Database verification and service startup are reported separately. `service_start_failed` with `database_state=ready`
means the schema migration succeeded; fix the startup problem and use `powercontext service start`. If execution stopped
after database verification but before switching the registration, review and apply a new `--manage-service` plan. It can
finish the service transition when the database is already ready, without rerunning completed revisions.

`--manage-service` covers only the accepted local service. It cannot replace `--maintenance-confirmed` for other writers
and shared nodes. The partial Artifact and Topic recovery bundles reject service management when changes are needed;
an already-ready no-op does not touch the service. They cannot establish complete Server readiness or switch a business
service. Controller tests use a simulated native manager with a real SQLite migration. The native lifecycle suite also
exercises the installed CLI with isolated service registrations: it upgrades a stopped frozen SQLite baseline, checks
that it stays stopped until an explicit start, and completes service maintenance for an already-ready running database.
Each operating system requires its own native run. A frozen baseline fixture is distinct from upgrading a database
populated by an installed historical release.

The suite also installs the actual 1.3.0 wheel in a separate environment, starts it through the native manager and
populates a Scope through HTTP. Both running and stopped service cases use the public maintenance command, verify the
backup and retained data, and confirm that registration switches to the target executable while preserving the intended
running state. The CI setup verifies the source wheel's SHA-256 and supplies
`POWERCONTEXT_NATIVE_UPGRADE_SOURCE_PYTHON`; local native runs need the same installed-source interpreter. These cases
cover SQLite service switching. Backend crash recovery and the full installed-release domain suite remain separate
acceptance requirements.

Failure cases cover unavailable backup storage, an actual port conflict when the target service starts, and injected
`ENOSPC` when replacing its registration file. Startup/switch failures retain the stopped service and recovery record
after database verification; an explicit service start completes recovery without another migration apply.

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
receipts. The partial Artifact bundle does not cover these complete business checks. The full Server path checks its
registered 1.3.0 Dream formats and rejects queued/running old tasks before migration; terminal payloads remain intact.
For 1.4.0, it validates current task identities and formats and preserves compatible queued tasks.

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

The separate Linux OceanBase engine-recovery test requires
`POWERCONTEXT_TEST_MIGRATION_OCEANBASE_ENGINE_CRASH_ID` with the exact disposable container ID and
`POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL` with its matching loopback port and dedicated `pc_migration_probe_*` database.
It sends `SIGKILL` to observer after registered DDL, then restarts observer in the same directory and verifies the original
recovery record, required acknowledgement, historical bytes, final revision and repeated apply. It does not restart the
slim image's bootstrap script, which unfolds a snapshot and truncates logs. CI requires one executed test and zero skips.
The test uses frozen historical resources and an operator backup declaration; it does not establish installed-release
acceptance or verified backup restoration.

seekdb/OceanBase DDL may commit implicitly, so one Python transaction cannot make the entire upgrade atomic. Recovery
recognizes only the registered sequence: `p0001` creates the four frozen tables and two indexes; `p0002` adds the two
nullable `MEDIUMBLOB` citation columns; `p0003` removes the tag-family CHECK. Actual schema, data checks, and persisted
maintenance evidence must establish the known completed prefix before continuing. `p0004` adds exact-revision Artifact
handles. Alembic advances the version only
after the revision's postconditions pass. Unknown objects, layouts, or partial transitions block execution instead of
being optimistically stamped.

MySQL schema steps are registered in frozen layout fragments selected by the `mysql_layouts` list in `manifest.json`.
The fragments together must cover exactly the Alembic chain, without duplicate declarations. New revisions append
immutable fragments and declare their files in `revision_resources`; they do not edit previously released fragments.
Optional checkpoints compare the accumulated steps with independently
frozen complete layouts. Reflection, next-revision prefix recovery, and version-table validation use that same registered
chain; adding a revision does not require changing a second version list in the recognizer. These schema registrations
do not replace domain conversions, compatibility checks, or full Server readiness.

New MySQL execution wrappers are also registered in `manifest.json`, using a `mysql_wrappers` object that maps a
revision to its frozen Python resource. List that resource in the revision's `revision_resources` dependencies.
The wrapper declares the same `revision` and `down_revision` as the common chain and calls
`context.config.attributes["execute_revision"](revision)`. Historical execution wrappers cannot be overridden.
The executor validates complete, linear wrapper coverage before opening a maintenance target; missing or divergent
execution history is an invalid bundle rather than a failure discovered after earlier revisions have committed.
Wrapper bytes participate in both the reviewed bundle and the revision's execution checksum.

The reviewed plan binds the registration through the complete bundle checksum. Active maintenance requires the same
bundle for retry. Released revision resources and their recorded checksums remain immutable.
Parameter-free DDL uses statement-scoped driver execution options so CHECK remainder operators and percent-containing
SQL literals reach the backend unchanged.

Repositories run after schema checks. Both full Server source chains register exact per-statement schema transitions and recovery prefixes through `mysql_state_layouts`. Domain steps attach to the shared revision and use its connection, lock, and recovery point; verification precedes version advancement. Read-only live checks are separate from migration-copy validation. Vector configurations still require separate registration.

Online readiness reads Atomic Memory authority and projections from the same snapshot without locking business head rows. Offline projection repair retains its locked state comparison and write-side concurrency protection.

The 1.4.0 bridge is assembled from immutable shared resources and its `server_sources/1.4.0` overlay. Source
selection uses the complete unversioned inventory or the original lineage in the main maintenance evidence; it
neither adds another version table nor rewrites a completed backup's source revision. Repeated maintenance and
later registered upgrades retain the same lineage. The canonical target remains the ordinary runtime admission
contract. New sources need their own exact baseline and bridge; adding a target revision alone does not register
all intervening application releases.

One version table offers no generic run-ID resume. After interruption, inspect actual schema/data through read-only
commands. Ambiguous state returns `recovery_required`; never blindly stamp or replace the original recovery point with a
partial-state backup. External summaries record execution and backup information before DDL. A retry preserves the
original maintenance window and recovery point. Manual/skip policies can be explicitly reconfirmed when recovery
conditions allow, but they do not bypass structural verification or replace the required progress evidence.

Restoration is separate, explicit, and performed with writes stopped. Restore the database, required files, and matching
program before verification. Application rollback is not database rollback. Manual mode does not claim verified recovery;
skip cannot promise recovery of original data.

## Legacy Scope search startup recovery

The existing Scope discovery startup migration inspects the actual search-column nullability in MySQL-compatible
storage. If an earlier startup stopped after adding nullable columns or backfilling them, the next startup fills any
remaining missing values from the original text and completes `NOT NULL` constraints. Columns already marked
`NOT NULL` do not receive another `MODIFY COLUMN`. Original Scope titles, summaries, external references, and binding
identifiers are preserved. This compatibility repair remains for unmanaged profiles. Managed Server storage uses the
registered migration chain and read-only startup admission; its startup never invokes this repair to upgrade the schema.
