---
title: DSH handoff preparation and recovery
description: The internal DSH delivery interface, package format, and recovery and retention rules.
---

# DSH handoff preparation and recovery

This component implements preparation and local delivery records for [RFC #2528](../rfcs/2528-dsh-plugin-ui-and-agent-handoff.md).
The authenticated DSH Host, target preflight, and launch services are separate. This component does not add a
browser route or enable delivery in the DSH UI on its own.

## Preparing a delivery

Construct `LocalDeliveryService` from `integrations/dsh/plugins/powercontext/src/handoff/delivery.ts` with an
absolute delivery directory, the Host's real `PreflightGate`, and a connection resolver. The shared types are
in `handoff/contracts.ts`. The Host supplies the authenticated owner and call cancellation signal in
`CallContext`. It verifies the selected session, message, inclusive event cutoff, directory, and workspace
observation time through the gate. The connection resolver must use the verified Server, connection
generation and Scope, or fail. Binding fields are identifiers; the resolver keeps credentials in memory.

The first request waits for preparation to settle. A concurrent repeat can return the same delivery in
`preparing`; use `get` to read its state. A request key belongs to one owner and one input. A replay with
different input fails. Equivalent requests on the same verified binding reuse the delivery. To replace a
result, supply a new request key and the old `supersedesDeliveryId` after explicit user selection. The old
result and receipt attempt remain available under their original ID.

| Input | Preparation |
| --- | --- |
| Inspected `workInput` | Validate the existing `CurrentWorkHandoff` fields, evidence and 32 KiB input limit; call `handoff_current_work` once. Save its full Prepared value and boundary receipt. |
| Configured generation and a trusted boundary reader | Capture bounded task evidence using the Source API. Call `prepare_handoff` once, validate the draft and send it unchanged to `finalize_handoff`. Save the complete result and the actual capture receipt. |
| Explicit exact Handoff Revision | Verify the exact `ArtifactReference` in the bound Scope and save that reference. The reference uses the existing integer `revision` field. |
| No usable generation or inspected fields | Report `needs_input`; ask for inspected work fields and prepare a new version. |

The Host's `BoundaryReader` supplies the selected reply and observations at that boundary. Its underlying
read must honor the requested limits; the collector also enforces 512 events including the selected reply,
256 KiB overall and 32 KiB for workspace evidence, subject to the lower Source and configured model input
limits. It preserves known test statuses, exact citations when supplied, revisions and working tree
observations. No observed check means the result is unknown. An explicit check that did not run is
different from a failure, interruption, or missing evidence. The reader must not run tests or replace an
unavailable workspace observation with the current directory state. For configured generation, use a
client configured for the intended generation deadline; the service caps the model call at 90 seconds.
There is no generation fallback or automatic milestone commit.

## Package integrity and recovery

The Host uses a private POSIX directory below its user data, with one owning Host process per directory.
Records and packages use `powercontext.dsh-delivery.v1` and are stored separately by delivery ID. The
service writes identity, binding and the `content` ACK source ID before dispatch. It persists the expected
size and file checksum before atomically publishing the complete package. Recovery verifies those bytes and
the original source, binding, receipt attempt and carrier before publishing `prepared`. An interruption
without a valid package becomes `unknown`; recovering from a Source or re-finalizing would not recover the
original package. A cancelled call before dispatch stops preparation. Once a call may have run, interruption
can leave its outcome unknown. Cancellation preserves a complete saved package.

`getForLaunch` checks authorization and package integrity. It returns the exact stored binding, selection,
path, checksum and ACK identity. The Prepared digest is the Core Work digest of the canonical Prepared
value. The package SHA-256 checks the bytes of the whole JSON file. Keep both values; neither can substitute
for the other. Export returns the full validated bytes. If a launch path will be consumed asynchronously,
use `withLaunchBundle` while reading or copying it into a verified location; explicit deletion and pruning
are blocked while that reader holds the path. Call `close` on Host shutdown.

The default capacity is 100 carrier slots or 50 MiB, with space reserved for in-flight preparations. The
service never evicts pending or pinned packages to make room. Owners can pin, export or explicitly delete
a delivery. Deletion leaves an idempotency tombstone for 30 days. Automatic package expiry requires the
future exact ACK verifier to supply confirmation for that receipt attempt. An unpinned confirmed package can
expire after seven days; its metadata is kept for 30 days after confirmation. Cleanup does not touch Server
records or write an acceptance state. Unsupported record versions are rejected and left on disk.

The Host assembly supplies the authenticated preflight and exact connection resolver, maps delivery errors,
and keeps launch attempts separate from delivery records. It accepts inspected input and exact Revisions.
Automatic drafting requires a trusted session evidence reader; the Host does not expose package deletion or
pruning on these routes. Native target launch and receipt verification require separate acceptance tests.
