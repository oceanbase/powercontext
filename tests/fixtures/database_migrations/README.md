# Database migration prototype

Implementation of the Phase A migration primitives in [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771), tracked by [#1756](https://github.com/oceanbase/powercontext/issues/1756).

This is a repository-only prototype for **disposable databases**. Its `ready` result means that this bundle's four tables and migration receipts verify. It does not mean that a PowerContext Server, its data migrations, or its search projections are ready. The production `powercontext server` commands and startup behavior are unchanged.

## Fixed historical inputs

The JSON resources contain the original compiled definitions of `pc_artifacts`, `pc_artifact_heads`, `pc_artifact_candidate_versions`, and `pc_artifact_tags`, including indexes, constraints, and MySQL collations. They do not import current application metadata.

| Input | Source | Purpose |
| --- | --- | --- |
| `schemas/pre_dream.json` | [495cc93d67387879382c98706f4975f638faec15](https://github.com/oceanbase/powercontext/commit/495cc93d67387879382c98706f4975f638faec15), before Dream citations | Upgrade existing rows through both change classes |
| `schemas/v1_1_0.json` | [powercontext-v1.1.0 / 86e687f13fec62151ee5657a90733586fd5158dc](https://github.com/oceanbase/powercontext/commit/86e687f13fec62151ee5657a90733586fd5158dc) | Recognize the already-upgraded released table shapes |

Both resources record the source Git blob and the SQLAlchemy compiler version. They were captured from the original commit's `tables.py` **and `limits.py`**, selecting these four original tables and compiling `CreateTable` plus each named `CreateIndex` for SQLite and MySQL. The pre-Dream fixture was not synthesized by subtracting columns from today's models. These are table subsets, not complete released database images.

The single Alembic chain is:

1. `p0001`: create the frozen pre-Dream structures.
2. `p0002`: add nullable `memory_citations` columns to immutable artifact and candidate revisions.
3. `p0003`: remove `ck_pc_artifact_tags_family`; use an Alembic batch rebuild on SQLite and explicit constraint DDL on MySQL dialects. Preserve the target constraint, foreign key, indexes, rows, and registered triggers.

`manifest.json` records exact SQLite schema fingerprints at each revision and the two supported unversioned baselines. Exact matching intentionally rejects unregistered tables, indexes, triggers, views, temporary-table residue, and alternate DDL spellings. The released table definitions and the migrated definitions have separate admitted fingerprints. No generic `stamp head` fallback exists.

Revision receipts bind each script, its declared historical resources, and the Alembic environment. A reviewed plan also binds the complete bundle, configuration digest, canonical database path, source fingerprint, and target. Editing a recorded revision or its declared resources prevents acceptance.

## SQLite executor

Run from the repository after `uv sync --locked`:

```bash
uv run --locked python scripts/database_migration_probe.py plan --sqlite-path /tmp/pc-migration-probe.sqlite3
uv run --locked python scripts/database_migration_probe.py apply --sqlite-path /tmp/pc-migration-probe.sqlite3 --maintenance-confirmed --yes --plan-id PLAN_ID
uv run --locked python scripts/database_migration_probe.py verify --sqlite-path /tmp/pc-migration-probe.sqlite3
```

Replace `PLAN_ID` with the returned ID. An interactive apply prints a fresh plan and asks once; non-interactive apply requires both the exact ID and `--yes`. The command never discovers `.env` files or opens the business runtime. `status`, `plan`, and `verify` open an existing target read-only and never create a missing target, parent, lock, or bookkeeping table. A no-op apply needs no confirmation, lock, backup, or new run.

Changing runs require the operator to stop all writers. A canonical-path OS lock excludes other migrators across processes, including symlink aliases; hard-linked SQLite files are rejected. Each schema transaction begins with `BEGIN IMMEDIATE`, which also rejects a currently active database writer. This check cannot prove that an idle legacy process has stopped. The maintenance confirmation remains an operator responsibility.

Before bookkeeping or business DDL, the executor uses SQLite's online backup API from a separate read connection while holding the database write reservation. Backups include committed WAL data, are created with restrictive permissions, and pass integrity verification. They are retained beside the target in `<filename>.pc-migration-backups/`; migration does not automatically delete them or restore over the database.

Bookkeeping bootstrap is atomic on SQLite. Each subsequent revision, version update, schema/foreign-key verification, and completion receipt commits together. Foreign-key enforcement is disabled only on the dedicated migration connection before its explicit transaction; `foreign_key_check` runs before commit. An interrupted committed run blocks readiness until explicitly resumed:

```bash
uv run --locked python scripts/database_migration_probe.py status --sqlite-path /tmp/pc-migration-probe.sqlite3
uv run --locked python scripts/database_migration_probe.py apply --sqlite-path /tmp/pc-migration-probe.sqlite3 --maintenance-confirmed --yes --plan-id ORIGINAL_PLAN_ID --resume RUN_ID
```

Recovery requires the original bundle, configuration, target, and unmodified backup. It retains the run ID and recovery point, skipping only atomically completed revisions. A crash before bootstrap commits can leave an unreferenced backup file; it is preserved, while the rolled-back database can be planned again.

## Backend probes and tests

```bash
uv run --locked pytest -q tests/builtin/persistence/test_database_migrations.py tests/test_database_migration_probe.py
POWERCONTEXT_TEST_MIGRATION_SEEKDB=1 uv run --locked --extra seekdb pytest -q tests/builtin/persistence/test_database_migration_backends.py -k seekdb
```

The seekDB probe holds the directory lock before engine startup until after shutdown and runs actual Alembic DDL on a new temporary instance. The main workflow runs it on Linux with Python 3.11 and 3.14. The probe establishes additive and constraint-change behavior, data/index preservation, and repeated `upgrade head`; it does not establish backup or interrupted-DDL recovery support.

For OceanBase, provision an **empty dedicated database** named `pc_migration_probe_<unique suffix>` and supply its `mysql+aoceanbase` URL through `POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL`. Run the same backend test file with `-k oceanbase`. It refuses other database names and nonempty targets, leaves the resulting schema for inspection, and does not provision or drop databases. No deployment credentials are embedded in the fixtures or test output.

The OceanBase lock primitive tests exclusion with two distinct physical sessions, ownership after commit, and the pinned session after DDL. It refuses unsupported locking rather than falling back to a local mutex. This is one capability probe; it does not prove exclusion across every node/proxy route. The probe has no production backup or nontransactional-DDL recovery executor.

SQLite tests exercise empty initialization, both historical table subsets, payload preservation, new tag families, constraints/indexes/triggers, consistent WAL backup and restore, no-op behavior, read-only inspection, explicit confirmation, plan changes, active writers, concurrent processes, lock-owner exit, interruption before/after every revision commit, original-backup reuse, checksum conflicts, unknown/newer schemas, and integrity failures. Backend tests skipped for missing configuration are not counted as acceptance.

## Before production enablement

The remaining RFC phases require complete released database fixtures and ownership inventory; full application revision chains; required data/projection task scheduling; seekDB consistent backup and OceanBase external recovery evidence; nontransactional-DDL postcondition recovery; all-profile startup/SDK/Worker gates; the public `server db-migrate` interface and `processing-migrate` compatibility; and mandatory schema-change CI checks. These primitives do not bypass those release gates. PR #1716 remains independent.
