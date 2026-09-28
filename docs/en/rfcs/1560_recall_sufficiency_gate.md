- Proposal Name: `recall_sufficiency_gate`
- Start Date: 2026-09-10
- Status: Proposed
- RFC PR: [oceanbase/powercontext#1560](https://github.com/oceanbase/powercontext/pull/1560)
- Tracking Issue: [oceanbase/powercontext#1556](https://github.com/oceanbase/powercontext/issues/1556)
- Related RFCs: [RFC 0028](0028_context_pack.md), [RFC 0080](0080_memory_search_reranking.md),
  [RFC 0081](0081_end_to_end_evaluation_architecture.md), [RFC 1229](1229_unified_workloads_and_long_horizon_memory_evaluation.md),
  [RFC 1489](1489_prepared_context_text_assembly.md)

# Summary

This RFC adds an internal **recall sufficiency gate** with **bounded expansion** to the Runtime `prepare_context`
operation.

Today `prepare_context` performs exactly one search over the participating Artifact families, fits the hits into the
caller's `max_bytes` budget, and renders. When that single search under-retrieves, the operation still returns
normally; it simply delivers fewer items, or a normal empty result.

This RFC lets the Runtime, before rendering, cheaply assess whether the candidate set is *sufficient* for the query
and, when it is not, run at most two controlled expansion rounds. Both rounds lower the **admission floor** applied to
the candidates each participating family's search has already retrieved. Selection then happens inside the **same**
caller-provided budget.

The gate uses no model, never changes the caller's budget, never changes the public `PreparedContext` contract, and
fails open to current behaviour.

Three properties make the proposal safe to evaluate:

- **Expansion cannot break the Builder invariant.** Merged candidates are re-selected down to each family's existing
  candidate ceiling using the allocators that already clamp round 0, so a cross-round union can never exceed the
  ceiling `build_scopes_result()` enforces (`prepared_context.py:164-169`).
- **A run that does not expand is byte-identical to today.** The no-expansion path is unchanged, which is the
  regression guarantee.
- **Delivered size never exceeds `max_bytes`.** Expansion may use budget the round-0 candidate set left unused, so
  `content_bytes` can *increase* within the ceiling; it can never cross it.

# Motivation

RFC 0028 deliberately collapses internal reasons (no Memory, no match, error) into one normal empty result for the
caller. That decision stands and is not revisited here. Collapsing the *reported reason* is a different matter from
having no *recovery path at all*.

Concretely, a query whose relevant evidence sits just outside the first recall round — a differently worded entry, or
an entry the admission floor discarded from a pool the search had already retrieved — currently receives a silently
thinner context, and every integration observes the same thinness. RFC 1489 gave callers control over which families
participate, their order, and a per-family item limit; it did not address what the Runtime should do when the
participating families together return too little to be useful.

The current pipeline is single-pass by construction:

```text
one search per participating family
  -> clamp to the Builder candidate limits
  -> select and fit to max_bytes
  -> render
```

Two facts about the existing implementation decide where a second, bounded look can actually change the candidate set:

- **Round 0 already asks for everything the ceilings allow, so the unused room is inside the search result, not beside
  it.** The per-family search limits are fixed constants on `PreparedContextBuilder` (`prepared_context.py:103-110`:
  `memory_candidate_limit = 16`, `topic_memory_candidate_limit = 8`, `experience_candidate_limit = 8`), exceeding them
  raises `PreparedContextInvariantError` (`prepared_context.py:164-169`), and the Runtime requests exactly those values
  (`application.py:742-743` for Memory and Experience, `application.py:761` for Topic Memory). There is therefore no
  headroom in `SearchMemoryRequest.limit` at all: raising it is a no-op by construction. The unused room is one level
  down. Every family already asks its backend for roughly four times its limit — `candidate_limit = max(coarse_limit *
  4, 32)` for Memory (`service.py:457`, so 64 at the prepare-path limit of 16), `limit * 4` for Experience
  (`sqlite/experience_index.py:156`, `oceanbase/experience_index.py:114`), and `min(MAX_TOPIC_MEMORY_SEARCH_LIMIT, limit
  * 4)` for Topic Memory (`persistence/topic_memory.py:441`) — and that pool is then filtered by an admission floor
  before it is fused: a lexical-evidence gate requiring enough distinct query terms (`search.py:104`, shared by all
  families) and, for the vector channel, a fixed cosine baseline of `0.3` (`memory/fusion.py:29`,
  `topic_memory/fusion.py:33`). A thin result is produced by that floor, not by the limit, and round 0 never revisits
  it.
- **Switching `mode` is not an expansion lever either.** `_recall_scope` hard-codes `mode="auto"`
  (`application.py:849`), but `auto` already resolves to `hybrid` whenever the hybrid channel is available
  (`service.py:602-607`), so a Scope that can serve both channels already does. On a Scope with no vector deployment an
  explicit `hybrid` does not degrade gracefully: it raises `CapabilityNotSupportedError` (`service.py:598-601`).

The pool *capacity* alone cannot tell the gate whether the floor is what thinned the result, because a sparse backend
and a heavily filtered pool look identical in the delivered hits: `MemorySearchResult` exposes only `mode`, `hits` and
an optional `rerank` trace (`memory/models.py:165-169`), and the Experience and Topic Memory recall entry points return
bare hit tuples. Nothing counts how many candidates each family *retrieved* versus how many *survived admission*. This
RFC therefore adds those counts as in-process-only values (see *Required internal plumbing*); without them the first
signal below is not computable and must not be claimed.

A third observation motivates the reporting half of this RFC: today nothing counts the items that lose to the budget.
In the non-assembly path `_fit_entry` truncates to `max_entry_content_bytes` and returns the candidate when it fits
(`prepared_context.py:461`); it drops the item whole on two distinct paths — when the source is shorter than
`_MIN_TRUNCATED_CONTENT_BYTES` (`prepared_context.py:462-463`, the constant being `64` at `:39`), and, when that is not
the case, when the truncation search that follows finds no rendering that fits and returns its still-`None` best
candidate (`prepared_context.py:465-485`, returning `best` at `:485`). In the assembly path `fit_context_text_item`
drops an item when the truncation search finds nothing usable or the best result falls below the body floor of `64`
bytes (`prepared_text.py:103-104`, constant at `:35`). `truncated` is rendered per item, but neither the truncations
nor the whole-item drops are counted anywhere. Making them countable is a small change and is a prerequisite for
evaluating this feature honestly.

This RFC is not a claim that more recall is always better. It is a claim that *conditional* additional recall — paid
only when the first round looks thin — is worth evaluating under the existing workload infrastructure.

# Guide-level explanation

## Mental model

```text
Stage A  recall
           search participating families (round 0)
           probe the budget over round 0's candidate set      (pure, no I/O)
           RecallSufficiencyGate.assess(families, query, budget, policy)
             sufficient                     -> Stage B
             insufficient, committed < 2    -> expand (bounded) and search again
             insufficient, committed == 2   -> Stage B with what we have
Stage B  select + section assembly + budget fitting + render   (unchanged)
Stage C  report recall effort and omission                     (in-process)
```

Expansion may only change **which candidates compete**. It never changes the output budget, the trust wrapper, the
citation form, or the set of families the caller selected.

## What the gate looks at

The gate is deliberately cheap and model-free. Every signal below is either already available from round-zero results
or comes from the counters this RFC adds; none requires a model call.

| Signal | Computable from | What it detects |
| --- | --- | --- |
| Retrieved vs. admitted per family and channel | The new admission counters | A family's admission floor discarded nearly everything it retrieved. |
| Admitted candidates vs. that family's Builder ceiling | Round-zero hits and `prepared_context.py:103-105` | The family has room for a later round to contribute; a saturated family does not. |
| Top-1 score and the gap to the mean over admitted candidates | Score-bearing families only: Memory (`MemoryHit.score`) and Topic Memory (`TopicMemorySearchHit.score`) | One plausible hit surrounded by noise, or no clear winner. |
| Lexical coverage of the top candidates in the analyzer's token space | `analyze_text` / `fts_query_requirements` (`search.py:78`) | Hits matched on stopwords or on one shared token only. |
| Number of participating families that returned at least one admitted candidate | Round-zero results | An assembly that selected three families and got results from one. |
| Distinct evidence identities within a family | Family-specific identity (see below) | Many candidates that are really the same evidence. |
| Whether round zero's fit was budget-bound | The budget probe | Thin output caused by `max_bytes`, not by recall. |

**Experience carries no score.** `ExperienceSearchHit` has exactly `artifact_ref` and `content`
(`artifacts/experience/search.py:26-30`), so the score-based signals apply only to the score-bearing families.
Experience contributes its admission counters, its candidate count against its ceiling, and its family-coverage bit.

**Evidence identity is family-specific.** A single Memory search returns many `MemoryHit` values that all share one
`memory_ref` Artifact revision, because one Memory Revision holds many entries; the independent unit of evidence is the
entry, identified by `entry_id` and `entry_version_id` (`memory/models.py:142-150`). Counting distinct Artifact
revisions would therefore make any Memory-only result look like one source. The gate must use:

| Family | Evidence identity |
| --- | --- |
| Memory | `(memory_ref, entry_id, entry_version_id)` |
| Experience | `artifact_ref` (`ArtifactRef` revision) |
| Topic Memory | `artifact_ref` (`ArtifactRef` revision) |

Thresholds are deployment configuration, not request parameters, and are recorded by version in the trace so a run can
be reproduced.

## What expansion does

Only three families are searchable: `memory`, `experience` and `topic-memory`. `profile` is a selectable section family
(`runtime/models.py:195`) but it is read with `profiles.latest` and is never searched or admission-filtered
(`application.py:752-759`), so it has no floor to lower. **Profile is never expanded**, and a request whose only
section is `profile` is never expanded at all.

| Round | Action | Precondition |
| --- | --- | --- |
| 1 | Lower the admission floor applied to what each participating searchable family's search already returned, within the configured expansion floor: the lexical-evidence requirement (`search.py:104`) and the cosine baseline (`memory/fusion.py:29`, `topic_memory/fusion.py:33`). | Round 0 assessed insufficient. |
| 2 | Lower admission to the policy floor and accept the best available evidence. | Round 1 committed and round 1 assessed insufficient. |

No further action is available in round 2. In particular, **raising `memory_rerank_candidate_limit` is not an
expansion action**: `MemoryService` uses that bound to size the backend request, not only to rerank an existing pool
(`coarse_limit` at `service.py:452`, then `candidate_limit=max(coarse_limit * 4, 32)` at `service.py:457`), so raising
it from 30 to 100 would grow the backend pool from 120 to 400 candidates and contradict the same-pool guarantee that
makes the cost claim defensible. It is rejected here and belongs to a separate retrieval-expansion proposal, if ever.

**The cost is bounded cumulatively, not monotonically per round.** The round count is capped at two, so a prepare
performs at most three search passes per participating searchable family. This RFC does **not** claim that round 2 is
strictly more expensive than round 1: with reranking disabled and the same pool size, round 2 repeats the same work
with a lower floor, and its incremental cost is the same as round 1's. The bound that matters is the cumulative one,
and it is stated in full in the cost model below.

**Expansion never raises `limit` and never switches `mode`**, for the reasons in the Motivation: round 0 already
requests every family at its Builder ceiling, and `mode="auto"` is already `hybrid` wherever hybrid is available.

**Expansion never adds a family.** RFC 1489 states that `assembly.sections` determines which families participate, and
that a family the caller did not select is not searched and is not given output budget. Silently searching an
unselected family would violate that contract, so family membership is out of scope for expansion. If a caller omits
`assembly` entirely, the Runtime's existing default family selection applies unchanged and is also not expanded.

**Expansion is a no-op for a family whose round 0 already saturated its ceiling.** Because a merged set keeps round 0
as its prefix (see *Between rounds*), a family that already filled its candidate ceiling in round 0 cannot receive
later-round candidates. The gate's "admitted vs. ceiling" signal reports this, and the trace records the round as one
that changed nothing. The constraint in that case is downstream — budget, not recall — and the budget probe says so.

## What you can observe

Following the precedent of the RFC 0080 `rerank` trace, the gate result stays in the process and is **not** added to the
HTTP v1 response.

`RecallEffort` is produced by the expansion loop, which lives in `ScopedContextApplication._prepare`
(`application.py:727`), not by the Builder. It is therefore **not** attached to `PreparedContextBuild`: `_prepare`
returns `build.context` (`application.py:812`) and discards the rest of the build result, so a new field there would be
unobservable to every consumer except the optional recall-token estimator (`application.py:794-796`). Instead the
Runtime gains an optional sink, exactly mirroring the recall-token estimator that already exists:

```python
RecallEffortSink: TypeAlias = Callable[[RecallEffort], Awaitable[None]]

class Runtime:
    ...
    # Configured like recall_token_estimator (composition.py:511); None by default.
    self._recall_effort_sink: RecallEffortSink | None = recall_effort_sink
```

`_prepare` awaits the sink after the build, with errors swallowed and logged the same way a failing estimator is
(`application.py:794-808`). A caller that configures no sink pays nothing.

```python
@dataclass(frozen=True)
class AdmissionCounts:
    family: str                       # "memory" | "experience" | "topic-memory"
    scope_id: str
    retrieved: int                    # returned by the backend, before admission
    admitted: int                     # survived the admission floor

@dataclass(frozen=True)
class RecallEffort:
    policy: str                       # versioned policy id, e.g. "powercontext.recall-gate.v1"
    assessment: str                   # final gate reason, e.g. "sufficient" | "thin-candidates" | "weak-top-1"
    rounds: int                       # search passes actually executed: 1 + len(expansion_actions), so 1..3
    expansion_actions: tuple[str, ...]  # committed rounds only; at most ("admission", "policy-floor")
    candidates_by_round: tuple[int, ...]  # candidates offered to the Builder per round; len == rounds
    admission_by_family: tuple[AdmissionCounts, ...]  # measured in the last executed round
    added_embeddings: int             # query embeddings paid by expansion rounds
    added_generation_calls: int       # extra RFC 0080 rerank calls paid by expansion rounds
    truncated_items: int              # delivered but cut; not counted today
    dropped_items: int                # whole-item omissions across both fitting paths; not counted today
    dropped_below_min_bytes: int      # subset of dropped_items: below _MIN_TRUNCATED_CONTENT_BYTES / _BODY_BYTES
    dropped_no_fitting_truncation: int  # subset of dropped_items: no rendering fit at all
```

`rounds` counts **search passes executed**, so a prepare that ran round 0 and two expansion rounds reports
`rounds: 3`, and one expansion round reports `rounds: 2`. `dropped_items` equals
`dropped_below_min_bytes + dropped_no_fitting_truncation`, so a reader can tell "the budget could not fit this item"
apart from "this item was too short to truncate into the remaining space". A single counter labelled "lost to the
budget" would misreport the second cause, so both are reported and `_fit_entry`'s two `None` paths
(`prepared_context.py:462-463`, `:465-485`) and the assembly path (`prepared_text.py:103-104`) are distinguished
rather than merged.

The last four fields are new observability rather than new behaviour; neither the truncations nor the drops are
counted anywhere today, and without them "we found the evidence" cannot be told apart from "we found it and the budget
ate it" — the distinction that matters when interpreting an expansion that changed nothing.

The `context.build` stage (`application.py:766-793`) may additionally carry aggregate counters (`recall.rounds`,
`recall.assessment`, `recall.truncated_items`, `recall.dropped_items`). RFC 0028 permits "internal search mode and
aggregate selection counts" in metrics (`docs/en/rfcs/0028_context_pack.md:521-522`), and these are aggregate counts
with no query text, no entry identity and no per-entry attribution.

## Example

A Codex hook asks for context with the default budget; the first round returns one weak Memory hit.

```http
POST /v1/context/prepare
Content-Type: application/json

{
  "scope_id": "project:demo",
  "query": "How do I verify the HTTP API after changing the contract?",
  "max_bytes": 8000
}
```

Round 0 returns three Memory candidates from a backend pool of 64, one with a usable score; admission admits three of
64, which is far below the Memory ceiling of 16, so the family has room to grow. The budget probe finds unused bytes
and no whole-item drops, so the output is recall-thin rather than budget-thin. The gate assesses `weak-top-1`, expands
once (admission floor lowered), and round 1 admits four more candidates that the round-0 floor had discarded from the
pool the search had already retrieved. Selection and rendering then proceed exactly as today, inside the same 8000
bytes.

If round 1 had contributed nothing new, prepare would have delivered the round-0 result — the behaviour of today —
and `RecallEffort` would report `rounds: 2` with `expansion_actions: ("admission",)` and a `candidates_by_round` pair
that is flat, which is precisely the case `truncated_items` and `dropped_items` make interpretable.

## What this RFC does and does not do

| It does | It does not |
| --- | --- |
| Assess sufficiency with model-free signals | Add a model call to assembly (RFC 1489 keeps assembly model-free) |
| Expand at most twice, with a bounded cumulative cost | Retry indefinitely or loop |
| Search harder inside the existing candidate ceilings | Exceed `memory_candidate_limit` / `experience_candidate_limit` |
| Keep the caller's `max_bytes` as the only output budget | Change `PreparedContext(schema, status, content, content_bytes)` |
| Re-select the merged set down to each family's ceiling | Let a cross-round union break the Builder invariant |
| Use unused budget within `max_bytes` when expansion supplies usable evidence | Deliver more than `max_bytes` |
| Report effort through an in-process sink | Change the HTTP v1 response |
| Degrade to today's behaviour on any error | Make expansion a new failure mode |

# Reference-level explanation

Every file and line citation in this document was resolved against `origin/master` at
`5ecfad187b4b303504bfd3a36251531b0f4fe1d0`, reached through the prepare path from `_prepare` down to each admission
site.

## Current behaviour

`PreparedContextBuilder` (`src/powercontext/builtin/runtime/prepared_context.py`) declares the ceilings:

```python
memory_candidate_limit = 16
topic_memory_candidate_limit = 8
experience_candidate_limit = 8
candidate_limit = memory_candidate_limit
entry_limit = 8
topic_memory_entry_limit = 8
experience_entry_limit = 2
max_entry_content_bytes = 2000
```

`build_scopes_result()` raises `PreparedContextInvariantError` when a family exceeds its candidate limit
(`prepared_context.py:164-169`), then either routes to `_build_text()` when `request.assembly` is present, or
interleaves, fits, and renders. When nothing fits it returns `PreparedContext(status="empty", content=None,
content_bytes=0)`; otherwise `status="ready"` with the rendered content and its UTF-8 length. If the rendered length
exceeds `request.max_bytes`, it raises `PreparedContextInvariantError("output-budget")`
(`prepared_context.py:207-208`). `PreparedContextStatus` has exactly the two values `"ready"` and `"empty"`
(`runtime/models.py:58`).

`PrepareContextRequest` carries `query`, `max_bytes` (512–32768, default 8000) and an optional `assembly`.
`SearchMemoryRequest` carries `query`, `limit` (default 10), `mode` (`fts` / `vector` / `hybrid` / `auto`, default
`auto`), and `tag_filter`. There is no time-window or as-of parameter on either request. The prepare path never uses
the `limit` default: `_prepare` passes the Builder ceilings themselves (`application.py:742-743`, `:761`), as noted
above.

## Where the candidate set is actually thinned

Each participating family's search runs in two stages. Stage one retrieves: the backend is asked for roughly four times
the family's limit (`service.py:457`, `sqlite/experience_index.py:156` and `oceanbase/experience_index.py:114`,
`persistence/topic_memory.py:441`). Stage two admits: the retrieved candidates are filtered before fusion by a
lexical-evidence requirement — a candidate must cover enough distinct query terms (`search.py:104`, with the required
count derived in `fts_query_requirements` at `search.py:78`) — applied to Memory at `memory/fusion.py:34-40`, to Topic
Memory at `topic_memory/fusion.py:102-107`, and inside the Experience index at `persistence/experience_index.py:309` —
and, for the vector channel, by a cosine baseline of `0.3` (`memory/fusion.py:29`, `topic_memory/fusion.py:33`,
applied at `memory/fusion.py:43-52` and `topic_memory/fusion.py:110-117`).

A pool that is already four times the delivered ceiling, filtered down to almost nothing, is the situation the gate
exists to detect. The expansion action is therefore defined as **the admission floor**, because it is the only boundary
on this path that can be relaxed without exceeding a Builder invariant, without a capability error, and without
touching the public request.

**The Runtime does not own that boundary today, so this RFC has to specify the plumbing rather than assume it.**
`_prepare` calls `MemoryService.search` (`service.py:398`) and passes only `query`, `memories`, `limit` and `mode`
(`application.py:845-850`); the lexical and vector floors are applied *inside* that call (`service.py:464-465`) and
inside the Experience index and Topic Memory fusion code. Lowering them therefore requires new internal parameters on
those three entry points, described next.

## New components

1. **`RecallSufficiencyPolicy`** — a frozen, versioned value object holding the thresholds, the maximum round count,
   the per-round admission floors, and the prompt-shape limits for the budget probe. Constructed from Runtime
   configuration; defaults preserve today's behaviour when the feature is disabled.
2. **`RecallAdmissionPolicy`** — the value threaded into each searchable family's search to override its floor:
   an optional `required_matches` (default: the value `fts_query_requirements` derives, `search.py:78`) and an
   optional `min_semantic_similarity` (default: `0.3`, `memory/fusion.py:29`, `topic_memory/fusion.py:33`). Passing
   `RecallAdmissionPolicy()` — both overrides `None` — reproduces today's behaviour exactly, which is what round 0
   does.
3. **`AdmissionCounts`** — `(family, scope_id, retrieved, admitted)`, one per searchable family per scope per round.
4. **`RecallSufficiencyGate`** — a pure function. It takes the round's per-family views, the query, the policy, and a
   budget view:
   `assess(*, query, families, budget, policy) -> GateAssessment`. No I/O, no model call, and no clock access beyond
   what the candidates already carry. It returns `sufficient` plus a reason and the signal values that produced it.
5. **`RecallBudgetView`** — `max_bytes` together with the counters from a **budget probe**: one pass of the Builder's
   existing pure fitting code over the round's candidate set, discarding the rendered output and keeping
   `delivered_items`, `truncated_items`, `dropped_items` and `unused_bytes`. The gate needs this to distinguish
   budget-limited thinness from recall-limited thinness; without it the 512-byte edge case below is undecidable.
6. **`RecallExpander`** — a pure function `(round, policy) -> SearchPlan`, where `SearchPlan` carries only the two
   admission overrides for the next round. It never names a family, so it cannot violate the assembly contract, and it
   never sets `limit`, `mode`, or a rerank candidate bound.
7. **`RecallEffort`** — the trace value described above, delivered to the Runtime's optional sink.

All of these live under `src/powercontext/builtin/runtime/`. The gate, the expander and the budget probe reuse pure code
and are tested directly without a database.

## Required internal plumbing

The counts and the floor override have to reach the three admission sites. None of this is an HTTP change.

**Memory.** `MemoryService.search` already materializes both sides of the boundary — `channels` from the backend, then
`admitted_fts` and `admitted_vector` (`service.py:463-465`). It gains an optional `admission: RecallAdmissionPolicy |
None = None` keyword that replaces the derived required-match count and the `0.3` cosine baseline, and it reports
`retrieved = len(channels.fts) + len(channels.vector)` together with `admitted = len(admitted_fts) +
len(admitted_vector)`. The counts are exposed on an in-process-only field of `MemorySearchResult`
(`memory/models.py:165-169`). The HTTP contract is untouched because the response is built from `MemorySearchPage`
(`runtime/models.py:183-189`, constructed at `application.py:1738` and `:1761`) by `search_response`
(`server/mapping.py:788`), which enumerates its fields explicitly; the counters must stay off `MemorySearchPage` and out
of `openapi/powercontext.yaml`, so `make api-generate` is not required.

**Experience.** The Runtime recalls Experience through the `experience_recall` callback
(`application.py:862-878`), which today returns a bare `tuple[ExperienceSearchHit, ...]`. It must return an outcome
carrying the hits plus their `AdmissionCounts`. The floor is applied in the row decoder
`experience_search_hits` (`persistence/experience_index.py:298-323`, admit at `:309`), which already stops as soon as
`limit` hits are admitted (`:321-322`); to report `retrieved` it must count the rows it examined rather than only the
rows it kept. The callback's signature is part of the Runtime's construction surface (`composition.py:486`,
`application.py:2257`). The Skill path (`persistence/experience_index.py:338`) is not reached from `prepare_context`,
because `ContextAssemblySection.family` admits only `memory`, `experience`, `profile` and `topic-memory`
(`runtime/models.py:195`), so it does not need the same treatment.

**Topic Memory.** `_topic_memory_hits` (`application.py:890`) also returns a bare tuple; it must return an outcome
carrying the hits plus their `AdmissionCounts`, measured around `_admit_fts` and `_admit_vector`
(`topic_memory/fusion.py:102-117`).

**Round 0 behaviour is unchanged.** Round 0 passes `RecallAdmissionPolicy()` and ignores the floor override; the only
difference is that it now observes and returns the counts. With the feature disabled, the counters are not even
collected.

## Where the loop goes

The loop belongs in the Runtime layer that currently performs the per-family searches and then calls
`PreparedContextBuilder.build_scopes_result()` — `ScopedContextApplication._prepare` (`application.py:727`) via
`_recall_scope` (`application.py:814`). The Builder itself stays free of I/O, persistence, and reranking, as its
docstring states; it receives the candidates from the winning rounds, exactly as it does today. Any round needs the
same per-scope context and lock that `_recall_scope` already acquires (`application.py:827-830`), so a later round
re-enters the same guard rather than holding it across rounds.

## Between rounds

The Builder receives one candidate set, so the RFC has to say which round produces it, and it has to do so without
breaking the invariant the Builder enforces.

**Merge rule.** Per family and per scope group, the merged set is round 0's candidates followed by the identities each
later round contributes that are not already present, in round order, deduplicated by the family's evidence identity
(*What the gate looks at*). On an identity collision the earlier round's occurrence is retained. Concretely, for
`memory`: `merged = round0_hits + (new identities from round 1, in fusion order) + (new identities from round 2, in
fusion order)`.

**Reselection rule.** The merged set is then passed through the allocators that already clamp round 0 —
`_limit_memory_candidates` (`application.py:747`) and `_limit_experience_candidates` (`application.py:748-751`), which
round-robin the family's ceiling across scope groups — while Topic Memory keeps its single-scope ceiling check. Because
round 0 is a prefix of the merged sequence, a truncation can only ever remove candidates a later round added; a
candidate that round 0 produced is never displaced by expansion.

Two consequences follow, and both are required properties rather than incidental effects:

- **The Builder invariant holds by construction.** After reselection the family totals are at or below
  `memory_candidate_limit`, `topic_memory_candidate_limit` and `experience_candidate_limit`, so the checks at
  `prepared_context.py:164-169` cannot be triggered by expansion. This is why the merge is defined on the same
  allocators instead of as an unbounded union: two individually valid rounds of 16 Memory hits can differ by one
  candidate, and their deduplicated union of 17 would otherwise raise
  `PreparedContextInvariantError("memory-candidate-limit")`.
- **A saturated family cannot grow.** If round 0 already filled a family's ceiling, the merged set truncates back to
  round 0's candidates and that family's later rounds change nothing. Expansion can only add candidates to a family
  whose round-0 admission count is below that family's ceiling — the signal the gate reads before deciding, and the
  reason a no-op expansion is a normal, reported outcome.

Consequently, a round returning *fewer* candidates than its predecessor is ordinary and expected rather than a
degradation trigger.

## Persistence boundary

RFC 0028 states that Context Pack "writes no database or file, enters no Source journal or Memory evidence, starts no
scheduler work, and is not persisted as telemetry" (`docs/en/rfcs/0028_context_pack.md:518-519`). This RFC stays
inside that boundary.

`RecallEffort` — including `truncated_items`, `dropped_items` and the admission counters — is a value computed within a
single `prepare_context` call. It writes no database row, is not a Source observation or Memory evidence, starts no
scheduler work, and is not persisted as telemetry. It is handed to an in-process sink the deployment opts into; a
caller that configures no sink pays nothing and produces no side effect. The aggregate counters that may appear as
`context.build` span attributes are limited to the categories RFC 0028 already permits for metrics
(`docs/en/rfcs/0028_context_pack.md:521-522`).

This is why the proposal deliberately does **not** include a per-entry recall-outcome ledger that survives across
sessions, even though that is the more useful signal. Persisting "this entry was selected" or "this entry lost to the
budget" from the prepare path requires amending the RFC 0028 write-free clause, which is a foundational change and is
being decided elsewhere: #1554 raised exactly this choice, and the maintainer's steer there is to keep prepare
read-only for the first scope. Any cross-session variant of this trace therefore needs its own RFC.

`truncated_items` and `dropped_items` are aggregate counters over one call — never per-entry attribution and never a
verdict about an entry. That distinction is deliberate, because #1554 has already ruled that an entry losing to the byte
budget is **not** a negative result about that entry. This RFC counts the omission only to interpret its own expansion,
records no `candidate_not_selected`-style signal, and adds no field to the HTTP contract.

One correction worth recording, because it bears on how that future RFC must be argued: the fact that
`RelationalRecallTokenEstimator` resolves recall lineage inside prepare (`recall.py:106`) is **not** a precedent for
permitting a write. Both `resolve()` and `estimate()` are reads, and RFC 0028 constrains writes, not work.

## Budget and byte invariance

Expansion can only increase the number of candidates competing for a fixed budget, and it runs before selection and
rendering, which are unchanged. `content_bytes` is still checked against `request.max_bytes`
(`prepared_context.py:207-208`), so the invariant this RFC guarantees is:

```text
content_bytes <= request.max_bytes
```

**Byte-identical output is guaranteed only when the candidate set and the selection are unchanged.** A run in which the
gate reports `sufficient` — the entire no-expansion path — is byte-identical to today's, which is the regression
guarantee. A run that *does* expand is expected to differ: a round-1 candidate may occupy budget headroom that round 0
left unused (adding one item to a 551-byte result produced 785 bytes in a probe against a `max_bytes` of 8000), and
swapping one candidate for another changes the bytes even when the item count is identical (546 to 593 bytes at an
assembly limit of one item). Requiring byte-identity there would reject exactly the useful expansions this RFC exists to
allow. What is guaranteed is that the ceiling is never crossed and that expansion is stopped by the byte budget, not
allowed to override it.

This is also why the evaluation section measures injected bytes rather than asserting their equality.

## Cost model

Per participating searchable family, with reranking disabled and enabled shown separately:

| Situation | Search passes | Query embeddings | RFC 0080 rerank generation calls |
| --- | --- | --- | --- |
| Round 0 sufficient | 1 | 1 if the resolved mode includes the vector channel, else 0 | The deployment's existing 1 per non-empty Memory search |
| One committed expansion | 2 | +0 when the round-0 query vector is reused, else +1 | +0, or +1 per reranked Memory search |
| Two committed expansions | 3 | +0 when reused, else +2 | +0, or +2 per reranked Memory search |

Cumulatively bounded: at most 3 search passes, at most 2 additional query embeddings, and at most 2 additional
generation calls per reranked Memory search. The per-round cost is **not** claimed to grow monotonically, because with
reranking disabled both rounds repeat the same search against the same pool and differ only in the admission floor.

**Query embeddings are real and must be accounted for.** `mode="auto"` resolves to `hybrid` wherever the hybrid
channel is available (`service.py:602-607`), and `MemoryService.search` embeds the query on every call
(`service.py:442`); Topic Memory embeds once per search as well. Three consecutive `auto` searches against a SQLite
backend with a counting embedding stub produced three embedding invocations with reranking disabled. This RFC
therefore makes **reuse part of the design**: the round-0 query vector and embedding profile are threaded into later
rounds through the same internal admission parameter path, so that repeats do not re-embed. When reuse is unavailable,
the round pays the call and `RecallEffort.added_embeddings` records it, so the cost model cannot silently drift.

**No expansion round changes the backend candidate pool size.** Neither `limit` nor `memory_rerank_candidate_limit` is
touched, so `coarse_limit` (`service.py:452`) and `candidate_limit = max(coarse_limit * 4, 32)` (`service.py:457`) are
identical in every round.

**Rerank is Memory-only.** RFC 0080's reranker is constructed for `MemoryService` (`service.py:161`, `:174`, `:489`);
Experience and Topic Memory searches do not rerank. The generation-call column above therefore applies to reranked
*Memory* searches only, and a mixed-family expansion adds no generation call for the other families. Deployments that
enable both features accept the added call explicitly; expansion rounds are skipped when rerank is enabled unless
configuration allows the additional call, and the decision is recorded in `RecallEffort`.

## Failure and degradation

Expansion is committed **per round**, and a round is all-or-nothing:

- Round 0 is committed unconditionally. If round 0 fails, prepare fails exactly as it does today.
- A round `r >= 1` first collects every participating family's results for every scope into a **staged** set. The stage
  is committed into the merged set only if every search in it succeeded.
- If any search in the round raises, the entire stage is discarded and the merged set reverts to the last committed
  state. `RecallEffort.rounds` counts the passes actually executed, and `expansion_actions` records only committed
  rounds.

Without that boundary, "fail open" would not hold: expansion runs per family and per scope, so a later search can fail
after earlier searches have already contributed candidates, and swallowing that error would return a partially expanded
result instead of today's round-0 result. With the boundary, a failed round yields exactly the previous committed
result.

The gate itself fails closed: if `assess` raises, or if configuration is absent, no expansion happens and prepare
proceeds with round 0. The gate never turns a successful prepare into a failure, and never changes `status`. Because
the gate runs before selection, a failure costs at most an extra search, never a lost result.

## Edge cases

- **Empty Scope.** No Memory, no Experience. The gate must not expand: absence of content is not thin recall. The
  gate returns `sufficient` with reason `no-content`, and the result is today's normal empty result.
- **Caller passed `assembly: {"sections": []}`.** RFC 1489 defines this as a normal empty result after validation.
  Expansion does not run.
- **`max_bytes` at its 512-byte floor.** One item may be all that fits. The budget probe reports a budget-bound fit,
  so the gate reports rather than expands; thinness here is a budget property, not a recall property.
- **Duplicate evidence.** Several candidates citing the same evidence identity count once for the distinct-source
  signal, which is computed per family on the identity table above. A low distinct-source count is therefore not by
  itself grounds to expand, and a Memory-only result is not mistaken for a single source because the Memory identity is
  the entry, not the Memory revision.
- **Profile-only request.** `profile` is a section family (`runtime/models.py:195`) but is read via `profiles.latest`
  (`application.py:752-759`) with no search and no admission floor, so it is never an expansion target and a
  profile-only request is never expanded.
- **A saturated family.** If a family's round-0 admission already equals its Builder ceiling, expansion cannot add
  candidates to it; the round is recorded as one that changed nothing rather than being retried.
- **A caller-selected family set.** `assembly.sections` decides which families participate, so a family the caller did
  not select is never searched, is given no output budget, and is never a legal expansion target however thin round 0
  was. If the caller selected Memory only, "add Experience" is not on the table.
- **A Scope with no vector deployment.** Hybrid is unavailable, so `mode` is already `fts` and the cosine baseline does
  not apply; only the lexical-evidence requirement can be relaxed, and no round may attempt a mode change
  (`service.py:598-601`).

## Compatibility and API impact

`PreparedContext` keeps its four fields and `openapi/powercontext.yaml` is unchanged, so `make api-generate` is not
required: the admission counters live on an in-process value of `MemorySearchResult` and never reach the HTTP
projection built by `search_response` (`server/mapping.py:788`). `PreparedContextBuild` is unchanged. The changes are
internal and in-process:

- an optional `admission` parameter and in-process counters on the Memory search entry point;
- an outcome value (hits plus counts) replacing the bare tuple returned by the `experience_recall` and Topic Memory
  recall callbacks, whose signatures are Runtime construction parameters (`composition.py:486`, `application.py:2257`,
  `:2268`);
- new Runtime configuration for the policy, the expansion floors and the optional `RecallEffort` sink.

The feature is off by default and enabled by Runtime configuration.

## Testing

Gate, expander and budget probe are tested as pure functions, including the signal computations for a family with no
score (Experience), a family whose admission admitted nothing, and a family already at its ceiling.

Runtime-level tests assert, for a fixed candidate set and a fixed budget:

- the non-expanded path produces byte-identical output to today;
- `content_bytes <= max_bytes` on the expanded path, and that expansion is stopped by the budget rather than crossing
  it;
- an insufficient round leads to exactly one committed expansion; a second insufficient round stops;
- **a merged set never breaks the Builder invariant**: two rounds of 16 Memory hits differing by one candidate must
  build successfully rather than raise `PreparedContextInvariantError("memory-candidate-limit")`, and no round can
  exceed a family's ceiling;
- a round contributes no new identity → the earlier set stands and `candidates_by_round` is flat;
- a family saturated in round 0 receives no later-round candidate;
- no candidate produced by round 0 is ever displaced by expansion;
- a failure in any search of a round discards that round's entire stage and returns the previous committed result;
- an empty Scope never expands; a profile-only request never expands; an explicitly selected family set is never
  widened;
- the admission counters are reported and, when the sink is absent, no side effect occurs.

Existing homes for these tests are `tests/builtin/runtime/test_prepared_context.py` (pure Builder and rendering
behaviour) and `tests/e2e/test_builtin_runtime.py` (Runtime-level prepare behaviour); the assembly-path counterpart is
`tests/e2e/test_context_text_assembly.py`.

End-to-end acceptance follows RFC 0028's rule that task benefit must be evaluated separately from context size: a
fixed task, model, and `max_bytes` under the RFC 1229 workload contract and the RFC 0081 harness, control
(single-pass prepare) versus treatment (gated prepare), on both SQLite and OceanBase, reporting task success,
injected bytes, `truncated_items`, `dropped_items`, expansion rounds, `added_embeddings`, `added_generation_calls`, and
added latency. The feature is not enabled by default until that comparison shows benefit.

# Drawbacks

- **Added latency on thin queries.** One or two extra searches per prepare, precisely in the case where recall is
  already weak, so the added latency may not buy anything. Reusing the round-0 query vector removes the embedding part
  of that cost but not the search itself.
- **A lowered admission floor admits what the floor was built to reject.** The lexical-evidence requirement and the
  cosine baseline exist to keep weak matches out of a bounded budget; a round that lowers them can displace evidence
  that the default floor would have delivered. This is the main reason the feature ships disabled and must be
  calibrated, not merely switched on.
- **Expanded output can be larger.** Within `max_bytes`, a successful expansion can deliver more bytes than round 0
  would have, which is the intent but also means byte count is no longer a stable property of a query.
- **Expansion is inert for saturated families and for budget-bound queries.** The candidate ceiling caps what any
  round can add, and the budget probe stops expansion when the budget is the constraint. The feature addresses exactly
  one failure mode — thin candidates against a non-binding budget — and should not be sold as a general recall fix.
- **A new tunable surface.** Thresholds that decide "sufficient" are now joined by a second set that decides "relaxed
  enough". Both are easy to set wrong and hard to justify empirically. Shipping them as a versioned policy mitigates
  but does not remove this.
- **Risk of masking a retrieval defect.** If the first round is thin because the index or the embedding is wrong, a
  second round with the same machinery will often also be thin, and the gate adds work without diagnosis.
- **Interaction with rerank.** With RFC 0080 enabled, expansion can add up to two generation calls per prepare.
- **More code in the prepare path**, plus new internal parameters on three search entry points and two recall
  callbacks, for a benefit that this RFC does not yet demonstrate.

# Rationale and alternatives

- **Do nothing.** Cheapest, and defensible: a caller can always raise `max_bytes` or re-query. But that pushes
  application policy into every provider adapter, which is what RFC 0028's motivation argues against, and each
  integration would invent a different retry rule.
- **Let the caller retry with a reworded query.** Same objection, and it additionally asks the caller to do retrieval
  work that RFC 0028 deliberately placed in the Runtime.
- **Lower the admission floor unconditionally**, or raise the Builder candidate ceilings unconditionally. This taxes
  every query, including well-served ones, and admits weaker candidates into a bounded budget. A gate pays only when
  recall is actually thin. For the avoidance of doubt, raising `SearchMemoryRequest.limit` is not among the options: the
  Runtime already requests each family at its ceiling (`application.py:742-743, 761`).
- **Raise `memory_rerank_candidate_limit` in round 2.** Rejected: it sizes the backend request
  (`service.py:452`, `:457`), so it enlarges the candidate pool from 120 to 400 at the prepare-path limit and breaks
  the same-pool guarantee the cost model rests on. An independent retrieval expansion is a different proposal.
- **Accumulate candidates across rounds without reselection.** Rejected: a deduplicated union of two individually valid
  rounds can exceed a family's ceiling and raise `PreparedContextInvariantError("memory-candidate-limit")`. The merged
  set is re-selected through the allocators that already clamp round 0 instead.
- **Assert byte-identical output for expanded runs.** Rejected: `max_bytes` is a ceiling, not a target, so a useful
  expansion legitimately increases delivered bytes within it, and swapping one candidate for another changes the bytes
  at a constant item count. The invariant is `content_bytes <= max_bytes`, and byte-identity is required only on the
  no-expansion path.
- **Leave it to the RFC 0080 listwise reranker.** Rerank selects within the candidate pool; it cannot surface evidence
  that never entered the pool. It is also Memory-only, off by default, and costs a generation call per search.
- **Implement it in the host plugin.** Host-side context plugins already do this. For PowerContext that would move
  selection back out of the Runtime application boundary.
- **Expand across families.** Rejected: RFC 1489 makes family participation a caller decision, and searching an
  unselected family would spend recall and budget the caller did not grant.
- **Expand across time windows.** Not possible in v1: neither `PrepareContextRequest` nor `SearchMemoryRequest`
  carries a time or as-of parameter. Adding one is a separate contract change and belongs in its own RFC.

# Prior art

- **RFC 0080 rerank trace** is the direct precedent for attaching diagnostic detail to an in-process result while
  leaving the HTTP response alone. This RFC follows the same rule, and it follows the same in-process-callback pattern
  the recall-token estimator already uses (`application.py:794-808`).
- **Host-side context plugins** in the OpenClaw ecosystem implement a completeness gate that emits
  `use` / `expand` / `max_expand`, plus an adaptive expansion loop that grows top-K (x2, then x3) and relaxes the age
  window, capped at two rounds. The shape is worth having; the implementation is not worth copying — their gate and
  their compression are regular-expression and keyword scoring with no real semantics, their cross-project contract is
  duck-typed with no schema, and their metrics module is explicitly decoupled from the decisions it measures. The
  bounded-merge and budget-probe parts of this RFC have no counterpart there.
- **Retrieval evaluation practice** (multi-stage recall then rerank) is standard. What is less common, and what this
  RFC borrows from the host-side implementations, is treating *insufficient recall* as a first-class state that
  triggers another bounded attempt rather than as a silent truncation.

# Unresolved questions

1. **Threshold calibration.** What values make the gate fire on genuinely thin recall and stay quiet otherwise? This
   RFC proposes shipping the policy disabled and calibrating against the existing workload suite before enabling it
   anywhere.
2. **Should the gate be per-family or global?** A global assessment is simpler; a per-family one can notice that one
   selected family returned nothing while another did well, which is what the "admitted vs. ceiling" signal already
   hints at. Per-family may be strictly better, but it interacts with RFC 1489's per-section limits in ways that need a
   decision.
3. **How far may the admission floor be lowered, and per channel or per family?** The floor is a real tunable with a
   real failure mode, and it is not the same filter everywhere: the lexical requirement is shared (`search.py:104`) but
   Experience applies it inside its own index (`persistence/experience_index.py:309`), and the cosine baseline is a
   separate constant per family (`memory/fusion.py:29`, `topic_memory/fusion.py:33`). Both the derived
   `required_matches` and the `0.3` baseline are cheap to override once the plumbing exists, but a single global
   expansion floor is simpler to reason about than a per-family one. The gate and the expansion floor should be
   calibrated together against the existing workload suite.
4. **Should query-vector reuse be mandatory or best-effort?** This RFC specifies reuse as part of the design and
   records the residual cost in `RecallEffort.added_embeddings`. Making reuse mandatory would remove an accounting
   branch at the cost of a stricter internal contract between the Runtime and the search entry points.
5. **Where does the cost go in the evaluation report?** RFC 1229 workloads should report added latency, added query
   embeddings and, with rerank enabled, added generation calls, alongside the benefit metrics.
6. **Should a caller be able to opt out per request?** Today the feature would be deployment-wide. A per-request
   escape hatch may be unnecessary complexity, or may be needed by latency-sensitive integrations.

# Future possibilities

- **A time-window parameter on search**, which would let a later RFC add "relax the time window" as a genuine
  expansion action rather than the admission-floor action available today.
- **A retrieval expansion that may change pool size**, if evaluation shows that widening the backend candidate pool
  helps where admission relaxation does not. That would need its own guarantees for cost and for the
  same-pool invariant this RFC relies on.
- **Feeding gate outcomes back into retrieval ranking** — recording which candidates were selected and which lost, so
  retrieval quality can evolve. Deliberately out of scope here, and it must respect the boundary in RFC 0051 and
  #1425: no automatic decay, and no automatic retirement.
- **Reporting expansion in the public contract**, if a future profile makes recall effort part of what an Agent or an
  operator is expected to see. That would be a separate API change.
- **Sharing the gate with other bounded-selection operations**, if PowerContext grows a second operation that also
  selects evidence under a budget.
