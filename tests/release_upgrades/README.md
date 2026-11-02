# Installed-release upgrade acceptance

`release/upgrade-support.json` requires **positive** 1.3.0 upgrades on SQLite, embedded seekDB, and OceanBase.
The current `upgrade-required` mode keeps the upgrade support declaration unverified while requiring every automatic case
to run and pass, including both embedded seekDB Code configurations. Missing full migration integration (#2154) fails the gate. `supported-upgrades` runs the same
matrix and enables publication after the reviewed support declaration is updated. `fresh-install-only` is rejected.

## Reviewed sources and current target

The declared target release is **1.4.1**. Public migration output maps the two source baselines to
`v1_3_0_000` / `v1_4_0_000` and the shared steps to `v1_4_1_001`–`v1_4_1_006`. The suite checks
these names while retaining the original revision IDs in execution and recovery evidence.

The `automatic_sources` policy uses `strategy: "fixed-version"` and `source_version: "1.3.0"`.
Explicit `cases` are empty. The catalog retains twelve configurations of the 1.3.0 source across SQLite,
embedded seekDB, and OceanBase. The target is the **actual wheel built by the shared preparation job**, not the latest published
PyPI package. Later target releases keep the same 1.3.0 baseline; intermediate releases are not added automatically.
The framework and fixture retain the independently registered 1.4.0 adapter, but CI does not execute its
installed-release upgrade path. Passing this gate does not establish 1.4.0 upgrade acceptance. This matrix does not establish support for every intermediate release's database or
data shapes; add reviewed explicit `cases` and source adapters if those need separate acceptance.

The runner resolves the exact source wheel from public PyPI and installs its URL with a SHA-256. Missing/yanked
source wheels, invalid artifact metadata, catalog outages, and a target at or below the source version fail the gate.
Evidence records the catalog digest, exact source artifact, target artifact digest, and expanded matrix. The resolver
requires universal `py3-none-any` wheels; a distribution format change must extend it explicitly.

The selected source uses all twelve profiles in `test_full_server.py`: Code enabled or disabled, with or without vectors,
on SQLite, seekDB and OceanBase. Vector cases use exact unit-normalized L2 profiles: dimension three without Code and
dimension four with Code. The local controlled embedding endpoint serves actual installed source/target processes;
it does not replace either runtime or its persistence. Each source Server writes nonempty Atomic Memory and Topic
vectors. Maintenance must preserve the exact historical embedding digests without calling that endpoint. Target
acceptance checks historical and new vector retrieval, Topic content, configured profile drift without writes,
and fresh-storage equivalence. SQLite additionally rejects a missing vector helper without repairing the clone.
These assertions establish deterministic execution and space preservation, not embedding model quality.
Code cases preserve immutable cache files and original fingerprint/symbol identities. Embedded seekDB additionally
preserves the exact graph-table row digests through maintenance; its frozen extension creates the same schema for fresh storage.
The CI suite starts the installed 1.3.0 Server, writes data through HTTP,
stops it, seeds a pending processing intent using the installed SDK, and runs the target wheel's public
`server db-migrate status/plan/apply/verify` commands. Reviewed adapters select the source Candidate API and table
names. The retained, unselected 1.4.0 adapter creates a pending intent with `consecutive_dream_attempts=1`; target readback must preserve
every field supplied by the source SDK before idempotent acknowledgment. The 1.3.0 fixture retains its original
pending-intent contract. Verification must report `state=ready` and `server_ready=true`.
The target Server must then start, preserve data and permissions, accept new writes, and support retrieval.
Partial migration success, missing framework/baseline support, and startup rejection all fail. SQLite profiles use
automatic backup and restore that recovery point into a separate database. The suite compares the complete
restored source schema and row digests, reads historical Memory, Candidates, grants and cursors with the installed
source release, upgrades the restored database again, then verifies target reads, FTS and new writes. Code-enabled
SQLite also preserves its separate immutable cache files. The original backup checksum must remain unchanged.
seekDB and OceanBase fixtures explicitly accept no backup; these cases do not establish backup restoration.
This is not a production upgrade procedure.

`cases` remains available for additional exact source versions/configurations. Each case needs `source_version`,
`backend`, `configuration`, and `suite`; overlapping cases fail. Optional features that create persistent objects
require additional profiles. The fixed CI baseline and advertised upgrade support must stay consistent.

PR/master and publication CI start an isolated OceanBase CE container using the image and MySQL tenant setup from
the existing E2E workflow. They wait for SQL readiness and adequate datafile capacity before testing, save diagnostics,
and remove the container even when a check fails. No external database credentials are needed. Embedded seekDB
runs directly in a temporary runner directory. A backend startup failure fails the job instead of skipping it.
OceanBase reserves separate upgraded and fresh databases for every source/configuration pair. The default
connection URLs remain `pc_upgrade_test` and `pc_upgrade_fresh`; the suite selects the corresponding configuration
and, for 1.4.0, the `pc_upgrade_140` prefix before either Server is started. Every selected database must be empty.
PR and publication CI use the same reusable workflow, `upgrade-release.yml`:

1. **Prepare once:** build the wheel and source archive, rebuild the source archive to verify matching contents,
   validate release metadata, and resolve the source catalog into one frozen twelve-case plan.
2. **Run independent cases:** each selected matrix job executes exactly one source/backend/configuration tuple.
   Every job downloads the same prepared artifacts and verifies their hashes. Each OceanBase job owns a separate
   container; SQLite and embedded seekDB run in their own runners. `fail-fast: false` allows other cases to finish
   after a failure. There is no second serial execution of the matrix.
3. **Collect once:** require exactly one passing result for every planned case, revalidate JUnit phases, installed
   versions and backend evidence, and reject duplicate, missing, skipped, failed or mixed-plan reports. The plan
   binds the workflow run, checkout, reviewed policy and distribution hashes. A new preparation gets a new identity;
   rerunning only failed cases can reuse the unchanged prepared plan and the other cases' results from that run.
4. **Gate publication:** require all case jobs, including cleanup and artifact upload, to succeed. Only the accepted automatic
   distribution snapshot can reach PyPI or GitHub Release assets. `upgrade-required` still blocks publication.

Main and Release automatically require all 12 configurations, including embedded seekDB `full-server-code`
and `full-server-vector-code`. The project requires `pylibseekdb>=1.4.0.post2,<2`, and `uv.lock` pins
`1.4.0.post2`, which contains the recovered-transaction lock fix from seekdb#1499.
Both `full` (the default) and `automatic` select the complete matrix.

For focused diagnostics, Actions → Main → Run workflow still accepts `upgrade_selection=seekdb-code`
to run just the two seekDB Code configurations. Other Main CI suites still run normally:

```sh
gh workflow run master.yml --repo oceanbase/powercontext --ref YOUR_BRANCH -f upgrade_selection=seekdb-code
```

A diagnostic subset produces no publication artifacts and cannot authorize release. Missing, skipped or failed
cases in the full automatic matrix block publication, and the reviewed support declaration remains mandatory.

The fixed required check remains **Upgrade release gate**; on Main it covers only the selected configurations. It fails if the shared workflow fails or is cancelled.
Each case job has a sixty-minute budget including provisioning; existing thirty-minute command deadlines and
owned-descendant cleanup remain unchanged. Wall time depends on the slowest case and available runner capacity,
not the sum of twelve cases. This increases concurrent runner use and does not promise a fixed speedup.

The runner creates separate source/target environments, installs the exact published source package and the built
target wheel, pins both seekDB environments to the `pylibseekdb` version in the checkout's `uv.lock`, verifies their versions, then executes the reviewed suite in an isolated working directory. Repository
pytest configuration and deployment environment variables are excluded. The suite must exercise those installed
processes, not import the current checkout's runtime in place of either version. Use `-I` when invoking their Python.

The suite receives:

- `PC_UPGRADE_SOURCE_PYTHON`, `PC_UPGRADE_TARGET_PYTHON`: isolated installed interpreters.
- `PC_UPGRADE_SOURCE_VERSION`: the exact source release selected for this case.
- `PC_UPGRADE_BACKEND`, `PC_UPGRADE_CONFIGURATION`: the declared case.
- `PC_UPGRADE_WORK`: disposable workspace for both databases, controlled inference fixtures, and environment files.
- `PC_UPGRADE_EVIDENCE`: destination for backend evidence JSON.
- `PC_UPGRADE_REPORT_DIR`: persistent per-case command logs, fixture snapshots and schema inventories.
- `PC_UPGRADE_OCEANBASE_URL`, `PC_UPGRADE_OCEANBASE_FRESH_URL`: dedicated disposable MySQL-tenant databases for
  the upgraded and fresh-install comparisons. CI supplies both; local positive OceanBase suites must supply their
  own isolated databases. Each suite must isolate/reset its own fixtures when multiple configurations run.

Provide these behavioral tests (additional tests are allowed):

| Test | Required behavior |
| --- | --- |
| `test_source_release_database` | Start the actual source Server to create its complete database. Populate representative Memory history, references, permissions, Candidates, tasks and cursors through that version's public interfaces; verify the data before shutdown. Do not create a minimal schema by hand. |
| `test_legacy_startup_is_rejected` | Target startup on the old database must fail without changing schema or row digests. |
| `test_repeated_migration` | A second plan/apply/verify returns unchanged and preserves every table row digest. |
| `test_public_migration_commands` | Stop writers, run the target artifact's public maintenance commands, and validate full readiness. SQLite additionally restores the actual automatic recovery point, reads it with the source Server, upgrades it and verifies target use. A limited bundle verification does not pass. |
| `test_target_startup` | Start the target Server against the migrated database and verify readiness. |
| `test_read_write_and_retrieval` | Read the old records, write new ones, and retrieve both through public interfaces. |
| `test_authorization` | Preserve the old viewer grant; verify allowed reads, denied cross-scope reads, denied writes and unchanged data after rejection using both installed releases. Invalid credentials must still fail. |
| `test_candidates_and_processing` | Verify retained Candidates and CAS approval; load and acknowledge the old pending intent using the target installed SDK, including idempotency. |
| `test_background_processing` | Run the installed target's actual Memory Worker against a local controlled inference endpoint; capture a new Source, verify the published Memory's exact Source reference and FTS retrieval, and require the child Worker's usage report. |
| `test_preserved_history_and_cursors` | Verify exact history/references and unchanged or declared-transformed cursor and receipt semantics. |
| `test_fresh_upgrade_equivalence` | Initialize a fresh target database; compare schema/backend objects and behavior with the upgraded database. The current suite has no retained/archive object exclusions. |

The authorization probe runs each installed release's native Server through real HTTP handlers with a fixed
ordinary-user authentication provider. The persisted builtin authorization policy is not overridden. Both the
source and target CLI Servers are stopped before the SDK probe opens storage, so embedded seekDB has one owner.
CLI readiness remains a separate mandatory phase.

Use a session fixture for the shared lifecycle; do not depend on pytest's test execution order. All collected tests
must execute and pass. Empty collection, missing phases, skips (including missing backends), xfail, failure, command
failure, and missing/malformed reports fail the gate. A real OceanBase suite must provision an isolated database and
fail if its required environment is unavailable; it cannot replace execution with mocks. Controlled model responses
may test domain behavior, but do not establish model quality.

Write backend evidence containing `backend`, nonempty `engine_version`, `driver_version`, and recorded schema
inventories or their digests in `source_schema`, `target_schema`, and `fresh_schema`. OceanBase additionally requires
`tenant_mode: "mysql"` and `proxy_version` (or `direct`). Do not record credentials or business payloads. The controller
records installed Python/package versions, target wheel SHA-256, policy SHA-256, and the individual case result.

Evidence is created during the gate run in new directories. Parallel collection accepts only reports bound to
that preparation plan; unrelated historical results cannot satisfy the gate. The local serial runner does not
import reports.
Test names and metadata do not themselves prove correctness: reviewers must inspect the assertions and confirm
they follow actual installed operations and compare persisted data. The profile exercises explicit writes, FTS and
actual background Memory processing with controlled model responses. Both installed releases declare the Memory
capability and use the same processing ownership; the Worker check changes only the execution interval after migration.
The separate SDK fixture tests the old pending intent's format and acknowledgment. Controlled responses establish
execution and evidence preservation, not real-model quality. Vector storage uses its separately registered profiles;
passing a no-vector case cannot establish vector acceptance.
SQLite inventories include indexes, views and triggers; MySQL-family table DDL includes indexes and constraints,
and views are collected separately. MySQL comparison parses columns, indexes (including parser properties), constraints, and CHECK expressions, ignoring only physical formatting and backend-generated constraint names. Views and triggers are collected independently; native trigger comparison includes timing (`BEFORE`/`AFTER`),
event, table and statement. SQLite preserves quoted tokens and normalizes only unquoted formatting whitespace. Structural differences fail rather than being silently excluded.

Regenerate installation support regions with
`uv run python scripts/upgrade_release_gate.py --write-docs` and run `make upgrade-policy-check`. When persistence
inputs change, calculate `managed_digest(ROOT)` from `upgrade_release_gate`, review the compatibility impact, and
update both the digest and rationale in `schema_review`. The inputs include persistence, optional code storage,
Server identity and authorization, and shared limits used in table definitions. Add new schema owners to
`MANAGED_INPUTS` when introducing them. The digest intentionally includes non-schema code: it forces a review of
changes, but does not infer semantic compatibility or replace real upgrade tests.

Build one wheel and its matching `.tar.gz` source distribution into `dist/`, then run `make upgrade-release-test`. Before release, additionally run
`uv run python scripts/run_upgrade_release_gate.py --release --release-notes /path/to/notes.md`. The notes must contain
the exact English support declaration generated in the installation guide. The release workflow performs this check
against the GitHub event's notes and gates both PyPI publication and release assets on the same tested wheel.

Repository administrators must require **Upgrade release gate** in branch protection. Workflow code cannot configure
that setting. Because the release workflow starts on `release.published`, it can block package/asset publication,
not the earlier manual creation of a GitHub Release; run the prepublication command before publishing that page.

## Historical rejection regressions

`scripts/ci_legacy_upgrade_rejection.py` remains a diagnostic tool for historical rejection behavior. PR and release
workflows do not invoke it as upgrade acceptance. An expected rejection or its `--respect-policy` result can never
replace a successful positive upgrade. Independent CI jobs attempt every required combination even after another case fails; the local serial runner
continues through the same matrix. Both fail overall and preserve each case's logs and result. Empty matrices and missing suites are errors.

## Publication artifacts

Preparation validates every distribution input once before parallel execution. Publication requires exactly one wheel and one source archive with
matching names and metadata. It rebuilds the source archive with `uv build --wheel` and compares wheel member bytes,
excluding only the derived `RECORD` file. A same-version stale source archive fails just like an older-version one.
PRs may test a wheel alone, but the PR workflow builds both formats to exercise this validation too.

Acceptance uses a private copy of those files. Only a passing run creates `validated-dist/` and `publication.json`
in its evidence directory. CI uploads that directory; local `make publish` calls the runner with `--release --publish`
and uploads its exact returned paths, never a fresh glob of `dist/`. Failed acceptance produces no publication set.
Managed persistence hashes normalize CRLF/LF checkout line endings; artifact SHA-256s remain byte-exact.


Installed-release execution requires Linux. Each gate command runs under a separate
Linux child subreaper, which owns detached Server and embedded-engine descendants.
On timeout it first interrupts the command so fixtures can close the Server and
seekDB normally, then terminates and reaps any remaining owned descendants before
collecting native logs, deleting the workspace, or starting the next case. Normal
success and failure also clean up leftover descendants. If the gate is terminated,
the owner receives a Linux parent-death signal and completes the same cleanup.
Policy and documentation checks remain available on other platforms.

Main first runs the previously failing embedded seekDB concurrent-startup regression in
`SeekDB managed startup regression (priority)`. Its three isolated Server/SDK scenarios run
once, before the installed-release workflow and the remaining seekDB clean-migration job.
Those jobs still run after a failed priority check unless the workflow is cancelled; the
selected installed-release cases remain disjoint and parallel. Other CI suites can run concurrently.

## Regression checks

The shared prepare job runs gate, publication and schema-equivalence regressions before checking the reviewed
persistence digest. Digest failure still blocks preparation; the earlier regression result remains visible.
The raw DDL comparator preserves quoted literals, comments and identifier boundaries, normalizing only unquoted
whitespace and table-level auto-increment counters. Native logical table reflection remains authoritative for
seekDB/OceanBase comparisons, with views and triggers also compared. SQL literal differences must never disappear
as formatting changes. Unknown configurations and missing or mismatched execution evidence fail closed.

Full installed-release acceptance requires Linux process ownership and descendant cleanup. Other hosts can run
the policy and comparison regressions, but the full runner rejects them before creating workspaces or installing
packages. Passing local regressions is not evidence of a successful native-backend upgrade.


## Acceptance boundaries

A passing run establishes the selected cases for that exact target build, not every historical production database.
The source fixture is newly initialized with the official 1.3.0 package and representative data; it does not recreate
an older-version-to-1.3.0 lineage or an existing user's database. Each future target build must rerun acceptance.
The Linux matrix does not establish macOS embedded-engine, desktop-daemon or in-place tool-installation behavior.
Source and target dependency resolution does not freeze the dependencies of a historical installation, so recorded
engine/driver versions must be considered when interpreting compatibility evidence.

The positive path exercises the public `db-migrate` commands, not the historical `atomic-memory-migrate` procedure.
SQLite tests automatic backup restoration; seekDB and OceanBase explicitly skip backups in this suite and do not
establish backup/rollback acceptance. Both embedded seekDB Code configurations require successful evidence
as part of the complete automatic matrix.
