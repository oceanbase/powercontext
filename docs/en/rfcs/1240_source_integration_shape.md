# Source Integration Shape

- Proposal Name: source_integration_shape
- Status: Proposed
- Start Date: 2026-08-25
- Tracking Issue: [oceanbase/powercontext#1240](https://github.com/oceanbase/powercontext/issues/1240)
- Related RFCs: [RFC 0019](0019_local_source_memory_runtime.md), [RFC 0020](0020_runtime_backed_memory_remote_access.md), and [RFC 0051](0051_experience_skill_artifact_families.md)

# Summary

PowerContext needs a Source contract for external systems whose objects change over time. A GitHub issue, Notion page,
Slack message, or Linear issue has a stable provider identity, but its current value is allowed to change. The value
already used by an Artifact must remain exact. This RFC therefore separates a mutable Source head from immutable
snapshots of the values observed at particular points in time.

The proposed model has two levels:

    Source: identifies the external object and points to its latest snapshot.
    Source Snapshot: preserves the exact value observed at one provider revision and capture time.

Artifacts, Candidates, Handoff records, and other exact-evidence consumers MUST cite an immutable Source Snapshot.
A provider URL or logical object ID alone is not sufficient evidence unless the provider guarantees that the referenced
revision is immutable and re-readable.

The default read path may use the latest snapshot for search and ingestion. Historical Artifact lineage MUST continue
to use the snapshot that was present when the Artifact was produced. This RFC defines the PowerContext boundary only;
connectors remain responsible for discovery, synchronization, checkpoints, retries, credentials, and provider-specific
change handling.

# Motivation

The current Source implementation uses one identity for both the logical object and its captured value. The relational
primary key is effectively (scope_id, source_type, source_id), and a second payload for that key is a conflict. This is
correct for an immutable capture, but it cannot represent a mutable Source head plus retained historical snapshots.

The current SourceRef also contains only source_type and source_id. Artifact lineage therefore identifies a logical key,
not the exact provider revision or payload that produced the Artifact. A later provider update could make the same
reference resolve to different content or become impossible to resolve.

The design must preserve these properties:

- the latest Source can change and be re-ingested without rewriting historical evidence;
- snapshots referenced by an Artifact remain readable after the provider changes or becomes unavailable;
- repeated delivery of the same snapshot is idempotent;
- connectors can use provider-native revisions such as a Git commit SHA, Notion edit marker, Slack message timestamp,
  or file SHA-256;
- existing captured text remains useful without forcing every integration through an untyped payload;
- Source-specific adapters can participate in persistence, Runtime, transport, and evidence projection;
- connector synchronization does not become part of the Source model.

# Proposed model

## Source head and snapshots

The public model should distinguish a mutable Source head from an immutable snapshot reference. Names are illustrative:

    {
      "source_type": "github-issue",
      "source_id": "oceanbase/powercontext#1240",
      "snapshot_id": "snap_01J..."
    }

The mutable Source head contains at least:

- source_type and source_id: stable logical identity;
- latest_snapshot_id: the snapshot used by the current read and ingest path;
- locator and provider metadata needed by a connector.

A snapshot contains at least:

- snapshot_id: PowerContext identity of the immutable snapshot;
- source_type and source_id: the stable logical object identity;
- provider_revision: provider-native revision, when available;
- materialization: captured or referenced;
- content_hash: hash of the canonical observed value;
- captured_at: PowerContext capture timestamp;
- payload: canonical captured value when materialized;
- locator: provider URL or provider-specific locator when useful.

snapshot_id MUST identify one immutable payload. Reusing it with a different canonical payload is a conflict. A changed
provider revision creates a new snapshot and advances latest_snapshot_id; it MUST NOT update the payload of a snapshot
already referenced by an Artifact. Equal canonical payloads MAY reuse a snapshot when provider provenance is equivalent.

SnapshotRef is the exact-evidence citation boundary. During migration, a legacy SourceRef without snapshot_id may resolve
only to the one immutable payload represented by an existing legacy row. Newly created exact evidence MUST include
snapshot_id. A two-part reference MUST NOT silently resolve to the latest snapshot when that could change historical
Artifact lineage.

## Capture, Ref, and Hybrid

Capture is the default for evidence that enters an Artifact lineage. It stores the canonical value and hash locally as a
snapshot, so provider availability and future edits cannot change historical evidence. Connectors may capture every
observed change or only changes admitted to the PowerContext ingestion boundary; once a snapshot is referenced by an
Artifact, it is retained according to the retention policy.

Ref is allowed only when a provider revision has a documented immutable and re-readable contract. A mutable URL, object ID,
or current updated_at value is not enough. If a Ref cannot be resolved with the recorded revision, the snapshot is
unavailable and MUST NOT be silently replaced by the provider current value. An unavailable Ref cannot satisfy an
Artifact's exact-evidence requirement unless it is first materialized as a retained snapshot.

Hybrid stores both the provider locator/revision and the canonical captured value in the snapshot. It is preferred when
external traceability matters, provider reads are expensive, or a connector may reconcile later changes.

The resulting flow is:

    connector discovers source
      -> new snapshot with provider revision and hash when the value changes
      -> advance Source.latest_snapshot_id
      -> Artifact lineage cites the snapshot used for generation

## ContentSource and the content API

ContentSource remains one concrete built-in captured-text Source. Its current behavior is effectively a single immutable
snapshot per source ID; the future model should allow a new snapshot when the same logical ID receives new content.
POST /v1/sources/content remains the compatibility and minimum-ingestion path for callers that already have text. It is
not the universal representation of GitHub, Notion, Slack, Linear, or other provider objects.

Additional integrations should define typed Source and capture models. They may reuse common snapshot persistence,
hashing, idempotency, and citation machinery while keeping provider-specific fields in their typed payload.

## Connector boundary

Connectors own discovery, provider authentication, cursors, checkpoints, polling, webhooks, retries, rate limits, and
conversion of provider responses into typed snapshots.

PowerContext owns Source and snapshot identity validation, durable snapshot storage and idempotency, exact reads by
snapshot reference, scope isolation, Artifact foreign-key integrity, evidence projection, and citation validation.

This division follows the useful part of TencentDB-Agent-Memory's ISourceFetcher and SourceFetcherRegistry: fetchers route
provider protocols and return a provider version, while the core stores metadata and serves memory operations. PowerContext
additionally retains immutable evidence because Artifact lineage needs stronger replay guarantees.

# TencentDB-Agent-Memory research

This RFC is informed by [TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory):

- MemoryKnowledge routes source protocols through ISourceFetcher and SourceFetcherRegistry.
- Git synchronization returns a commit hash and stores repo_url, branch, version, and last_sync_at.
- Wiki source files use filename plus sha256 for incremental change detection and track ingest status separately.
- Wiki and CodeGraph maintain asset-level version counters and audit rows for sync lifecycle events.
- MemoryCore separates knowledge metadata from content/indexing and uses versioned records for memory evolution.

These patterns are useful for connector boundaries, provider revision metadata, content hashing, incremental sync, and
operational audit. TencentDB-Agent-Memory treats a changed hash as a reason to pull and ingest the new value; retaining
the prior source text is not its primary contract. PowerContext has a different requirement: when an Artifact already
cites that text, the prior payload must remain available after the current Source changes. PowerContext therefore adopts
the metadata and fetcher separation, but adds a retained immutable snapshot for Artifact citations.

References:

- [SourceFetcher types](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/source-fetcher/types.ts)
- [SourceFetcher registry](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/source-fetcher/registry.ts)
- [Git fetcher](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/source-fetcher/git-fetcher.ts)
- [Wiki source index](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/engines/wiki/index-db.ts)
- [Knowledge metadata schema](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/db/client.ts)
- [CodeGraph service](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/store/code-graph-service.ts)

# Persistence and Runtime impact

The implementation is expected to introduce a mutable Source/current-head representation alongside an immutable snapshot
table. Artifact lineage should reference the immutable snapshot key rather than only source_type/source_id. The exact
migration shape is left to implementation, but it MUST preserve old captured Sources and reject payload replacement under
an existing snapshot identity.

A new typed Source must register with Source resolution and exact reads, persistence encoding and decoding, Runtime
composition where applicable, HTTP/client mapping if exposed, evidence projection, citation validation, and focused
persistence and end-to-end tests. Evidence projectors SHOULD use adapter capabilities or a registry instead of accumulating
ContentSource-specific branches in Runtime composition.

Existing ContentSource captures remain readable. Existing POST /v1/sources/content remains idempotent for its current
identity/payload contract. Legacy two-part references require an explicit migration rule and MUST NOT silently mean the
latest snapshot when that could change historical Artifact lineage. OpenAPI changes follow agreement on this identity
and legacy behavior.

# Alternatives considered

## Keep the current stable Source key

This keeps the smallest API, but provider changes remain conflicts and multiple snapshots cannot be cited. It is suitable
only for already-immutable captures.

## Use only provider references

This minimizes storage, but exact evidence depends on provider retention, permissions, availability, and historical-read
semantics. It cannot satisfy replay for arbitrary integrations.

## Make every provider value a ContentSource

This reduces public types but moves provider semantics into untyped metadata, weakens validation, and makes evidence
projection provider-blind. It is not a durable extension boundary.

## Store only a local revision

A local revision helps ordering and cursors but does not prove which provider state was observed. It complements, rather
than replaces, provider revision and content hash.

# Rollout and validation

The first bounded validation should cover one mutable provider object and one immutable provider revision. A GitHub
issue/commit or Git repository commit is a practical candidate. It should demonstrate:

1. repeated delivery of one provider revision is idempotent;
2. a later provider revision creates a new snapshot and advances the current head without replacing the old snapshot;
3. current search and ingestion use the new snapshot;
4. an Artifact still cites and reads the old snapshot after the provider changes;
5. a missing or unverifiable Ref is rejected rather than silently refreshed;
6. evidence projection and HTTP/client mapping preserve the exact snapshot reference.

# Open questions

- Should snapshot_id be opaque and generated by PowerContext, content-addressed, or expose both forms?
- Should SourceRef grow a snapshot_id, or should a separate SnapshotRef be introduced?
- Which provider revision guarantees qualify for Ref-only materialization?
- What retention and garbage-collection policy applies to snapshots no longer referenced by an Artifact?
- Should large captures use an external blob store while retaining a canonical hash and durable locator?
- Should logical Source heads be public API, or remain connector/persistence metadata initially?

# Decision requested

Approve the mutable Source head plus immutable snapshot model, snapshots as the exact-evidence boundary, Capture as the
default materialization, Hybrid as the preferred traceable form, and ContentSource as one concrete capture type rather
than a universal provider model. After approval, implementation can define the concrete schema, migration, OpenAPI
fields, retention rules, and one bounded connector validation.
