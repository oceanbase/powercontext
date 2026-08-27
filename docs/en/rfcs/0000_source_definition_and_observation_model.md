- Proposal Name: `source_definition_and_observation_model`
- Start Date: 2026-08-27
- Related Discussion: [oceanbase/powercontext#1240](https://github.com/oceanbase/powercontext/issues/1240),
  [oceanbase/powercontext#1363](https://github.com/oceanbase/powercontext/issues/1363)
- Related Design: [oceanbase/powercontext#1345](https://github.com/oceanbase/powercontext/pull/1345)
- Related RFCs: [RFC 0002](0002_core_sdk_product_model.md), [RFC 0014](0014_memory_layer_design.md),
  [RFC 0019](0019_local_source_memory_runtime.md), [RFC 0048](0048_handoff_artifact.md)

# Summary

This RFC defines the standard Source model and the contract for defining additional Source types.

A Source belongs to exactly one Scope. Within that Scope, a `SourceKey` identifies one logical source and a
`SourceRef` identifies one immutable observation of that source. Advancing the current observation, observing a
deletion, changing an external locator, or disconnecting a Connector does not alter an earlier observation or move
it to another Scope.

A Source Definition gives one stable Source type its value schema, provenance schema, identity rules, observation
rules, materialization contract, canonicalization, and compatibility policy. Definitions are registered explicitly
and remain fixed for the lifetime of a composed Runtime. Persistence, transport, and Artifact consumers route by the
stable definition name and version rather than by a concrete Python class.

Materialization identifies the authority used to resolve an exact observation. A captured observation is resolved
from the canonical value retained by PowerContext. A referenced observation is resolved from an immutable external
revision. An external locator, modification time, ETag, or current-provider read does not by itself satisfy the
referenced contract.

`ContentSource` remains a simple captured-text Source. Its caller-stable identity and immutable-payload conflict rule
make it useful for one-shot content capture, but it is not the general external integration model. A document Source
serves as the first conformance validation of the standard definition contract, not the definition of that contract.

This RFC defines semantics and conformance. It does not define a Connector runtime, plugin discovery mechanism,
storage schema, public transport operation, synchronization algorithm, scheduler, or document implementation.

# Motivation

`ContentSource` and `POST /v1/sources/content` provide captured-text ingestion. The caller
chooses one `source_id`; replaying an identical payload is idempotent, while reusing that identity with a different
payload is a conflict. This gives exact evidence only when the caller treats the identity as immutable.

External systems usually expose a different lifecycle. A wiki page, issue, object, message, or file has one logical
identity but may produce several values over time. The external object can be renamed, revised, deleted, restored,
or become temporarily unreadable. Artifacts that used an earlier value must continue to cite that exact evidence.
The two-part `(source_type, source_id)` Source reference cannot express both the stable logical object and its immutable observation
without making every integration invent a composite `source_id`.

The extension boundary is also incomplete. A Source adapter binds a native input class to a concrete
Source class and a read result, while the built-in Runtime and relational persistence assemble a fixed adapter set.
This does not state the durable rules an independently defined Source type must follow across identity, persistence,
transport, and Artifact evidence.

The standard model must answer five questions without assigning them to one identifier:

1. Which Scope owns this evidence?
2. Which logical external or internal source does it describe?
3. Which exact observed value did an Artifact use?
4. Where does PowerContext read that exact value from?
5. Which definition gives the value and provenance their meaning?

Connector concerns are adjacent but distinct. Discovery, credentials, filtering, checkpoints, retries, provider
change handling, and deletion detection decide which observations are submitted. They do not define Source identity,
weaken exact evidence, or change Scope ownership.

# Guide-level explanation

## Domain model

Read the model by establishing ownership first, then logical identity, exact observation, materialization authority,
and type semantics:

| Concept | Representation | Question answered |
| --- | --- | --- |
| Ownership | Scope | Where does the Source belong? |
| Logical identity | `SourceKey` | Which continuing source is this? |
| Exact evidence | `SourceRef` | Which immutable observation is cited? |
| Read authority | materialization | Where is that exact value resolved? |
| Type semantics | Source Definition | How are value, provenance, and identity interpreted? |
| Acquisition | Connector or direct caller | How are new observations found and submitted? |

These responsibilities form one direction of dependency:

```text
Connector or direct caller
          |
          v
Source Definition
          |
          v
Scope-owned Source history
          |
          +---- mutable head selection
          |
          `---- exact SourceRef ----> Artifact evidence
```

A Connector can use one Source Definition, several Connectors can use the same Definition, and a direct caller can
submit a Source without a Connector. Connector identity therefore does not become Source type identity.

## Scope ownership

Every SourceKey and observation belongs to exactly one Scope. Scope ownership is not inferred from an external
workspace, path, repository, provider account, Connector instance, or Source locator. Those values may contribute to
binding or provenance, but they do not allocate or replace `scope_id`.

The fully qualified logical identity is:

```text
SourceKey = (scope_id, source_type, source_id)
```

The fully qualified exact identity is:

```text
SourceRef = (scope_id, source_type, source_id, observation_id)
```

A scope-bound operation may obtain `scope_id` from its fixed request binding instead of accepting it as an arbitrary
argument. The durable resolved reference still retains the owner Scope so that evidence remains unambiguous after
publication, reporting, or export.

Changing a Scope Parent, Context References, an Agent binding, or an observation selection changes no SourceKey or
SourceRef. Publishing an Artifact across Scopes preserves the original Scope and exact SourceRef in provenance. It
does not move or implicitly copy the Source history.

## Logical Source and immutable observation

`source_id` names a logical source within one `(scope_id, source_type)` namespace. Its meaning is defined by the
Source Definition. It may correspond to a provider object ID, a stable import identity, or another normalized key.
It must not silently change when a new value is observed.

`observation_id` names one immutable observation under a SourceKey. It is opaque to generic PowerContext components.
It may be derived from a provider revision, a canonical value digest, or a definition-specific combination. It does
not imply an integer sequence, timestamp order, or ancestry.

The following invariants apply:

- one `(SourceKey, observation_id)` identifies one canonical observation forever;
- re-observing the same canonical observation is idempotent;
- a different canonical observation cannot reuse an observation ID;
- one SourceKey may have several observations with the same value digest when their identity-bearing provenance is
  different;
- observations with the same value digest are not automatically the same logical Source; and
- an Artifact cites an exact SourceRef, never a moving SourceKey or `latest` observation.

For example, a document update retains one SourceKey and produces another SourceRef:

```text
SourceKey(scope-a, document, provider-page-42)
|-- SourceRef(..., observation-1)  "Initial decision"
`-- SourceRef(..., observation-2)  "Revised decision"
```

An Artifact derived from `observation-1` continues to cite it after `observation-2` becomes current.

## Source Definition

A Source Definition is the durable semantic contract for one `source_type`. It declares:

- a stable definition name and version;
- the Source value and typed provenance shapes;
- Source ID normalization and equality;
- observation ID normalization and equality;
- identity-bearing fields and non-identifying annotations;
- canonical bytes and the value digest algorithm;
- supported materialization modes and exact-read requirements;
- limits and validation failures; and
- compatibility rules for older definition versions.

A Definition resolves definition-native input into a canonical observation and reads the definition-owned value from
an exact persisted observation. Resolution does not select a Scope, mutate a catalog, advance a head, or discover
external objects. Reading does not resolve `latest` or substitute another observation.

Definitions are explicit and typed. A new integration must not simulate a new Source type by placing an undocumented
schema inside `ContentSource.metadata`. Provider-specific provenance may extend a Definition's declared schema, but
fields that affect identity, exactness, or compatibility must be named by the Definition.

## Materialization authority

Materialization answers where the value returned for an exact SourceRef comes from:

| Materialization | Authority | Required guarantee |
| --- | --- | --- |
| `captured` | Canonical value retained by PowerContext | The retained value matches the observation digest |
| `referenced` | Immutable external revision | Re-reading the reference returns the same canonical value and digest |

A captured Source may retain an external locator, provider revision, and digest as provenance. It remains captured
because the retained value is the read authority. This covers the useful part of a hybrid design without creating a
third mode with ambiguous fallback semantics.

A Definition can use referenced materialization only when the external system and its reader can address immutable
historical values. Reading the current value at a path, page ID, issue ID, or URL is not sufficient. Modification
times and ETags may contribute to provenance or conflict detection, but a Definition must state whether the provider
guarantees that they address an immutable value.

When the referenced value is unavailable or its digest differs, exact resolution fails. PowerContext does not return
the current provider value, a stale cache entry, or another observation. A provider that cannot satisfy this rule
must use captured materialization or reject the observation.

## Current head and deletion

A Source history is immutable; its current head is a mutable catalog selection. The head can select one exact
SourceRef or record that the logical Source was positively observed as deleted. The head is useful for current-state
queries and later acquisition, but it is not evidence and cannot appear in an Artifact citation.

Advancing or deleting a head changes no observation. A timeout, permission failure, incomplete listing, unavailable
Connector, or disconnect is not positive deletion evidence and does not change the head. A Source Definition may
define a tombstone value only when deletion itself is meaningful Source evidence; a generic head deletion does not
fabricate one.

## ContentSource

`ContentSource` remains the neutral captured-text path defined by RFC 0019. Its caller chooses an identity that can be
committed once with one canonical payload. The persistence conflict rule makes an accepted ContentSource exact, but
it does not provide a separate logical Source lifecycle.

The standard model treats this as a valid single-observation Source implementation:

- the existing identity remains immutable;
- an identical replay remains idempotent;
- different content under the same identity remains a conflict;
- references that resolve ContentSource remain exact and unchanged; and
- no mutable head or multi-observation behavior is inferred from metadata.

ContentSource is suitable for prompts, explicit text capture, import records, and other cases where the caller
already owns an immutable identity. Integrations that observe one logical object over time should define or reuse a
multi-observation Source type instead.

## Document Source as the first validation

The first validation Source represents a logical document with immutable observations. It is deliberately separate
from ContentSource. Its conformance scenarios require:

- one logical document retaining its SourceKey across updates;
- each changed canonical document observation receiving an exact SourceRef;
- an unchanged observation replaying idempotently;
- an earlier observation remaining readable after update or deletion;
- provider locator changes not rewriting an accepted observation;
- incomplete discovery or permission loss not becoming deletion; and
- captured materialization when the provider cannot resolve immutable historical revisions.

The validation does not make documents the universal Source value. Issues, messages, traces, code states, reviews,
and other Source types can define different values and provenance while following the same identity and observation
contract.

# Reference-level explanation

## Source identity contract

`scope_id` is the ownership boundary defined by the Scope organization design. `source_type` is the stable Source
Definition name. `source_id` is a non-empty, normalized identifier whose equality and bounds are declared by that
Definition.

Source identity is Scope-local. Two Scopes may contain equivalent external material without sharing ownership or
identity. A Definition may include a stable external instance or connection discriminator in its `source_id` rules
when required to prevent collisions, but the discriminator does not replace `scope_id`.

Renames are definition-specific. A provider object ID may preserve SourceKey across locator changes. A path-derived
identity normally treats a rename as one logical deletion and one creation. A Definition must not claim rename-stable
identity when its provider and acquisition path cannot prove it.

## Observation contract

An observation contains these standard fields:

```text
SourceObservation
|-- source_key
|-- observation_id
|-- definition_version
|-- materialization
|-- value_digest
|-- provenance
`-- definition-owned value or exact external reference
```

`value_digest` uses SHA-256 over the canonical bytes declared by the Definition and is encoded as
`sha256:<lowercase-hex>`. For structured values, the Definition specifies a deterministic canonicalization. The
digest verifies value equality; it does not replace SourceKey or observation identity.

The canonical observation contains every field that the Definition says affects identity or exact meaning.
Operational facts such as a retry count, last scan time, or processing status are not Source value and do not change
observation identity. If a timestamp or provider attribute affects provenance meaning, the Definition must classify
and canonicalize it explicitly.

## Source reference contract

A SourceRef identifies an exact observation and includes its owner Scope. It never accepts an absent observation ID,
`latest`, a head version, or a current provider locator.

Within a scope-bound operation, a compact local representation may omit a repeated `scope_id` only while the current
Scope is fixed and the resolved durable value restores it. Any reference that crosses a Scope boundary, leaves the
Runtime, or enters durable cross-Scope provenance carries the owner Scope explicitly.

Reference resolution verifies all four identity components and the stored observation's definition version and
digest. Failure to resolve the exact observation is distinct from the logical Source being deleted, the head having
advanced, or the Connector being unavailable.

## Definition registration contract

A composed Runtime has one explicit Definition registry. Registration validates stable Definition name and version,
declared value and provenance schemas, identity rules, materialization support, and read behavior. Two incompatible
Definitions cannot claim the same `(source_type, definition_version)`.

Registration is fixed for the Runtime lifetime. Catalog decoding, Source reads, and Artifact validation use the same
registry view. A persisted observation whose Definition is unavailable remains stored but cannot be interpreted or
advertised as readable. It is not decoded into a base Source with discarded fields.

Definition discovery and registration are separate. A package entry point or another discovery mechanism may report
available Definitions, but installation does not imply activation. This RFC does not select entry
points, a central settings format, pluggy, or a Connector marketplace.

## Definition compatibility contract

The Definition name remains stable across compatible schema evolution. Each persisted observation records the
Definition version used to validate and canonicalize it. A newer Definition version must either declare how it reads
an older observation without changing its canonical meaning or coexist with a reader for the older version.

A Definition change is incompatible when it changes SourceKey equality, observation equality, canonical value bytes,
provenance meaning, or materialization guarantees for an accepted observation. Such a change requires a new
Definition version and cannot rewrite existing SourceRefs.

Renaming a Definition creates a new `source_type`. Reclassifying an existing observation under another Definition is
an explicit derivation with provenance, not an in-place migration of identity.

## Connector boundary

A Connector owns provider interaction: discovery, credentials, filtering, checkpoints, retries, rate limits,
provider change handling, and positive deletion detection. It submits definition-native inputs against a Scope
binding and receives exact accepted SourceRefs.

A Source Definition owns semantic normalization: logical identity, observation identity, canonical value,
provenance, materialization validity, and exact read. A Connector cannot override those rules. If the intersection of
provider capabilities, Connector behavior, and Definition requirements cannot satisfy a selected materialization,
the observation is rejected or captured under a valid mode.

```text
provider capabilities
  intersect Connector behavior
  intersect Source Definition requirements
  = valid Source observation
```

This RFC does not define Connector lifecycle interfaces or require that a Connector run inside the PowerContext
Server. Direct imports, local tools, hosted Connectors, and external synchronization services can submit the same
definition-native observations.

## Artifact evidence and cross-Scope delivery

An Artifact revision records exact SourceRefs used directly by its computation. Advancing a Source head does not
change existing Artifact lineage. Recalculation against a newer observation produces a new Artifact revision rather
than rewriting prior evidence.

Sources remain in their producing Scope. A Context Reference may expand a read selection according to the Scope
organization contract, but it does not change Source ownership. Exact Artifact publication across Scopes retains the
origin Scope and exact SourceRef in lineage. Publishing an Artifact does not publish every Source in its origin Scope.

If an application deliberately captures the same external value into another Scope, the target receives a new
Scope-owned Source observation. Its provenance may cite the origin Scoped SourceRef, but the original Source is not
moved and the two SourceKeys are not made identical.

## Conformance

A Source Definition can be supported only after its mandatory contract passes conformance scenarios for:

- identity normalization and collision rejection;
- identical observation replay;
- conflicting payload rejection for one observation ID;
- several immutable observations under one SourceKey;
- exact old-observation reads after head advancement and deletion;
- digest verification for captured and referenced values;
- referenced-value unavailability and mutation;
- Scope isolation and explicit owner preservation;
- Definition version compatibility and unavailable-definition behavior; and
- explicit registration conflict handling.

The first document validation additionally covers provider update, locator change, positive deletion, incomplete
discovery, permission loss, and providers without immutable revision reads. Passing document validation proves the
standard contract can support one document Source; it does not add document fields to the standard Source model.

# Drawbacks

- Separating SourceKey, SourceRef, Source head, and Definition version introduces more concepts than one immutable
  `(source_type, source_id)` pair.
- Exact SourceRefs retain owner Scope and observation identity, increasing lineage payload size.
- Definition authors must specify canonicalization, provenance, and compatibility instead of relying on arbitrary
  metadata.
- Referenced Sources are unavailable for providers that expose only current values, so some integrations must retain
  captured data.
- Explicit registration requires deployment coordination before a persisted custom Source can be read.

# Rationale and alternatives

## Extend ContentSource into the general integration model

Adding provider fields to ContentSource would preserve the `POST /v1/sources/content` capture API, but it would keep logical identity,
observation identity, and provenance inside caller conventions. Different integrations would encode incompatible
schemas in metadata, and non-text Source values would still need another model. ContentSource remains a useful
single-observation implementation instead.

## Use one opaque Source envelope

A universal JSON payload would make persistence and transport uniform, but would move schema validation and
compatibility into runtime conventions. Definition-owned typed values and provenance make the extension boundary
reviewable and allow consumers to reject unsupported Source types before interpretation.

## Put an observation digest inside source_id

An integration can preserve the two-part SourceRef shape by composing logical identity and digest into `source_id`. This
makes immutable capture possible but hides the continuing logical Source from the catalog. Updates, current-head
selection, deletion, and provider identity then become integration-private conventions. The standard model represents
both identities directly.

## Make SourceRef logical and add a separate ObservationRef

Two public reference types would make SourceRef logical, but Artifact evidence would need
to reject SourceRef and accept only ObservationRef. Defining SourceRef itself as exact follows the existing ArtifactRef
principle that durable lineage references immutable state.

## Add hybrid materialization

A third mode that sometimes reads externally and sometimes falls back to captured data obscures which value is
authoritative and which failures are visible. A captured observation can retain a complete external reference as
provenance. A referenced observation either resolves exactly or fails.

## Let Parent or Connector identity own Sources

Scope Parent is organization, and Connector identity is acquisition provenance. Neither is a durable ownership
boundary. Using either would conflict with the Scope organization contract and would make reorganization or
Connector replacement change Source identity.

# Prior art

- The [Scope organization and Agent integration design](https://github.com/oceanbase/powercontext/pull/1345) separates
  Scope ownership, read sharing, organization, delivery, and observation. This RFC applies the same separation to
  Source ownership, identity, exact evidence, and acquisition.
- [Apache OpenDAL OFS RFC-0016](https://github.com/apache/opendal-ofs/blob/main/rfcs/0016_filesystem_architecture.md)
  separates namespace authority from access frontends and forbids a frontend from advertising guarantees that the
  underlying layers cannot enforce. Source materialization follows the same authority rule.
- [opendalfs](https://github.com/fsspec/opendalfs) exposes OpenDAL services through the fsspec interface and is a
  candidate acquisition layer for the first filesystem-backed document Connector. Its paths and file metadata do
  not define Source identity or immutable revision semantics. A backend read can satisfy referenced materialization
  only when the complete stack addresses and verifies an immutable revision; otherwise the document is captured.
- DataHub stateful ingestion separates connector checkpoints and stale-entity detection from emitted metadata
  identity. Airbyte treats connector state as an opaque recovery boundary rather than record identity.
- OpenMetadata separates the Source that emits records from connection checks, workflow status, and the sink.
- Nowledge Mem's TiddlyWiki importer uses stable logical IDs, canonical payload digests, source revalidation, and
  per-item outcomes. Those behaviors motivate the document validation without defining the standard Source value.

# Unresolved questions

- Must every durable SourceRef carry `scope_id` directly, or may a canonical scoped envelope contain a local exact
  SourceRef while preserving the same fully qualified identity?
- Which Source Definition versions must a Runtime retain simultaneously before a Definition can be considered
  supported?
- Should Source head deletion be one common catalog state, or should the first standard contract expose only an
  active exact head and leave deletion entirely to Connector state?
- Which normalized value categories, if any, should Artifact families share without requiring them to understand a
  complete definition-owned value schema?

# Future possibilities

Connector lifecycle, checkpoints, run status, and explicit plugin discovery require a separate contract. Document
ingestion supplies a conformance case for that contract without changing Source identity or materialization semantics.

Definitions may advertise optional projections, such as text, structured records, or binary attachments, for
Artifact families that cannot consume the complete native value. Projection identity and digest rules require their
own contract and do not weaken the original Source observation.

Retention policies may reclaim captured values only after defining how exact Artifact evidence reports unavailable
content and how legal or user-requested deletion interacts with immutable lineage. A Source head deletion alone does
not authorize evidence removal.
