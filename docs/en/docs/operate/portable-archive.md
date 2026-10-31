---
title: Export and restore a portable archive
description: Create, validate, and restore a verified PowerContext logical bundle.
---

# Export and restore a portable archive

A portable archive is a `.pcb` file for moving or recovering complete PowerContext scopes. It is a logical archive:
it preserves domain identities and immutable history rather than copying a SQLite file. Use it for a controlled local
backup, an offline transfer, or a future backend migration.

This is an offline operator command. It does not call a remote Server API. Without `--env-file` it opens the default
SQLite database; with `--env-file` it uses the same SQLite, SeekDB, or OceanBase settings as that deployment. Stop
PowerContext writers before restore. Export uses one database transaction as its consistent logical snapshot.
MySQL-mode backends use `REPEATABLE READ` for the export transaction even when the deployment defaults to
`READ COMMITTED`; later transactions retain the deployment default.

## Choosing the recovery format

Use a deployment-native backup when you need the complete database, deployment-local state, or point-in-time recovery.
A `.pcb` bundle addresses a narrower requirement: transfer selected complete scopes between supported backends while
preserving their exact identities, revisions, evidence and retry behavior, without copying access bindings, host-local
selection, scheduler state or derived indexes. A SQLite database copy is not an OceanBase database, and replaying a
backend's SQL dump does not perform these application-level selections and projection rebuilds. Schema migrations
upgrade a deployment's schema; they do not replace this scope-transfer operation.

Checksums detect corruption, not producer authenticity or completeness against the source database. Domain decoding
and dependency validation still run before restore.

## Prerequisites

Install the CLI from the same PowerContext revision that created the local data. The archive command uses the persistent
SQLite database at `POWERCONTEXT_HOME/powercontext.db`; without `POWERCONTEXT_HOME`, PowerContext uses its operating
system user-data directory.

```bash
uv tool install "powercontext[cli] @ git+https://github.com/oceanbase/powercontext.git@master"
export POWERCONTEXT_HOME="$HOME/.local/share/powercontext"
```

The archive contains content and metadata needed for recovery. Store it in a protected location with access controls
and retention appropriate for the underlying project data. The format deliberately omits credentials and configured
provider secrets, but it does not encrypt the project content.

## Create and inspect an archive

Pass every complete scope to include. A scope cannot be selected by prefix or partially by record type.

```bash
powercontext archive export \
  --scope-id project:payments \
  --output ./backups/payments.pcb

powercontext archive inspect ./backups/payments.pcb
```

Add `--no-compress` when the surrounding storage system already compresses objects. Export authorization occurs before
the first database query. For this offline command, authority is the operating-system and database permission needed to
read the configured deployment; it never reuses or exports Server bearer tokens. Progress is emitted as content-free
JSON on stderr while the final receipt remains JSON on stdout.

`export` writes JSON containing the bundle ID, record count, and checksum. `inspect` verifies the ZIP structure,
per-record digests, total digest, record counts, and selected scopes without opening the target database. Keep the
reported checksum with the backup inventory if an external backup system needs an independent verification record.
Before publishing the file, export checks its format, resource limits, and dependencies. If validation fails, an
existing backup at the output path remains unchanged.
The destination must not be the source SQLite database or one of its `-wal`, `-shm`, or `-journal` files; export rejects
these paths rather than replacing live storage with an archive. These sibling filenames are reserved regardless of
letter case, including on case-sensitive filesystems.

## Validate before a restore

Always validate the archive against the destination first:

```bash
powercontext archive restore ./backups/payments.pcb --dry-run
```

Dry-run performs no domain writes. It verifies checksums, required source and Artifact dependencies, and whether the
configured Runtime can restore the archive's source types and Artifact families. A validation failure exits with code
`2` for an invalid bundle or an uninspectable SQLite filename, or `3` for unsupported requirements or target conflicts,
and reports only a content-free
reason; record bodies are not printed. A successful report includes
`already_present`, `conflicts`, required and unsupported Source types and Artifact families, and whether the target has
a projection rebuilder. Candidate versions and decision heads use the same complete decoder as normal reads. Validation
checks their family consistency, evidence, reasons, lifecycle state and family-owned proposals, including optional Dream
audit envelopes; malformed or unsupported Candidates are rejected before any domain write. Run against a different
deployment with `--env-file ./target.env`. Existing SQLite targets, including `file:` URI filenames with `uri=true`,
are inspected read-only. A missing SQLite target is validated without creating its database file or parent directory.
This includes absolute `file:///C:/...` paths on Windows and percent-encoded filenames. Unsupported URI authorities,
unreadable targets, directory targets and non-directory parent paths fail validation rather than being treated as an
empty database.

Publication validation follows exact Artifact revision addresses through the complete chain. Cycles and inconsistent
content digests within a chain are rejected before domain writes, even if all endpoints and checksums are valid.
Compatibility checks also compare existing target publication links for restored revisions. A target link absent from
the archive is a conflict, even if its chain is valid: accepting it could change the restored revision's original
provenance. The graph is indexed in temporary local storage, and target links are read in batches without scanning
unrelated publication histories. Conflicting target histories are reported without overwriting them; recovery from an
already invalid history requires a clean target and a valid backup.

Tag labels and complete tag sets use the normal domain validation, including the limit of 32 labels per target. New
tag identities are added without removing existing labels; the combined set must also fit that limit. An oversized
bundle tag set is invalid input; an oversized combined target set is a conflict, rejected before writes.

Native Sources require their Python adapter on the target. Before writing records, validation uses the same registered
Source decoder as normal reads to check both payload shape and indexed identity, including supported legacy payloads.
Worker-materialized `SourceObservation` records carry their captured payload and projections. The same decoder
validates their storage envelope without loading the worker adapter. The bundle includes only the definition
manifests referenced by the selected scopes and verifies observation identities and definition
fingerprints before restore. Missing definitions and conflicting target definitions prevent restoration; unreferenced
global definitions are not exported.
Observation payloads and supplied projections must also satisfy the same manifest schemas checked during normal
ingestion. Restore does not fetch remote schema references.

To perform the write, repeat the command with explicit confirmation:

```bash
powercontext archive restore ./backups/payments.pcb --yes
```

The result is JSON with `inserted`, `already_present`, and `projections_ready`. Treat the restore as ready for search
only when `projections_ready` is `true`. The Runtime rebuilds Memory, Topic Memory, and Experience search projections
after the authoritative rows have been restored, including configured Memory and Topic Memory vectors.

## What the bundle preserves

Format version 1 carries the portable relational representation of these supported records:

| Preserved | Not portable |
| --- | --- |
| Scope identity, hierarchy, context/external references, and creation identity | Scope access bindings and host-local default selection |
| Source journal heads, Source records, and referenced remote Source Definition manifests | Search projections and indexes |
| Accepted Memory evidence: per-Source declaration bindings, the Definition manifests they reference, and exact-revision evidence snapshots | Today's registry declarations, which never replace a saved snapshot |
| Artifact Revisions, their timestamps/actors/rollback metadata, stable handles, lineage, cross-Scope publication provenance, and heads | Source cursors and scheduler state |
| Atomic Memory merge relationships and marked merge-input lineage | External Skill registrations and host-local installation state |
| Candidate versions, decision heads, and their evidence references | Audit events, usage facts, evaluation receipts, and restore receipts |
| Managed Skill package bytes and manifests; Artifact tags | Download locations and host-local Skill paths |
| Profile policies, Prompt revisions, and Topic Memory publication metadata | Target-specific embedding configuration |
| Work contracts, task outcomes, Handoff boundaries and receipts stored as Sources | Credentials, bearer tokens, provider secrets, and host-local Skill installation state |

The archive has a versioned manifest (`format_version`, producer version, scopes, counts, exclusions, and total
checksum) plus NDJSON records. Per-record digests compare canonical content for conflicts; the total digest verifies
the record stream. It is the authoritative round-trip format. CSV may be produced separately for bounded
analysis, but it cannot preserve immutable revisions, lineage, or evidence references and must not be used for restore.

Frozen recurrence matches and events are preserved with their original idempotency keys, Source positions, and exact
Artifact revisions. Restore validates candidate revisions, Handoff/receipt references, and event-to-match dependencies.
Advancing an Experience head does not change this history; replay after restore reuses the saved decisions.

Merged inputs remain frozen after restore, and marked merge-input lineage remains available for undo-merge. These fields
also participate in conflict checks: the same revision with a different merge relationship is not an identical record.

After Atomic Memory migration, bundles preserve the migrated Atomic Memory identities, immutable revisions and lineage.
The unused legacy entry rows whose collection is no longer public and the offline Memory collection archive are not
portable Runtime state. Export leaves this retained history untouched in the source database. Keep a deployment-native
backup if you need the original pre-migration collection snapshots, migration records or access-control history.

## Recovery and conflicts

Restore is idempotent. Replaying the same archive recognizes identical records as `already_present`. A different
payload for an existing immutable identity never overwrites it: restore exits with code `3` and leaves the write
transaction rolled back. Resolve the conflicting target data or choose a clean destination, then run the same restore
command again.

The restore command opens its maintenance Runtime without bootstrapping a new default scope. This lets an archive
containing the canonical default scope restore its original identity and creation key into a fresh target. On the next
normal startup, default-scope bootstrap reuses that creation identity when present. Default selection bindings remain
deployment-local; restoring an archive does not overwrite an existing deployment's selection or conflicting scope.
The maintenance Runtime does not start background Artifact processing supervisors, even when processing is configured
for the target deployment. Restart the normal deployment after recovery to resume background work.

If the command is interrupted before completion, do not claim recovery succeeded. Authoritative records are
transactionally applied, so a failed write does not leave a successful-looking partial restore. Re-run the same archive
against the same destination after the previous command has stopped.

The target database stores a durable receipt keyed by `bundle_id`. `authoritative_restored` means the logical rows
committed but search rebuild has not completed; `ready` means projection rebuild completed. If projection rebuild
fails, re-run the same restore: identical rows are skipped and the receipt advances to `ready` only after verification.
While any receipt has `projections_ready=false`, normal Runtime/Server startup and worker startup refuse to serve,
including when the archive contains only Atomic Memory or Experience. The explicit restore path may reopen the target
and unfinished Topic Memory projections to complete recovery. Re-run the same restore before serving requests, using
the same target embedding configuration. Export from an unfinished recovery exits with code `3` and requires recovery
to finish first.

Restore stages records in a temporary disk index before starting the target write transaction. Reserve temporary
disk space for both the archive copy and its expanded records. Existing MySQL-mode deployments may require an
explicit Runtime startup schema upgrade from `DATETIME` to `DATETIME(6)` for portable timestamps; back up the database
and provide schema-alter permissions before that upgrade.

## SQLite to OceanBase migration

Create separate environment files without placing credentials in the bundle:

```bash
powercontext archive export --env-file ./sqlite.env \
  --scope-id project:payments --output ./payments.pcb
powercontext archive restore ./payments.pcb --env-file ./oceanbase.env --dry-run
powercontext archive restore ./payments.pcb --env-file ./oceanbase.env --yes
```

The environment files use `POWERCONTEXT_SERVER_DATABASE_KIND` and `POWERCONTEXT_SERVER_DATABASE_URL` as documented in
the Server configuration guide. Dry-run must report no unsupported families and zero conflicts before the write.

## Format compatibility

Bundle IDs must use the canonical lowercase, hyphenated UUID representation. The entire archive file, including ZIP
prefixes or trailing bytes, is limited to 512 MiB. The manifest limit is 256 KiB; uncompressed records are limited to
2 GiB, with at most 2,000,000 records and 8 MiB per NDJSON line. Oversized files are rejected before restore copying,
and snapshot copying enforces the same limit if the source changes while being read.

Format version `1` readers require support for every record type present in the bundle. Older readers may reject
new record types even when the bundle uses format version `1`. The producer version is reported for diagnostics but
does not replace the archive schema version or record-type compatibility checks. Writers produce only version `1`; there is no
format `0` downgrade. A reader rejects unknown archive or record schema versions before target writes. Keep the old
binary available until a restore drill succeeds when upgrading across a PowerContext major release.

`artifact_revision`, `artifact_head`, `artifact_lineage_artifact` and `candidate_head` require record schema version `2`.
Incomplete schema version `1` representations of these records are rejected rather than discarding revision metadata
or assigning default merge state or Candidate kind. Legacy database revisions retain explicit null metadata; restore
does not invent an actor, timestamp or rollback history. A target missing required archive columns must first be
upgraded by the current Runtime; dry-run reports this incompatibility without changing the target.
Create a fresh bundle from the source database with the current binary when a bundle fails these compatibility checks.
Binary fields use canonical Base64, and timestamp fields use the canonical timezone-naive ISO text emitted by the
supported database profiles. Alternative encodings, offset-bearing timestamps and out-of-range storage values are
rejected before writes rather than accepted with digests that would change on readback or conflict on retry.

## Deployment-native backups

Logical export is not a substitute for a crash-consistent deployment backup:

- SQLite: stop all PowerContext writers before copying the database, or use the SQLite online backup API, for example
  `sqlite3 powercontext.db ".backup './backups/powercontext.db'"`. Verify the copy with
  `sqlite3 ./backups/powercontext.db "PRAGMA integrity_check"`, protect it like the source database, and test reopening it.
- OceanBase: enable tenant data backup and log archiving according to the deployed OceanBase version, retain the backup
  destination and encryption material independently, and perform a restore drill into an isolated tenant. Use native
  backup for point-in-time recovery and the portable bundle for logical cross-backend migration.

## Current boundaries

Target compatibility checks group records by type, with at most 100 records per SQL statement, 900 bound
parameters across the pending groups, and 1 MiB of encoded lookup keys and expected digests. Wider composite
keys can produce smaller batches. Pending groups retain keys and digests, not record bodies. The result cursor
fetches one target row at a time; peak memory still includes a single decoded archive record, a target row,
canonical encoding buffers, and database-driver overhead. A large existing target body therefore does not
multiply by the batch size. This is a working-set bound, not a fixed process-memory limit.

Each batch preserves the target database's key comparison rules and checks both logical identities and
alternate physical unique keys. Any differing match makes that archive record a conflict, counted once.
Nullable unique keys retain SQL null semantics. Dry-run results are not cached for a later restore: restore
checks the current target again, and write-time conflicts still roll back the transaction. Publication-chain
and combined-tag-set checks run after the record checks.

Target-check progress is reported after each completed batch, even when its processed count skips a
1,000-record reporting threshold. Records whose target tables are absent retain periodic per-record reporting.

For the representative archive containing one Scope, one journal head, and N small Sources, the compatibility
query budget against initialized tables is `ceil(N / 100) + 2`: 3, 12, and 52 statements for 100, 1,000, and
5,000 Sources. Missing tables require no record compatibility queries. Schema introspection, local dependency
index queries, and publication/tag integrity queries are separate from this budget.

Run `uv run python scripts/benchmark_archive_validation.py --sources 100 1000 5000 --runs 3` to measure full
validation times and query counts on disposable file-backed SQLite targets. Fixture generation and export
are excluded. The separately reported restore duration includes revalidation, staging, authoritative writes,
and the receipt, with no projection rebuilder configured; it is not an isolated write or search-rebuild timing.
Compare the same script and environment across checkouts. Fewer SQL executions do not imply the same reduction
in complete validation time: archive parsing, dependency checks, decoding, and digests remain per-record work.
Embedded SeekDB is local; remote OceanBase costs depend on the deployment topology and need real-backend measurements.

On 2026-10-23, the script was run against baseline `4a21da08` (PR #1561 plus current master) and the batched
implementation on Windows 11 build 26200, CPython 3.12.13, SQLite 3.50.4, with default SQLiteProfile settings,
256-byte Source content, and no injected network delay. Each cell below is the median of three full validations;
query counts were identical in all repetitions and exclude schema introspection.

| Records | Target | Queries: baseline → batched | Seconds: baseline → batched |
| ---: | --- | ---: | ---: |
| 102 | Empty | 102 → 3 | 0.3469 → 0.2828 |
| 102 | Identical | 102 → 3 | 0.3755 → 0.3039 |
| 1,002 | Empty | 1,002 → 12 | 2.6401 → 1.9954 |
| 1,002 | Identical | 1,002 → 12 | 3.2584 → 2.3064 |
| 5,002 | Empty | 5,002 → 52 | 16.3445 → 9.1713 |
| 5,002 | Identical | 5,002 → 52 | 16.9955 → 10.7552 |
| 5,002 | Tables absent | 0 → 0 | 7.9935 → 7.5444 |

The missing-table control also varies despite executing no compatibility queries; these are local diagnostic
measurements, not a throughput guarantee. One 5,002-record restore without projections took 17.6685 seconds on
the baseline and 10.5197 seconds with batching, including internal validation and staging. Projection rebuilding
was not measured. Native SeekDB and OceanBase were not executed in this environment; their parameterized archive
acceptance tests require actual backend runs before those configurations are qualified for this optimization.

Export streams database rows to a temporary NDJSON file. Validation stores record identities and Candidate versions in a
temporary on-disk index to verify each head against its exact version. Restore replays dependency levels as streams, so
aggregate archive payloads are not retained in
process memory. Scope metadata is bounded by the manifest scope list. The command is not a remote Server API and never
puts deployment configuration or database credentials into the archive.

Artifact Candidates are supported; tag-change Candidates are rejected before export publishes an archive or restore
writes records. An archive must not silently drop a tag-change decision or reinterpret it as an Artifact Candidate.
