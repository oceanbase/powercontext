# Dream contribution to unified migrations

This directory is the Dream-owned input to RFC #1771 and PR #1772. It is not a second runner or an upgrade command.
`manifest.json` declares objects, API replacement, legacy task handling, retained snapshots and registration prerequisites.
`schema.json` freezes the Candidate SQL and historical DreamRecord format, with source commit provenance. It is a component
snapshot, not proof that a complete Server database belongs to a supported historical release.

The framework must register this contribution against a complete, accepted baseline and its actual revision head. Do not
append it to #1772's four-table acceptance chain and claim complete Server readiness. Release and minimum Client versions
must be bound before registration. No generic migration table is added; revision tracking belongs to the shared
`pc_schema_revision` table. Production registration remains disabled in the manifest.

The framework's Alembic revision calls `upgrade_dream_storage(op.get_bind(), target_manifest=target_manifest)` from the
frozen `upgrade.py` after baseline
recognition, stopped-write checks, locking, backup policy and maintenance connection setup. Bundle this module, the SQL/
task schema and manifest in the framework's immutable checksummed resources. Released resources must not import current
repositories, runtime models or application metadata. This transformation neither commits nor stamps a revision, and
does not implement its own lock, backup, retry, cleanup or recovery protocol.

For SQLite, the caller disables foreign keys before starting its explicit rebuild transaction. The transformation keeps
copies without live foreign keys, renames the live tables so Profile references follow them, rebuilds Candidate constraints,
adds the deduplication column/index and fairness counter, converts the frozen processing manifest, compares copied fields
and checks live foreign keys. The caller
owns commit/rollback and restores connection settings. For seekdb/OceanBase MySQL mode, the supplied DDL alters constraints
and retains snapshots; it requires real backend DDL, locking and recovery acceptance in #1772. An interrupted remote
transformation is not automatically resumable, and this module must not be called again to guess recovery steps.

`validate_dream_tasks(connection)` recognizes the two released operations before DDL, including payload shape, semantic
selection rules, identity, principal and Candidate references. Queued/running runs block planning and execution before any
writes with `dream_tasks_require_drain`. Stop new admissions and finish them with the old runtime before stopping all
writers. This includes unstarted runs, retries, running leases and expired deadlines; migration never changes their
prompt/model identity, budget, deadline or status. Terminal payloads are not rewritten, and pending Candidates need not
be approved before migration. The full framework owns
other queues, delayed/retry/dead-letter work and leases. Unknown or unobservable task formats cannot be treated as no work.

`verify_dream_storage(connection)` compares the retained Candidate fields against the newly copied rows and validates the
upgrade's postconditions while writers remain stopped. It is migration-only: after normal reviews resume, live candidates
legitimately diverge from retained snapshots. Runtime readiness must use live constraints and current task recognizers,
not compare active data with historical copies or restrict new runs to legacy operations.

The Dream profile preflight runs before business initialization and does not upgrade existing storage. Until #1772's full
Server readiness integration is available, it explicitly rejects versioned databases instead of allowing the partial
bundle to grow into a Server through `create_all`. Existing development databases with the complete unversioned Dream
shape and fresh development initialization retain their ordinary domain behavior; they are not production migration
acceptance evidence. Enabling the production upgrade requires SQLite, seekdb and OceanBase acceptance, packaging checks,
full baseline registration, task validation and startup readiness together.

The framework supplies the target deployment's canonical processing manifest as a required input bound to the maintenance
plan. It must reflect explicit `artifact_processing_families` exactly; only deployments without an explicit declaration
use the target release's inferred defaults. The migration process's incidental runtime configuration is not this input.
`validate_dream_processing_manifest(connection, target_manifest=target_manifest)` validates it during planning, and
`upgrade_dream_storage` repeats validation under the maintenance lock before any DDL or data writes.

The frozen transformation preserves ownership mode and historical automatic bindings, adds the canonical Handoff/Prompt
bindings and adopts the declared capability set. Existing capabilities must remain; only Handoff/Prompt capabilities may
be added. Skill in the old record does not imply either addition. Removing existing capabilities or changing ownership
requires separate maintenance. An incompatible or missing target manifest is rejected; incompatible content raises
`incompatible_target_processing_manifest`. An unknown stored manifest or incomplete processing migration raises
`unknown_processing_manifest`. The frozen module validates plain manifest data without importing live runtime models.
Startup only checks the result; it does not repair or extend manifests. The framework must ensure all new hosts use the
deployment configuration bound to the plan, then verify compatibility before advancing the shared revision.
