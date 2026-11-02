# Candidate migration replay prototype

The development command replays the frozen `s1300` → `s1400` Candidate conversion on real SQLite, embedded seekDB, or OceanBase. It generates full-database schema inventories and ordered changes from database reflection, then compares the result with the existing registration. It does not edit migration resources, change checksums, or install a new registration format.

Run it from a development checkout:

```sh
uv run --locked python scripts/replay_candidate_migration.py sqlite --output /tmp/candidate-sqlite
POWERCONTEXT_TEST_MIGRATION_SEEKDB=1 uv run --locked --extra seekdb python scripts/replay_candidate_migration.py seekdb --output /tmp/candidate-seekdb
```

OceanBase uses the existing native fixture: set `POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL` to a dedicated `pc_migration_probe_*` management database, then select `oceanbase`. The fixture creates a unique child database and drops only the database it successfully created. No production database is an appropriate target. Output must be a new directory. The command fails if any of its three backend cases fails or skips; missing native services cannot produce a passing result.

## Evidence and comparison

Each case records the backend and engine/binding versions, frozen resource hashes, bundle and revision checksums, source release, operation order, full reflected inventories, their hashes, and changes derived by comparing consecutive inventories. Data-copy operations retain their own position even when their schema delta is empty. The generated changes reconstruct the complete reflected schema, including foreign-key rewrites in tables not named by a rename.

Native replay records each Candidate operation from the frozen `s1400` SQL registration, including the dynamic foreign-key operation. Actual reflection is compared with the corresponding full registered checkpoint using the existing backend normalization rules. Expected checkpoints are never used to construct generated inventories. SQLite executes its frozen Alembic implementation, records Candidate SQL boundaries, and compares the completed `s1400` schema with the frozen SQLite target. SQLite intermediate inventories are transaction-local observations, not independently durable recovery points.

All cases start from the frozen full Server 1.3.0 baseline and seed two historical Candidate versions, a current head, and a Profile reference. They verify preserved original columns and retained copies, both cross-table foreign-key rewrites, complete readiness, original maintenance evidence, and a no-write repeated apply. These are synthetic fixtures built from captured baseline resources; they are not a substitute for installed-release acceptance with actual historical wheels.

## Interruption semantics

The three scenarios are uninterrupted replay, interruption after the first retained-history copy, and interruption after renaming the versions table. The interruption is raised after the actual database operation returns; the runner closes its connection before recovery begins.

- SQLite must roll back the schema and Candidate rows to the baseline, then complete the same upgrade with the original backup and maintenance window.
- seekDB and OceanBase must retain committed DDL. An uncommitted retained-copy insert must roll back and be safely replayed. Recovery uses the existing runner and original maintenance evidence; only this controlled connection interruption permits explicit OceanBase acknowledgement.
- The seekDB fixture holds a real engine lease across maintenance connections within each scenario. This validates real native SQL and connection-interruption recovery, not engine crashes or restart-lock recovery. Those remain separate acceptance requirements.

The `Candidate schema replay` workflow executes all three scenarios independently on all three real backends and preserves the generated evidence. A local SQLite pass is not a native-backend result.

## Use for future migrations

This prototype is an equivalence experiment outside the production bundle. For a new migration, prefer deriving reviewed registrations from real database replay, with explicit data transformations and recovery boundaries, over hand-copying intermediate table definitions. Production integration still requires a reviewed artifact format, complete source/backend provenance, immutable historical resources, and equivalent installed-release, history, repeat-apply, backup and interruption/recovery acceptance. The prototype does not claim to generate an installable migration or simplify the existing frozen `s1400` resources.
