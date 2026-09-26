- Proposal Name: `memory_capacity_contract`
- Start Date: 2026-09-23
- RFC PR: [oceanbase/powercontext#1718](https://github.com/oceanbase/powercontext/pull/1718)
- Tracking Issue: [oceanbase/powercontext#1718](https://github.com/oceanbase/powercontext/issues/1718)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md) and [RFC 1652](1652_memory_quality_and_lifecycle.md)
- Related work: [#1321](https://github.com/oceanbase/powercontext/issues/1321),
  [#1709](https://github.com/oceanbase/powercontext/pull/1709), [#1656](https://github.com/oceanbase/powercontext/issues/1656),
  [#1657](https://github.com/oceanbase/powercontext/issues/1657), and
  [#1425](https://github.com/oceanbase/powercontext/issues/1425)

# Summary

This RFC defines the capacity contract for a long-lived Memory Artifact: what is measured, where the ceiling is, what
happens at the ceiling, and how an operator recovers headroom.

A Memory gains three measured dimensions (active entries, manifest entries, manifest bytes), a configured budget over
them, and a deterministic refusal when a write would cross the budget. Recovery is explicit: `forget()` retires entries
from the active surface, and a new opt-in `compact()` operation drops qualifying inactive tombstones from the *current*
manifest without deleting any entry body, any prior Revision, or any exact citation.

Automatic Memory splitting and routing are **not** part of this RFC. They require the routing manifest that RFC 0014
lists as a future possibility, and a second identity layer that RFC 0014 declares a non-goal. This RFC defines stable,
observable failure at the ceiling instead, and specifies the seam a later routing design plugs into.

# Motivation

PR #1709 removed the projection write amplification reported in #1321: an append now rewrites only the rows of the
entry it changed. It deliberately did not define a capacity boundary, and #1321 was closed with that gap open.

What remains is that a Memory has no ceiling. Every Revision stores a complete `flat-v1` manifest, so appending entry
*N* writes a manifest of *N* items; inactive entries stay in that manifest forever as tombstones; and nothing in the
public API tells a caller how close a Memory is to a practical limit, or refuses when it passes one. RFC 0014 records
the first half of this as a drawback:

> `flat-v1` duplicates the directory and accumulates inactive tombstones indefinitely, so manifest cost grows linearly
> with the number of entries.

and defers the second half to empirical work:

> Empirical results will determine thresholds for Memory splitting, inactive-tombstone compaction, and a public routing
> manifest.

RFC 1652 then supplies the *logical* lifecycle (importance, retention tiers, reversible automated deactivation) and
explicitly declines the physical half, pointing at a later design:

> Physical tombstone/manifest compaction, legal retention, external erasure, and cross-artifact cleanup are out of
> scope.

This RFC is that design, narrowed to one question: the capacity envelope of a single Memory Artifact. It is the step
RFC 1652 anticipates as "propose a separate RFC only if inventory and evaluation demonstrate a storage problem" — #1321
is that demonstration.

The outcome we expect: an operator can read a Memory's capacity through a public API, a runaway writer fails with a
specific actionable error instead of degrading silently, and an operator can recover capacity through supported
lifecycle operations and policy settings without losing a citation.

## Non-goals

- **No automatic split or routing.** Memory identity is the Artifact ID (RFC 0014), and a Scope resolves exactly one
  Memory Artifact ID. Routing to a second Memory requires a routing manifest and a mapping identity that RFC 0014
  excludes. This RFC specifies the refusal, plus the interface a routing design would later satisfy.
- **No deletion of Revisions or entry bodies.** Old Revisions and old entry versions are never rewritten *or removed*.
  Physical erasure, legal retention, and cross-artifact cleanup belong to #1425.
- **No change to `flat-v1` storage growth.** Per-Revision manifest duplication is inherent to the format. This RFC
  bounds and observes that growth; a delta manifest format is sketched under future possibilities.
- **No cursor pagination.** #1656 owns bounded entry listing and #1657 owns history pagination. This RFC bounds the
  *internal* read fan-out those issues depend on and defines no cursor of its own.
- **No quality or importance scoring.** Which entry deserves to survive is RFC 1652's question. This RFC only counts.

# Guide-level explanation

## A Memory now has a readable capacity

Every Memory reports where it stands:

```python
capacity = await memory_service.capacity(memory)

capacity.active_entry_count      # 412  entries on the active retrieval surface
capacity.manifest_entry_count    # 468  active + inactive items in the current manifest
capacity.manifest_bytes          # 104_568  canonical bytes committed by this Revision
capacity.compactable_entry_count # 31   tombstones currently eligible for compact()
capacity.budget                  # the configured ceiling
capacity.exceeded                # () — no dimension is over budget
```

`manifest_bytes` is not an estimate. It is `len(memory_content_bytes(content))`, the exact byte string that the Revision
content hash already commits to, so the number an operator reads is the number the storage layer writes.

## Crossing the ceiling is a specific, actionable failure

A write that would push a Memory past its budget is refused before anything is persisted:

```
409 Conflict
{
  "code": "memory_capacity_exceeded",
  "message": "The Memory has reached its capacity budget.",
  "details": {"dimension": "manifest_bytes", "limit": 4194304, "observed": 4194527}
}
```

The refusal names the dimension that bound, the configured limit, and what the rejected write would have produced. The
same write retried unchanged fails identically — it is a state conflict, not a transient error.

## Recovering capacity at the budget

A capacity ceiling that blocked its own remedy would brick a Memory. So relief always runs, even over budget:

- `forget()` retires entries from the active surface. Always permitted.
- `organize(mode="dedupe")` retires exact duplicates. Always permitted.
- `compact()` drops qualifying tombstones from the current manifest. Never blocked by capacity; explicit enablement
  and eligibility rules still apply.

Only operations that *grow* the dimension that is over budget are refused: appending or revising an entry, and
reactivating an entry when the active surface is already full.

If a full Memory has only recent tombstones, an operator can set the minimum age to zero and preview compaction
without creating artificial Revisions. Tagged entries remain protected: retaining those tags may require increasing
the budget instead of compacting their entries. The capacity contract does not override retention decisions.

## Compaction removes tombstones, not history

`forget()` leaves an inactive item in the manifest so the entry can be reactivated and so its history stays readable.
That is the right default, and it is also why manifests only grow. `compact()` is the explicit way to reclaim that
space:

```python
plan = await memory_service.compact(memory, dry_run=True)
plan.entry_ids        # the tombstones that qualify
plan.reclaimed_bytes  # signed decrease in complete canonical content bytes

result = await memory_service.compact(memory)  # requires compaction to be enabled
memory = result.memory
```

What compaction does **not** touch is the part that makes Memory citable. Entry bodies stay in
`pc_memory_entry_versions`. Every prior Revision keeps its own manifest. A Handoff citation resolves
`ArtifactRef + entry_id + entry_version_id` against the **exact Revision it names**, so a citation written before
compaction still validates afterward, byte for byte.

What it does change, and what an operator must accept before enabling it: a compacted entry is gone from the *current*
manifest, so `reactivate()` can no longer restore it and `list(include_inactive=True)` no longer shows it. Entries with
tags are excluded so their tags continue to resolve. Compaction is **opt-in, dry-run first, and irreversible for the
active surface**. The default age of 10 completed Revision advances keeps an accidental `forget()` recoverable;
explicitly setting the age to zero permits immediate compaction. Previews work while compaction is disabled.

## Defaults

The default budget is 5,000 active entries, 10,000 manifest entries, and 4 MiB of complete canonical content. These
limits bound growth of each Revision; they do not guarantee latency or cap total database size. Compaction is disabled
by default because removing an entry from the current manifest prevents reactivation. The default history read limit
is 100 Revisions, with an explicit error rather than silent truncation.

# Reference-level explanation

## Design invariants

1. **Authoritative content is never destroyed.** No entry body row is deleted, no prior Revision is deleted or
   rewritten, no content hash changes. Compaction only decides which items the *next* manifest carries.
2. **Citations are stable.** Exact-Revision citation validation is unaffected by any operation in this RFC, because it
   resolves against the Revision it names rather than the current head.
3. **Budgets never block relief.** Deactivation, deduplication, and enabled compaction run regardless of budget state.
4. **Refusal is side-effect free and deterministic.** The budget is evaluated on the fully prepared next manifest,
   before the transaction opens. The same input produces the same decision.
5. **#1709's guarantees hold.** Enforcement adds no per-entry I/O to the append path, and compaction writes no
   projection rows at all.
6. **Backend-neutral semantics.** Counts, bytes, decisions, and errors are identical on SQLite and OceanBase. Only
   physical space reclamation differs.

## Measured dimensions

All three derive from the manifest of one exact Revision. None is persisted redundantly, matching RFC 0014's rule that
entry counts are derived from the manifest.

| Dimension | Definition | Grows on | Shrinks on |
| --- | --- | --- | --- |
| `active_entry_count` | manifest items with `state == "active"` | add, reactivate | deactivate |
| `manifest_entry_count` | all manifest items | add | compact |
| `manifest_bytes` | `len(memory_content_bytes(content))` | content-dependent, including audit changes | content-dependent |

`manifest_bytes` is the load-bearing dimension: it is what each Revision physically writes, and it is the only one that
accounts for identifier and hash width rather than assuming a fixed per-entry cost. The two counts exist because they
are what an operator reasons about, and because an entry-count ceiling catches a pathological writer earlier than a byte
ceiling does.

`deactivate` lowers the active count but keeps the manifest count unchanged. Every operation replaces the Revision's
audit changes and reasons, so complete canonical bytes can grow or shrink independently of either count. Compaction
reduces the directory, but its new audit records can make that Revision larger; later Revisions do not repeat those
records. This is why `reclaimed_bytes` is signed and why `compact()` is required for manifest-entry relief.

## Budget value

New in `src/powercontext/builtin/artifacts/memory/models.py`:

```python
class MemoryCapacityBudget(BaseModel):
    """The capacity ceiling applied to one Memory Artifact."""

    max_active_entries: int = Field(default=5_000, ge=1)
    max_manifest_entries: int = Field(default=10_000, ge=1)
    max_manifest_bytes: int = Field(default=4_194_304, ge=1_024)

    @model_validator(mode="after")
    def validate_entry_ceiling_order(self):
        if self.max_active_entries > self.max_manifest_entries:
            raise ValueError("max_active_entries cannot exceed max_manifest_entries")
        return self


class MemoryCapacity(BaseModel):
    """Observed capacity of one exact Memory Revision against its budget."""

    memory_ref: ArtifactRef
    active_entry_count: int = Field(ge=0)
    manifest_entry_count: int = Field(ge=0)
    manifest_bytes: int = Field(ge=0)
    compactable_entry_count: int = Field(ge=0)
    budget: MemoryCapacityBudget
    exceeded: tuple[MemoryCapacityDimension, ...] = ()
```

with `MemoryCapacityDimension: TypeAlias = Literal["active_entries", "manifest_entries", "manifest_bytes"]`.

### Default budgets and calibration

The defaults are growth ceilings. Deployment-specific latency targets require representative backend measurements.

A canonical manifest item measures **223 bytes** at the identifier widths the service generates today: `mem_ent_` plus
a 32-character hex UUID, the same again for `mem_ver_`, a 64-character hex content hash, a state, and JSON framing.
Measured against the canonical encoder, 5,000 items is 1,115,092 bytes and 10,000 items is 2,230,092 bytes.

The byte ceiling is therefore 4 MiB, deliberately above the entry ceilings' own footprint. Setting it at 1 MiB would
make both entry ceilings unreachable — the byte ceiling would always bind first, at roughly 4,700 items — leaving two
documented dimensions that never fire. At 4 MiB and current identifier widths, `manifest_entries` binds first at about
2.13 MiB for the directory alone. `manifest_bytes` also includes audit records and reasons, so a large batch can hit
the byte ceiling even at the default identifier widths.

The 5,000 / 10,000 / 4 MiB defaults remain configurable growth limits. Calibrate deployment budgets with isolated,
representative measurements on the selected backend, including retained-history cost. Benchmark methodology and
measurement summaries belong in `benchmark/memory_capacity/README.md`; raw run results accompany acceptance evidence.

## Configuration

`RuntimeConfig` in `src/powercontext/builtin/runtime/config.py`, beside the existing memory settings:

```python
memory_max_active_entries: int = Field(default=5_000, ge=1, le=100_000)
memory_max_manifest_entries: int = Field(default=10_000, ge=1, le=200_000)
memory_max_manifest_bytes: int = Field(default=4_194_304, ge=1_024, le=67_108_864)
memory_compaction_enabled: bool = False
memory_compaction_min_tombstone_revisions: int = Field(default=10, ge=0)
memory_max_history_revisions: int = Field(default=100, ge=1)
```

These thread to the service exactly as `memory_rerank_candidate_limit` does today: read in
`builtin/runtime/composition.py`, carried as fields on the relational runtime in `builtin/runtime/relational.py`, and
passed to the `MemoryService` constructor. `MemoryService.__init__` gains `capacity_budget: MemoryCapacityBudget | None`
and `compaction: MemoryCompactionPolicy | None`, plus `max_history_revisions: int = 100`. `None` selects the default
budget and disabled compaction. Generic Artifact writes in `family_management.py` receive the configured capacity
budget too, so they cannot bypass deployment limits.

## Error

New in `src/powercontext/builtin/artifacts/memory/errors.py`:

```python
class MemoryCapacityExceededError(MemoryLayerError, RuntimeError):
    def __init__(self, dimension: str, limit: int, observed: int) -> None:
        self.dimension = dimension
        self.limit = limit
        self.observed = observed
        super().__init__(f"memory capacity budget is exceeded: {dimension} {observed} > {limit}")
```

It subclasses `MemoryLayerError`. The generic Artifact write path preserves this specific exception instead of
converting it to `InvalidBaseAccessRequestError`, so those writes return the same capacity conflict. Export it from
`src/powercontext/builtin/artifacts/memory/__init__.py`.

`_map_domain_error` in `src/powercontext/server/app.py` maps it ahead of the broad `InvalidMemoryCandidateError` branch:

```python
if isinstance(error, MemoryCapacityExceededError):
    return (
        status.HTTP_409_CONFLICT,
        "memory_capacity_exceeded",
        "The Memory has reached its capacity budget.",
        {"dimension": error.dimension, "limit": error.limit, "observed": error.observed},
    )
```

409 rather than 422 or 503: the request is well-formed and the Server is healthy, but the target resource's state
rejects it and an identical retry fails identically — the same reasoning that already maps `RevisionConflictError` and
`MemoryEntryInactiveError` to 409.

## Enforcement point

Enforcement happens once, on the prepared manifest, in `src/powercontext/builtin/artifacts/memory/service.py`:

- `_prepare_commit` builds `sorted_manifest` and `content` before constructing the `Memory`. The check goes directly
  after `content` is built and before the `MemoryCommit` is returned.
- `_commit_existing_transition` does the same for `forget`, `reactivate`, `organize`, and `compact`.

Both paths call one helper:

```python
def _require_capacity(
    self, base: Memory | None, content: MemoryContent, *, growth: frozenset[str], content_bytes: bytes
) -> None:
    """Refuse a prepared Revision that grows a dimension past its budget."""
```

`growth` names the dimensions this operation may increase; a dimension absent from `growth` is never checked, which is
how invariant 3 is enforced structurally rather than by convention:

| Operation | `growth` |
| --- | --- |
| append / revise (`_prepare_commit`) | `active_entries`, `manifest_entries`, `manifest_bytes` |
| `reactivate` | `active_entries` |
| `forget`, `organize`, `compact` | `frozenset()` |

A dimension is refused only when the prepared value both exceeds the limit **and** exceeds the base Revision's value for
that dimension. A Memory already over budget — after a config change that lowered a ceiling, say — therefore still
accepts a write that does not make the breach worse, and still accepts every relief operation. Dimensions are evaluated
in the fixed order `manifest_bytes`, `manifest_entries`, `active_entries`, so the reported dimension is deterministic
when more than one binds.

Because `MemoryCreateContent` and `MemoryReplaceContent` route through `plan_remember` in
`builtin/persistence/family_management.py`, the generic Artifact write API shares enforcement, configured budgets, and
the capacity-specific error. Head compare-and-swap rejects a prepared plan whose base moved.

The check counts the loaded manifest and reuses the canonical bytes computed for the content hash. It adds no entry
body reads or projection writes, so #1709's append guarantees are untouched.

## `compact()`

New on `MemoryService`:

```python
async def compact(
    self,
    memory: Memory,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    reason: str | None = None,
) -> MemoryCompactionResult:
    """Drop qualifying inactive tombstones from the current manifest."""
```

`MemoryCompactionResult` carries `memory` (the new Revision, or the unchanged base for a dry run or a no-op),
`entry_ids`, `reclaimed_bytes`, and `dry_run`.

`reclaimed_bytes` is the signed difference `len(base_content_bytes) - len(next_content_bytes)`, including every audit
change and reason. It may be negative even when tombstones were removed; it is zero for a no-op and never represents
physical database bytes freed. A dry run writes nothing and works while compaction is disabled. A real commit requires
`enabled=True` and an unchanged head.

### Eligibility

A manifest item qualifies only when all hold:

1. `state == "inactive"` in the base manifest.
2. It has been inactive for at least `min_tombstone_revisions` completed Revision advances. An entry deactivated at
   Revision 2 qualifies at Revision 12 with the default 10. Only changes in that recovery window are read, without
   loading entry bodies. Reactivation followed by deactivation restarts the window. Zero bypasses only the age check.
3. No Artifact tag binds it. Tag targets resolve against the latest manifest including inactive items
   (`builtin/persistence/tags.py:302`), so compacting a tagged entry would break tag resolution. A new backend method
   `any_tagged_entry_ids(memory)` supplies the exclusion set. The existing `tagged_entry_ids` already spans the whole
   manifest rather than only active items, but it requires a `TagFilter` and answers "which entries match this filter";
   eligibility needs "which entries carry any tag at all", so it is not reusable as it stands.
4. It is not reactivated, revised, or otherwise named by the same operation.

`limit` caps how many tombstones one Revision drops, so a Memory with a large tombstone backlog can be compacted in
bounded steps rather than one very large Revision.

Tags are rechecked under the owning Artifact's head lock before commit. A newly tagged candidate aborts the transaction
with `CapabilityNotSupportedError("compaction-tag-conflict")`; callers can preview again against the unchanged head.

### The Revision it writes

Compaction produces an ordinary Revision through `_commit_existing_transition`: the manifest omits the compacted items,
and each drop records a change. Both invariants that make this safe are structural rather than promised:

- **No projection work.** Inactive entries have no rows in `pc_memory_entry_heads` or the search index — `_commit`
  derives its active-head diff from `state == "active"` items only, so a compacted item is absent from both
  `previous_active` and `current_active` and appears in neither the delete set nor the upsert set. Compaction writes
  zero projection rows regardless of how many tombstones it drops.
- **No body deletion.** `pc_memory_entry_versions` rows stay. The foreign keys from `pc_memory_entry_heads` are
  `ondelete="RESTRICT"` and prior Revisions still reference those versions, so retaining them is required, not merely
  chosen.

### The change operation

Compaction records `op="compact"` with `from_entry_version_id` set to the dropped version and
`to_entry_version_id=None`. A manifest that silently shed items with no change record would break RFC 0014's rule that
`changes()` is the compact delta for a Revision, and would leave no audit trail for the one operation in Memory that
removes something from the current directory.

This extends `MemoryChangeOp`, which is public:

- `src/powercontext/builtin/artifacts/memory/models.py` — add `"compact"` to the `MemoryChangeOp` alias.
- `openapi/powercontext.yaml` — add `compact` to `EntryChangeOperation`.
- Regenerate with `make api-generate` and verify with `make contract-test`; the generated models use
  `extra="forbid"` with enum validation, so this is a versioned additive change, not a silent one.

This enum addition affects compatibility alongside write ceilings and the history read bound. A client that enumerates
`EntryChangeOperation` exhaustively must be updated before it reads a Memory whose history contains a compaction. Because compaction is
disabled by default, no existing deployment produces the new value until an operator opts in.

## Public read API

`POST /v1/memory/capacity` returns the `MemoryCapacity` of the current head, reusing the Scope authorization and
Memory-identity validation of the existing `/v1/memory/entries/list` handler. This is an additive OpenAPI operation:
specify it in `openapi/powercontext.yaml`, regenerate, and add a contract test. The service-level `capacity()` is what
SDK callers use directly. The route requires `scope.read` and returns 404 if the Scope has no Memory; it never creates
a Memory or fabricates a reference to report zeros. Python HTTP callers use `PowerContextClient.get_memory_capacity()`.

Compaction is deliberately **not** exposed over HTTP in this RFC. It is a maintenance operation whose authorization
model belongs with the broader retention policy in #1425; exposing it as an unauthenticated-by-default Server route
ahead of that design would be the wrong order. SDK and in-process runtime callers can invoke it today.

## Revision history bounding

This RFC does not delete or bound stored Revisions. Deleting history would break lineage, exact citations, and Handoff
verification, and physical erasure is #1425's boundary.

It does bound one unbounded *read*. `MemoryService.revisions()` loads every Revision from 1 to the head in a loop, one
backend `get()` each, so a Memory with 1,000 Revisions issues 1,000 loads for a single call. This RFC caps that fan-out
with `max_history_revisions` (default 100) and raises `CapabilityNotSupportedError("history-window")` before loading the
history past the cap, which the existing mapping already turns into a 422 naming the capability. The cursor-based
replacement is #1657's deliverable, and this cap is the explicit bound #1656's acceptance criteria asks callers to agree
on rather than discover.

The result is never silently truncated. At 4 MiB per Revision, 1,000 snapshots approach 4 GiB before Python object
overhead; even 100 approach 400 MiB. The limit bounds read fan-out, not process memory: relief operations and lowered
budgets can leave Revisions above the byte budget. Callers may explicitly raise it when they can afford the snapshots.
Exact Revision reads and stored history remain available after the cap is reached.

`entries()` remains unpaginated here and is #1656's to bound. Compaction reduces its cost as a side effect, because a
compacted tombstone is no longer a version that `entries()` loads.

## Backend behavior

Logic is backend-neutral: it lives in the service, operates on the manifest, and reaches storage only through existing
`MemoryBackend` methods plus the one new tag query. Counts, bytes, decisions, and errors are identical across backends,
and the same parametrized conformance tests cover both. OceanBase verification requires a disposable
`POWERCONTEXT_TEST_OCEANBASE_URL`; a skipped run is not evidence of backend parity.

The difference is physical reclamation, and it must be reported separately because the two behave differently:

- **SQLite.** Compaction reduces future directory size and retains every historical manifest. Checkpointing and
  `VACUUM` cannot reclaim pages still occupied by that history. Measurements must distinguish checkpointed bytes from
  post-`VACUUM` bytes; a smaller current manifest does not imply a smaller database file.
- **OceanBase.** Any physical reclamation is asynchronous and cannot reclaim retained history. Report observed database
  bytes and the observation delay without promising a decrease after logical compaction.

## Verification

### Behavior tests

`tests/builtin/artifacts/memory/test_capacity.py` covers the observable capacity contract on SQLite and, when
configured, OceanBase:

- Exact canonical byte counts, deterministic refusal for each dimension, and unchanged storage after refusal.
- Runtime budget propagation, lowered budgets, active-only reactivation checks, and over-budget deduplication.
- Compaction previews, stale-head conflicts, audit changes, retained bodies and citations, and zero projection writes.
- Tombstone age and reactivation resets, tag protection, and rollback when a candidate gains a tag before commit.
- A full Memory recovering immediately with explicit age zero while active and tagged entries remain protected.
- Signed `reclaimed_bytes` when audit reasons outweigh removed pointers, and zero bytes for no-op compaction.
- History reads succeeding at 100 Revisions, refusing at 101 before expansion, and explicit configuration overrides.

`tests/e2e/test_memory_capacity.py` covers the HTTP and client contract, including 404 for a Scope without Memory,
409 on explicit and generic writes, and read authorization. `tests/test_api_contract.py` verifies the operation and
additive `compact` enum value.

### Regression guard

The #1709 tests `test_memory_append_projection_writes_do_not_grow_with_entry_history` and
`test_memory_append_leaves_untouched_projection_rows_identical` must stay green unmodified. Enforcement adds no
projection work, so any change in their statement counts means the implementation put the check in the wrong place.

### Scale benchmark

A new `benchmark/memory_capacity/` module, alongside the existing `locomo` benchmarks and outside `tests/` for the
reasons `benchmark/README.md` gives, records at entry counts 200, 1,000, and 5,000, and across a compaction cycle:

entry count, manifest bytes, database bytes, mean append latency, mean final-window append latency, projection row
writes per append, and search recall behavior before and after compaction.

This is the full envelope #1718 asks for, superseding #1709's projection-statement-count measurement rather than
repeating it. SQLite and OceanBase results are reported separately, each stating its reclamation procedure.

## Implementation order

Each step is independently reviewable and leaves the tree green.

1. **Measurement, no enforcement.** `MemoryCapacityBudget`, `MemoryCapacity`, `MemoryCapacityDimension`,
   `MemoryService.capacity()`, exports. Test that reported bytes equal canonical bytes.
2. **Enforcement.** `MemoryCapacityExceededError`, `_require_capacity`, the two call sites, the `growth` table, the
   HTTP mapping. Tests for refusal, for nothing persisted, and for relief over budget.
3. **Configuration.** The six `RuntimeConfig` fields and the constructor threading. Test that a configured ceiling
   reaches the service.
4. **Compaction.** `compact()`, eligibility including the new tag query, the `compact` change op, the OpenAPI enum
   addition, `make api-generate`, `make contract-test`.
5. **Read bounding.** The `revisions()` cap and its capability error.
6. **Public read endpoint.** `POST /v1/memory/capacity`, OpenAPI, contract test.
7. **Benchmark.** `benchmark/memory_capacity/` and the recorded SQLite and OceanBase results.

Steps 1 through 3 alone close the "no observable ceiling" half of #1718 and are worth landing before compaction.

Validation for the whole change: `make check`, `make test`, `make contract-test` after step 4 or 6, and `make docs-test`
for this document and its Chinese translation.

## Acceptance criteria

- A Memory's active entry count, manifest entry count, and manifest bytes are readable through a public API, and the
  reported bytes equal the canonical bytes the Revision commits to.
- A write that would cross a budget dimension raises `MemoryCapacityExceededError`, maps to a 409 naming the dimension,
  limit, and observed value, and persists nothing.
- Refusal is deterministic: the same write against the same head fails identically, and which dimension is reported is
  fixed when several bind.
- Capacity never blocks `forget()`, `organize()`, or enabled `compact()`. A full Memory with eligible tombstones can
  recover through lifecycle operations without direct storage access; age zero permits immediate recovery while tags
  remain protected.
- `compact()` deletes no entry body row, no prior Revision, and no content hash, and writes zero projection rows.
- A citation created before compaction still validates against the Revision it names afterward.
- Compaction skips tagged tombstones and tombstones below the configured minimum age, and supports dry-run.
- `changes()` reports a `compact` operation for every dropped entry.
- The #1709 incremental projection write guarantees hold unchanged, verified by its existing tests.
- Scale results record entry count, manifest bytes, database bytes, append latency, final-window append latency,
  projection row writes, and post-compaction search behavior, reported separately for SQLite and OceanBase.
- Compaction is disabled by default, with a 10-Revision recovery window. Budgets default to 5,000 / 10,000 / 4 MiB and
  history reads to 100 Revisions; callers can explicitly configure these limits.

# Drawbacks

- **A ceiling can refuse a legitimate write.** A deployment that genuinely needs more than 5,000 active entries in one
  Memory now fails where it previously degraded. That is the intended trade — silent superlinear degradation is worse
  than a named limit — but it is a behavior change. A deployment may need to tune the defaults to its workload.
- **Compaction is irreversible for the active surface.** A compacted entry cannot be reactivated. Dry-run, the minimum
  age, the tag exclusion, and the disabled default reduce this risk. Explicit age zero trades the recovery window for
  immediate capacity relief.
- **A public enum grows.** `EntryChangeOperation` gaining `compact` obliges strict clients to update.
- **Total storage remains unbounded.** `flat-v1` still duplicates the directory per Revision and retains history.
  Budgets constrain growth of each Revision, with relief exempted; neither compaction nor the history read limit caps
  cumulative database size.
- **Three dimensions are more than one.** Two counts plus bytes is more contract surface than a single entry cap. The
  counts are what operators reason about and bytes is what storage pays, and collapsing them would lose one or the
  other.

# Rationale and alternatives

**Why refuse rather than split?** Splitting needs a second identity mapping one logical Memory to several Artifacts.
RFC 0014 makes that a non-goal and lists routing manifests as a future possibility. A split invented here would have to
answer which Memory a search covers, which Memory a Scope resolves, and how a citation survives a split — an RFC of its
own. Refusing is the honest intermediate: deterministic, observable, testable, and forward-compatible, since a later
routing design replaces `_require_capacity`'s raise with a route decision at exactly one call site.

**Why not delete old Revisions?** It is the largest storage win available and the one thing we must not do here. It
breaks lineage, exact citations, and Handoff verification, and it is physical erasure, which #1425 owns.

**Why not summarize or merge entries at the ceiling?** RFC 1652 rejected body compaction for a reason worth repeating:
entry bodies are self-contained citation-bearing records, and rewriting them risks losing names, dates, and quantities.
Capacity pressure must not become a license to rewrite content.

**Why `manifest_bytes` rather than database bytes?** Database bytes are the number an operator actually cares about,
but they are backend-specific, lag behind writes, and need `VACUUM` on SQLite to mean anything. Manifest bytes are
exact, backend-neutral, already computed in the write path, and directly proportional to the cost this RFC bounds. The
benchmark reports database bytes so the relationship between the two is measured rather than assumed.

**Why not a dedicated capacity table?** RFC 0014 requires entry counts to be derived from the manifest rather than
persisted redundantly. A counter table would add a second source of truth that could disagree with the manifest, for no
read we cannot serve from the manifest we already load.

**Why enforce in the service rather than the backend?** The service is where the next manifest is assembled, so it is
able to refuse before persistence writes. Both SQL adapters then inherit identical semantics, and the generic
Artifact write path inherits them too.

**Why enable the budget by default?** A capacity contract that is off by default does not bound anything, and #1321 is
a report about a system with no bound. Since compaction — the part that mutates — stays off, the default-on piece can
refuse growth past the configured ceiling; deployments that need a higher limit can configure it explicitly.

**Impact of not doing this.** #1321's superlinear growth stays unbounded and unobservable. A long-lived Memory keeps
degrading with no signal, no ceiling, and no supported way to reclaim tombstone space.

# Prior art

Immutable-log systems separate logical deletion from physical reclamation the same way: a tombstone marks the deletion,
and a later compaction pass reclaims space without rewriting history readers may still hold. LSM-tree compaction and
Git's reachability-based garbage collection both make the reclamation step explicit and asynchronous rather than
implicit in the delete.

Within PowerContext, `organize()` already establishes that maintenance is an ordinary Revision with recorded changes
rather than an out-of-band mutation, and the Topic Memory work budget in
`src/powercontext/builtin/persistence/topic_memory_budget.py` establishes server-owned ceilings enforced before
expensive work with a stable exhaustion reason. This RFC follows both patterns: compaction is a normal Revision, and
the budget is a ceiling checked before persistence with a named reason.

RFC 1652's retention tiers and reversible automated deactivation are the logical counterpart to this physical contract:
that RFC decides which entries should leave the active surface, this one decides when the manifest may stop carrying
them.

# Unresolved questions

- **Deployment calibration.** The defaults remain 5,000 / 10,000 / 4 MiB. Isolated latency measurements are still
  needed to recommend tighter budgets for particular workloads.
- **Should compaction ever be automatic?** This RFC makes it explicit and operator-driven. Whether a scheduled
  compaction below a headroom threshold is safe depends on the authorization and dry-run model in #1425.
- **What is the recovery path for a compacted entry?** Today: none through `reactivate()`. Whether a `restore`
  operation that re-adds a retained body as a new entry is worth defining, and what identity it would take, is
  deliberately left open.
- **Per-Scope budgets?** Budgets are per-deployment here. Per-Scope overrides need the Scope-level configuration story
  that #1219 and #1345 own.
- **Cursor and consistency sharing.** The `revisions()` cap must agree with the cursor and pinned-Revision semantics
  #1656 and #1657 settle. If they land first, this cap becomes their default page size rather than a separate limit.

# Future possibilities

A delta manifest format — `manifest-v2`, storing a base reference plus the changes since it, with periodic full
snapshots — is the actual fix for `flat-v1`'s per-Revision duplication, and would turn the storage growth this RFC
bounds into growth proportional to changes rather than entries. It is a persisted-format change requiring migration and
a rebuild path, so it needs its own RFC; this RFC's measured dimensions are what would demonstrate the need and verify
the result.

Beyond that: routing manifests and automatic split, plugging into `_require_capacity`'s single decision point; scheduled
compaction under #1425's authorization model; capacity signals feeding RFC 1652's cleanup proposals, so that pressure
selects low-importance entries rather than merely refusing; and per-Scope budget overrides once Scope-level
configuration exists.
