# Database migration acceptance bundle

The bundle validates Phase A of [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771), tracked by [#1756](https://github.com/oceanbase/powercontext/issues/1756). Use disposable databases. Its `ready` result covers only four registered Artifact tables, not complete Server schemas, task formats, search projections or cluster readiness.

The installed copy in `src/powercontext/builtin/persistence/migrations/resources/` is included in the wheel. The public `server db-migrate` command reads Server configuration without opening a business runtime; full unregistered databases are rejected. Ordinary Server startup and the existing `processing-migrate` flow are not yet transferred to this bundle.

## Frozen historical inputs

The JSON resources contain original compiled definitions, indexes, constraints and MySQL collations for `pc_artifacts`, `pc_artifact_heads`, `pc_artifact_candidate_versions` and `pc_artifact_tags`. Revisions never import current application metadata.

| Input | Source | Purpose |
| --- | --- | --- |
| `schemas/pre_dream.json` | [495cc93d67387879382c98706f4975f638faec15](https://github.com/oceanbase/powercontext/commit/495cc93d67387879382c98706f4975f638faec15) | Exercise both change classes with existing data |
| `schemas/v1_1_0.json` | [v1.1.0 / 86e687f13fec62151ee5657a90733586fd5158dc](https://github.com/oceanbase/powercontext/commit/86e687f13fec62151ee5657a90733586fd5158dc) | Recognize released table shapes |

Each resource records source blobs and compiler versions. Definitions were captured from the original commit's `tables.py` and `limits.py`, compiling `CreateTable` and named `CreateIndex` for SQLite and MySQL. These are subsets, not full released database images.

The single chain contains:

1. `p0001`: create the frozen pre-Dream structures.
2. `p0002`: add nullable `memory_citations` to Artifact and candidate revisions.
3. `p0003`: remove `ck_pc_artifact_tags_family`; SQLite rebuilds the table, preserves rows, constraints, indexes and registered triggers, and retains its prior layout and rows as `pc_retained_p0002_artifact_tags`. MySQL uses explicit constraint DDL.

The manifest registers exact business fingerprints, alternate released shapes, retained object shapes and their introduction revisions. Unregistered objects, premature retained objects, residue and unknown versions are rejected. There is no optimistic `stamp head` fallback.

Plans bind the canonical target, actual schema, immutable resource snapshot, configuration digest and maintenance choices. Alembic executes the reviewed snapshot even if installed resources change; changes detected during backup stop before migration DDL.

## Explicit maintenance

Repository-only scratch commands:

```bash
uv run --locked python scripts/database_migration_probe.py plan --sqlite-path /tmp/pc-probe.sqlite3 --backup auto
uv run --locked python scripts/database_migration_probe.py apply --sqlite-path /tmp/pc-probe.sqlite3 --backup auto --maintenance-confirmed --yes --plan-id PLAN_ID
uv run --locked python scripts/database_migration_probe.py verify --sqlite-path /tmp/pc-probe.sqlite3
```

Read-only commands create no missing target, parent, lock or control table. No-op apply performs no backup or lifecycle changes. Changing runs require explicit plan acceptance and stopped-writer confirmation. Canonical-path OS locking covers symlinks and process exit; hard links are rejected. `BEGIN IMMEDIATE` detects present writers, but cannot prove idle old clients will remain stopped.

Only Alembic's standard `pc_schema_revision(version_num)` control table is created. There are no run, step or task control tables and no generic resume ID. Each SQLite revision, version update and verification commits atomically. Retry inspects actual structure and revision, then runs only pending revisions. Partial or missing evidence blocks execution rather than replacing an original recovery point.

Automatic SQLite backup uses Online Backup API while holding a write reservation and includes committed WAL. Its immutable provider manifest and the maintenance summary are stored outside the target database with restrictive permissions and durable writes. Retry reuses the original verified recovery point. Empty initialization reports `not_required`; manual backup is an unchecked declaration (`user_confirmed`); skip requires risk acceptance (`skipped`). Retained tables and recovery points are not automatically deleted or restored.

## Real backend probes

```bash
uv run --locked pytest -q tests/builtin/persistence/test_database_migrations.py tests/test_database_migration_probe.py tests/test_database_migration_cli.py
POWERCONTEXT_TEST_MIGRATION_SEEKDB=1 uv run --locked --extra seekdb pytest -q tests/builtin/persistence/test_database_migration_backends.py tests/builtin/persistence/test_database_backup_providers.py
```

seekdb probes use a new temporary instance and a directory lock around engine startup and shutdown. The main workflow runs actual DDL probes on Linux Python 3.11 and 3.14. A simple-table Fork test additionally covers source DDL, restart and restoration to the original table name while retaining failed and Fork objects. This does not certify full PC full-text, vector, foreign-key or queue recovery.

OceanBase requires an empty dedicated database named `pc_migration_probe_<unique suffix>` through `POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL`. The probe refuses other names and nonempty targets; it leaves the resulting schema and never provisions or drops databases. Its lock probe uses two physical sessions and verifies ownership after commit and DDL. Missing environments and skipped tests are not acceptance.

The independent `BackupProvider` reports Fork features separately from an accepted PC recovery workflow. OceanBase AI Database requires 4.6.2 for both table and database Fork; seekdb requires 1.1.0 and 1.2.0 respectively. Actual product/version, full object coverage, permissions, DDL restrictions, restart and restoration must pass release-owned acceptance before automatic Fork is enabled. It never silently falls back to physical backup or skipped backup.

## Production gates

Full historical baselines, complete object ownership, data/projection and old-task validators, no-side-effect remote connections, nontransactional DDL recovery, startup/SDK/Worker gates and mandatory schema-change CI remain prerequisites for production enablement. The current limited bundle refuses `--manage-service`; its result must not start a full business runtime. Native service start/stop/restart and the separately tested maintenance lifecycle API do not establish those database gates. PR #1716 remains independent.
