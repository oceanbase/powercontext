---
title: "RFC 2573: Atomic Memory input-budget recovery"
description: Durable blocking and conditional recovery of complete extraction inputs that exceed their local budget.
---

# Atomic Memory input-budget recovery

Tracking issue: [#2573](https://github.com/oceanbase/powercontext/issues/2573).
This extends the processing admission contract in [RFC 1515](1515_artifact_processing_supervisor.md), following the
block and receipt semantics of [RFC 2018](2018-topic-memory-recovery.md). Cross-family recovery remains tracked in
[#2128](https://github.com/oceanbase/powercontext/issues/2128).

## Failure and scope

Atomic Memory checks effective extraction instructions plus the complete rendered evidence before generation.
Multiple journal positions can still reduce their persisted window. At a window of one position, a local
`atomic_memory.extract` budget rejection becomes a durable `input_budget_exceeded` block. The cursor position,
Source material and processing intent remain intact. No extraction request is sent and no partial Memory is published.

This policy applies to the automatic Source-window extraction path, including explicit flush of that path.
Reconcile budget rejections, including those at the minimum comparison unit, retain their existing reduction and retry behavior;
durable reconcile recovery is outside this contract. Provider errors, output validation and timeouts retain their own recovery behavior. A Source block does not prohibit
independently queued Dream requests. Other Scopes and independently scheduled families remain runnable.
Later Sources behind the same cursor wait for recovery; skipping evidence requires a separate completeness contract.
Oversized-evidence fragmentation remains subject to [#2074](https://github.com/oceanbase/powercontext/issues/2074).

## Durable identity and admission

`pc_memory_work_blocks` stores one immutable rejection snapshot with a server-generated block ID, Scope and binding,
exact one-position journal interval, the failing stage, the SHA-256 of the effective instructions and rendered input,
estimated input tokens, effective budget, estimator ID and first rejection time. It contains no Source bodies, prompts, model
output or credentials. `input_fingerprint` is the complete extraction budget input digest and correlates the rejected
extraction snapshot; it is not an equality condition between rejected and retried inputs. Correcting prompts or rendering
configuration may change the complete input. Retry checks the current complete input against the budget and retains the
original rejection digest. The digest is recomputable when the Source, prompt, renderer and dependencies are unchanged
and rendering is deterministic. Estimation and rejection hashing consume the same rendered string; the digest never replaces
the block identity, frontier or cursor-generation checks. Reconcile failures carry no extraction fingerprint.
An active marker and a unique frontier key admit one current block. Only retry acceptance changes the marker and writes
its acceptance time and processing request generation. Accepted receipts have no automatic expiry, including after
successful processing or disabling generation. Successful work creates no recovery record.

The failing Worker takes the same Source-head lock as Atomic publication, checks its execution fence and uses the selected cursor's generation CAS
before persisting the block. Publication and recovery retain cursor CAS protection. A stale Worker cannot block or publish
over a replaced frontier. Scheduler admission reads only cursor/block metadata; it never projects evidence or estimates
input. Ordinary scheduling, new Sources, flushes, configuration changes and restart do not clear a block.

## Management and recovery

All three management operations use POST and current-Scope authorization:

| Path | Permission | Behavior |
| --- | --- | --- |
| `/v1/atomic-memory/processing/status` | `scope.read` | Read cursor, head and the current block without changing state. |
| `/v1/atomic-memory/processing/blocks/get` | `scope.read` | Read one exact retained rejection and its receipt. |
| `/v1/atomic-memory/processing/retry` | `scope.admin` | Conditionally accept recovery of an observed block ID. |

A retry of an accepted ID returns the original receipt before consulting generation availability or today's budget.
Otherwise, the processor verifies the block's current cursor, authorizes its complete Source selection, binds the current
prompt snapshot, projects the evidence and checks the current complete input budget without calling a model. An input
that still cannot fit returns HTTP 409 and retains the block. A blocked Source that no longer qualifies for generation
returns HTTP 409 `atomic_memory_block_source_ineligible` with its block ID and Source reference, and leaves the block
untouched. Source-read authorization runs on the original window before eligibility is judged, so a retry without read
access fails with an authorization error and never reveals the Source reference. Generation unavailability returns 503.

After successful preflight, one transaction locks the frontier, rereads the receipt and cursor generation, rechecks Source
access and eligibility, fences the cursor generation, requests pending processing and its intent, and records acceptance. Concurrent
requests converge on one receipt; cancellation or rollback commits neither intent nor receipt. An unretried stale block
returns 409; an unknown or cross-Scope block returns 404 after authorization. A lost response is recovered by repeating
the same block ID. When automatic processing is disabled, accepted work remains pending for explicit flush. A later Worker always rechecks its complete input; changing configuration after acceptance cannot
bypass admission. A new failure after an accepted recovery creates its own block and requires its own explicit recovery.

An ordinary `/v1/memory/flush` encountering this block returns HTTP 409 with its block ID. Python SDK methods mirror the
management operations. CLI commands are:

```bash
powercontext atomic-memory status --scope-id SCOPE
powercontext atomic-memory block --scope-id SCOPE --block-id BLOCK_ID
powercontext atomic-memory retry --scope-id SCOPE --block-id BLOCK_ID
```

Inspect the effective input budget and prompt, and ensure the model can accommodate the complete request and output
reserve before increasing a limit. Local admission is not a guarantee of provider acceptance or generation success.
Recovery preflight additionally requires current Source-read and Memory-create access; `scope.admin` does not grant those permissions. Embedded Runtime callers own their management authorization; HTTP enforces the permissions above. MCP is unchanged.

## Status and diagnostics

Dashboard automatic-processing status reads the persisted block independently of process-local Supervisor observations.
It exposes the block ID, failure stage, rejection-time estimate and budget, estimator and supported recovery action. These
values describe the original rejection, not the current configuration. No attempts, provider requests or billed-token counts
are invented. Existing safe diagnostic fields cross the Worker boundary through `ArtifactProcessingBlock`. Unchanged metadata
probes do not repeat processing failures. `atomic_memory.retry.wake_failed` reports a failed best-effort wake after committed
acceptance; periodic discovery can still find the durable intent, and the receipt remains accepted.

## Storage compatibility

The additive table belongs to cumulative Server migration `000`, after frozen head `v1_6_0`, for SQLite and MySQL-compatible
backends and both registered source paths. Existing released databases require explicit managed migration before startup;
startup does not add the table. Earlier frozen resources remain unchanged. Databases created by unpublished `000` must be
rebuilt when the draft changes. Backups cover the registered block table together with Sources, cursors, pending work,
intents and leases. There is no historical backfill from logs: an existing oversized frontier blocks on its first
post-upgrade execution. Old and new writers must not run concurrently.

## Retention and capacity

Accepted receipts and rejection snapshots have no expiry, no TTL and no automatic cleanup. The table has no fixed upper
bound, so capacity planning is the historical count of distinct block IDs. A retry that fails again creates a new block ID
and adds a row; successful windows, polling, status reads and duplicate accepted-ID retries add none. Accepted rows keep
their filled acceptance columns and remain in the table and frontier index; `active IS NULL` rows are neither reclaimed nor
moved. Manually deleting rows breaks the exact-ID replay promise.

Each block costs table storage plus the primary-key and frontier indexes. Measurements on 2026-11-28 used Linux x86_64,
Python 3.11.15 and SQLite 3.50.4 with the current extraction-only fields: 36-character UUIDs, representative 30-character
Scope IDs, the actual 20-character `memory-source-window` binding, and near-limit 256/128-character identifiers with a
128-character estimator ID. Approximate single-Scope costs at 100,000 rows follow; both indexes include accepted
`active IS NULL` rows:

| Identifiers | State | Table B/row | Primary-key B/row | Frontier B/row | Total B/row |
| --- | --- | --- | --- | --- | --- |
| Representative | active | ~256.7 | ~50.1 | ~74.1 | ~380.8 |
| Near limit | active | ~821.2 | ~50.3 | ~455.1 | ~1326.7 |
| Representative | accepted | ~293.4 | ~50.1 | ~74.1 | ~417.5 |
| Near limit | accepted | ~821.2 | ~50.5 | ~455.1 | ~1326.9 |

The script covers 1,000, 10,000 and 100,000 rows, active/accepted and representative/near-limit fields separately.
A mixed workload with 100 Scopes also measures distinct frontiers and repeated blocking at the same frontier:
at 100,000 rows the totals are about 411.5 and 407.2 B/row respectively. Repeated-frontier mixed cases retain only
the last active block per Scope; earlier IDs are accepted receipts. Identifier lengths change the size; these values
are not upper bounds on row size.

Queries retrieve complete persisted rows. The script selects a hit from a real active row and verifies one match;
an absent Scope verifies a miss. Accepted-only data has no active hit, so that result is `null`. Exact-ID reads also
verify one match. Measured in-process warm-cache query medians were about 2.4–8.2 microseconds. Other regression
checks ran concurrently; background load was not isolated. Reports separately include whole-database allocated/free
pages, each index, in-memory backup time and backup row counts; timing an in-memory backup does not qualify recovery.
Reproduce with:

```bash
PYTHONPATH=src uv run python scripts/benchmark_memory_block_capacity.py \
  --counts 1000 10000 100000 --states active accepted \
  --field-modes representative near_limit
PYTHONPATH=src uv run python scripts/benchmark_memory_block_capacity.py \
  --counts 1000 10000 100000 --states mixed --scope-counts 100 \
  --histories distinct repeated
```

These are local SQLite observations using synthetic data and the current layout, not cold-cache measurements,
production capacity guarantees or native-backend capacity evidence. seekDB/OceanBase table and index occupancy,
backup/recovery and production workload targets require separate measurement.

Any future cleanup must preserve exact-ID replay through a compatible contract, for example a minimal receipt that still
answers exact reads, an archive tier that answers exact-ID reads and restores from backup, or an explicitly finite replay
horizon. An archive replaces online retention only when exact reads, backup restore and old-ID replay still work. Capacity must be re-measured when fields or indexes change.

## Acceptance

Verify persisted oversized evidence, zero generation at rejection, unchanged Source/cursor/publications, smaller-window
recovery, metadata-only admission, actual spawned Worker propagation, repeat scheduling and restart, unrelated Scope
progress, authorization, exact lookup, insufficient-budget refusal, concurrent recovery, cancellation rollback and
accepted-ID replay after processing or disabling generation.

SQLite and real embedded seekDB with a local scripted provider establish local handling. seekDB checks cover durable
blocking, Runtime reopen, concurrent recovery, cancellation rollback, isolation of old receipts from new blocks, and
creation of recovery storage and repeat application through the current cumulative Server migration. Native tests require
the explicit `POWERCONTEXT_TEST_MIGRATION_SEEKDB=1` opt-in and use independent temporary storage. These checks do not qualify
native upgrades from installed releases, real OceanBase services or external model providers; those acceptance checks still
require separate execution.
