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

## Validate before a restore

Always validate the archive against the destination first:

```bash
powercontext archive restore ./backups/payments.pcb --dry-run
```

Dry-run performs no domain writes. It verifies checksums, required source and Artifact dependencies, and whether the
configured Runtime can restore the archive's source types and Artifact families. A validation failure exits with code
`2` and reports only a content-free reason; record bodies are not printed. A successful report includes
`already_present`, `conflicts`, required and unsupported Source types and Artifact families, and whether the target has
a projection rebuilder. Run against a different deployment with `--env-file ./target.env`.

To perform the write, repeat the command with explicit confirmation:

```bash
powercontext archive restore ./backups/payments.pcb --yes
```

The result is JSON with `inserted`, `already_present`, and `projections_ready`. Treat the restore as ready for search
only when `projections_ready` is `true`. The Runtime rebuilds portable Memory and Experience search projections after
the authoritative rows have been restored.

## What the bundle preserves

Format version 1 carries the portable relational representation of these supported records:

| Preserved | Not portable |
| --- | --- |
| Scope identity, hierarchy, context/external references, and creation identity | Scope access bindings and host-local default selection |
| Source journal heads and Source records | Search projections and indexes |
| Artifact Revisions, lineage, cross-Scope publication provenance, and heads | Source cursors and scheduler state |
| Memory entry versions and heads | External Skill registrations and host-local installation state |
| Candidate versions, decision heads, and their evidence references | Audit events, usage facts, evaluation receipts, and restore receipts |
| Work contracts, task outcomes, Handoff boundaries and receipts stored as Sources | Credentials, bearer tokens, provider secrets, and host-local Skill installation state |

The archive has a versioned manifest (`format_version`, producer version, scopes, counts, exclusions, and total
checksum) plus NDJSON records. It is the authoritative round-trip format. CSV may be produced separately for bounded
analysis, but it cannot preserve immutable revisions, lineage, or evidence references and must not be used for restore.

## Recovery and conflicts

Restore is idempotent. Replaying the same archive recognizes identical records as `already_present`. A different
payload for an existing immutable identity never overwrites it: restore exits with code `3` and leaves the write
transaction rolled back. Resolve the conflicting target data or choose a clean destination, then run the same restore
command again.

If the command is interrupted before completion, do not claim recovery succeeded. Authoritative records are
transactionally applied, so a failed write does not leave a successful-looking partial restore. Re-run the same archive
against the same destination after the previous command has stopped.

The target database stores a durable receipt keyed by `bundle_id`. `authoritative_restored` means the logical rows
committed but search rebuild has not completed; `ready` means projection rebuild completed. If projection rebuild
fails, re-run the same restore: identical rows are skipped and the receipt advances to `ready` only after verification.

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

Format version `1` readers accept version `1` bundles from any PowerContext producer version. The producer version is
reported for diagnostics but does not replace the archive schema version. Writers produce only version `1`; there is no
format `0` downgrade. A reader rejects unknown archive or record schema versions before target writes. Keep the old
binary available until a restore drill succeeds when upgrading across a PowerContext major release.

## Deployment-native backups

Logical export is not a substitute for a crash-consistent deployment backup:

- SQLite: stop all PowerContext writers before copying the database, or use the SQLite online backup API, for example
  `sqlite3 powercontext.db ".backup './backups/powercontext.db'"`. Verify the copy with
  `sqlite3 ./backups/powercontext.db "PRAGMA integrity_check"`, protect it like the source database, and test reopening it.
- OceanBase: enable tenant data backup and log archiving according to the deployed OceanBase version, retain the backup
  destination and encryption material independently, and perform a restore drill into an isolated tenant. Use native
  backup for point-in-time recovery and the portable bundle for logical cross-backend migration.

## Current boundaries

Export streams database rows to a temporary NDJSON file. Validation stores only record identities in a temporary
on-disk index, and restore replays dependency levels as streams, so aggregate archive payloads are not retained in
process memory. Scope metadata is bounded by the manifest scope list. The command is not a remote Server API and never
puts deployment configuration or database credentials into the archive.
