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

This RFC lets the Runtime, before rendering, cheaply assess whether the candidate set is sufficient for the query and,
when it is not, run at most two controlled expansion rounds — raising the per-family search limit and, where the search
mode is `auto`, switching to `hybrid` — and then re-select within the **same** caller-provided total budget.

The gate uses no model by default, never raises the delivered byte count, never changes the public
`PreparedContext` contract, and fails open to current behaviour.

# Motivation

RFC 0028 deliberately collapses internal reasons (no Memory, no match, error) into one normal empty result for the
caller. That decision stands and is not revisited here. Collapsing the *reported reason* is a different matter from
having no *recovery path at all*.

Concretely, a query whose relevant evidence sits just outside the first recall round — a differently worded entry, or
an entry that the `auto` search mode ranked below the cut — currently receives a silently thinner context, and every
integration observes the same thinness. RFC 1489 gave callers control over which families participate, their order, and
a per-family item limit; it did not address what the Runtime should do when the participating families together return
too little to be useful.

The current pipeline is single-pass by construction:

```text
one search per participating family
  -> clamp to the Builder candidate limits
  -> select and fit to max_bytes
  -> render
```

Two facts about the existing implementation make a second, bounded look both cheap and safe:

- The per-family search limits are already fixed constants on `PreparedContextBuilder`
  (`prepared_context.py:103-110`: `memory_candidate_limit = 16`, `topic_memory_candidate_limit = 8`,
  `experience_candidate_limit = 8`), and exceeding them raises `PreparedContextInvariantError`
  (`prepared_context.py:165-169`). There is therefore spare headroom *inside* the existing invariants that a first
  round does not use: the default `SearchMemoryRequest.limit` is `10`, below the Memory candidate ceiling of `16`.
- `ScopedContextApplication._prepare` (`application.py:727`), via `_recall_scope` (`application.py:814`), calls memory
  search with a hard-coded `mode="auto"` (`application.py:849`). A query served by one retrieval channel in round one
  can be served by both in round two once that mode is parameterised — no new request field is required.

A third observation motivates the reporting half of this RFC: today nothing counts the items that lose to the budget.
In the non-assembly path `_fit_entry` truncates to `max_entry_content_bytes`, returns the candidate when it fits
(`prepared_context.py:461`), and returns `None` — dropping the item whole — only when the source is shorter than
`_MIN_TRUNCATED_CONTENT_BYTES` (`prepared_context.py:462-463`). In the assembly path `fit_context_text_item` does the
same, discarding an item when nothing usable fits (`prepared_text.py:103-104`).
`truncated` is rendered per item, but neither the truncations nor the whole-item drops are counted anywhere. Making
them countable is a small change and is a prerequisite for evaluating this feature honestly.

This RFC is not a claim that more recall is always better. It is a claim that *conditional* additional recall — paid
only when the first round looks thin — is worth evaluating under the existing workload infrastructure.

# Guide-level explanation

## Mental model

```text
Stage A  recall
           search participating families (round 0)
           RecallSufficiencyGate.assess(candidates, query)
             sufficient            -> Stage B
             insufficient, r < 2   -> expand (bounded) and search again
             insufficient, r == 2  -> Stage B with what we have
Stage B  select + section assembly + budget fitting + render   (unchanged)
Stage C  report recall effort and omission                     (in-process)
```

Expansion may only change **which candidates compete**. It never changes the output budget, the trust wrapper, the
citation form, or the set of families the caller selected.

## What the gate looks at

The gate is deliberately cheap and model-free. Signals, all available from round-zero results without further I/O:

| Signal | What it detects |
| --- | --- |
| Number of candidates returned vs. the per-family limit | A family returned almost nothing. |
| Top-1 score and the gap to the mean | One plausible hit surrounded by noise, or no clear winner. |
| Lexical overlap between query terms and the top candidates | Hits matched on stopwords or on one shared token only. |
| Number of families that returned at least one candidate | An assembly that selected three families and got results from one. |
| Distinct cited Artifact revisions among candidates | Many candidates that are really the same evidence. |

Thresholds are deployment configuration, not request parameters, and are recorded by version in the trace so a run can
be reproduced.

## What expansion does

| Round | Action | Precondition |
| --- | --- | --- |
| 1 | Raise the per-family search `limit` toward the Builder candidate ceiling; switch `mode` from `auto` to `hybrid`. | Round 0 assessed insufficient. |
| 2 | Widen the gate threshold (accept the best available evidence) and, where RFC 0080 rerank is enabled, raise its candidate limit. | Round 1 assessed insufficient. |

Each round is strictly more expensive than the last, and the count is capped at two, so the worst-case cost of a
prepare is bounded and predictable.

**Expansion never adds a family.** RFC 1489 states that `assembly.sections` determines which families participate, and
that a family the caller did not select is not searched and is not given output budget. Silently searching an
unselected family would violate that contract, so family membership is out of scope for expansion. If a caller omits
`assembly` entirely, the Runtime's existing default family selection applies unchanged and is also not expanded.

## What you can observe

Following the precedent of the RFC 0080 `rerank` trace, the gate result is attached to the in-process build result and
is **not** added to the HTTP v1 response. `PreparedContextBuild` gains one optional field:

```python
@dataclass(frozen=True)
class PreparedContextBuild:
    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    recall_effort: RecallEffort | None = None
```

```python
@dataclass(frozen=True)
class RecallEffort:
    policy: str                       # versioned policy id, e.g. "powercontext.recall-gate.v1"
    rounds: int                       # 0, 1, or 2
    gate_reason: str                  # "sufficient" | "thin-candidates" | "weak-top-1" | ...
    expansions: tuple[str, ...]       # e.g. ("limit", "hybrid")
    candidates_by_round: tuple[int, ...]
    truncated_items: int              # delivered but cut; not counted today
    dropped_items: int                # lost to the budget whole; not counted today
```

The last two fields are new observability rather than new behaviour. Neither number is counted anywhere today
(`prepared_context.py:462-463`, `prepared_text.py:103-104`), and both are needed to tell "we found the evidence" apart
from "we found it and the budget ate it" — a distinction that matters when interpreting an expansion that changed
nothing.

Process-in-process trace stays in the process. Benchmarks and `powercontext doctor`-style diagnostics may read it; the
public contract is unchanged.

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

Round 0 returns three Memory candidates, one with a usable score. The gate assesses `weak-top-1`, expands once (limit
raised, `mode` switched to `hybrid`), and round 1 returns a further candidate that carries the actual verification
instruction. Selection and rendering then proceed exactly as today, inside the same 8000 bytes.

If round 1 had returned nothing better, prepare would have delivered the round-0 result — the behaviour of today — with
`rounds: 2` and a `gate_reason` explaining why.

## What this RFC does and does not do

| It does | It does not |
| --- | --- |
| Assess sufficiency with model-free signals | Add a model call to assembly (RFC 1489 keeps assembly model-free) |
| Expand at most twice, with monotonically increasing cost | Retry indefinitely or loop |
| Search harder inside the existing candidate ceilings | Exceed `memory_candidate_limit` / `experience_candidate_limit` |
| Keep the caller's `max_bytes` as the only output budget | Inflate the delivered byte count |
| Report effort in the in-process trace | Change `PreparedContext(schema, status, content, content_bytes)` |
| Degrade to today's behaviour on any error | Make expansion a new failure mode |

# Reference-level explanation

## Current behaviour

`PreparedContextBuilder` (`src/powercontext/builtin/runtime/prepared_context.py`) declares the ceilings:

```python
memory_candidate_limit = 16
topic_memory_candidate_limit = 8
experience_candidate_limit = 8
entry_limit = 8
topic_memory_entry_limit = 8
experience_entry_limit = 2
max_entry_content_bytes = 2000
```

`build_scopes_result()` raises `PreparedContextInvariantError` when a family exceeds its candidate limit, then either
routes to `_build_text()` when `request.assembly` is present, or interleaves, fits, and renders. When nothing fits it
returns `PreparedContext(status="empty", content=None, content_bytes=0)`; otherwise `status="ready"` with the rendered
content and its UTF-8 length. If the rendered length exceeds `request.max_bytes`, it raises
`PreparedContextInvariantError("output-budget")`.

`PrepareContextRequest` carries `query`, `max_bytes` (512–32768, default 8000) and an optional `assembly`.
`SearchMemoryRequest` carries `query`, `limit` (default 10), `mode` (`fts` / `vector` / `hybrid` / `auto`, default
`auto`), and `tag_filter`. There is no time-window or as-of parameter on either request.

## New components

1. **`RecallSufficiencyPolicy`** — a frozen, versioned value object holding the thresholds, the maximum round count,
   and the per-round expansion descriptors. Constructed from Runtime configuration; defaults preserve today's
   behaviour when the feature is disabled.
2. **`RecallSufficiencyGate`** — a pure function `assess(candidates, query, policy) -> GateAssessment`. No I/O, no
   model call, no clock access beyond what the candidates already carry.
3. **`RecallExpander`** — a pure function mapping `(round, policy) -> SearchPlan`, where `SearchPlan` describes the
   `limit` and `mode` to use for the next round. It never names a family, so it cannot violate the assembly contract.
4. **`RecallEffort`** — the trace value described above, attached to `PreparedContextBuild`.

All four live under `src/powercontext/builtin/runtime/`. The gate and expander are pure and are tested directly without
a database.

## Where the loop goes

The loop belongs in the Runtime layer that currently performs the per-family searches and then calls
`PreparedContextBuilder.build_scopes_result()`. The Builder itself stays free of I/O, persistence, and reranking, as
its docstring states; it receives the candidates from the winning round, exactly as it does today.

## Persistence boundary

RFC 0028 states that Context Pack "writes no database or file, enters no Source journal or Memory evidence, starts no
scheduler work, and is not persisted as telemetry" (`docs/en/rfcs/0028_context_pack.md:518-519`). This RFC stays
inside that boundary.

`RecallEffort` — including `truncated_items` and `dropped_items` — is a value computed within a single
`prepare_context` call and attached to the in-process build result. It writes no database row, is not a Source
observation or Memory evidence, starts no scheduler work, and is not persisted as telemetry. A caller that never
inspects the in-process result pays nothing and produces no side effect.

This is why the proposal deliberately does **not** include a per-entry recall-outcome ledger that survives across
sessions, even though that is the more useful signal. Persisting "this entry was selected" or "this entry lost to the
budget" from the prepare path requires amending the RFC 0028 write-free clause, which is a foundational change and is
being decided elsewhere: #1554 raised exactly this choice, and the maintainer's steer there is to keep prepare
read-only for the first scope. Any cross-session variant of this trace therefore needs its own RFC.

One correction worth recording, because it bears on how that future RFC must be argued: the fact that
`RelationalRecallTokenEstimator` resolves recall lineage inside prepare (`recall.py:106`) is **not** a precedent for
permitting a write. Both `resolve()` and `estimate()` are reads, and RFC 0028 constrains writes, not work.

## Budget invariance

Expansion can only increase the number of candidates competing for a fixed budget. Because selection and rendering
happen after the final round and are unchanged, and because `content_bytes` is still checked against
`request.max_bytes`, the delivered size cannot grow as a result of expansion. A run that expands and then fits the same
number of items produces byte-identical output to a run that did not need to expand.

## Cost model

| Situation | Searches | Model calls |
| --- | --- | --- |
| Round 0 sufficient | 1 per participating family | 0 |
| One expansion | 2 per participating family | 0 (or 1 per family if RFC 0080 rerank is enabled) |
| Two expansions | 3 per participating family | 0 (or 2 per family if rerank is enabled) |

The rerank interaction is the one place where expansion can add model cost, because RFC 0080 performs a structured
generation call per non-empty reranked search. Deployments that enable both features accept that cost explicitly:
expansion rounds are skipped when rerank is enabled unless configuration allows the additional call. This is recorded
in `RecallEffort`.

## Failure and degradation

Every error path degrades to today's behaviour: gate raises, expansion raises, a round returns fewer candidates than
the previous one, or configuration is absent. The gate never turns a successful prepare into a failure, and never
changes `status`. Because the gate runs before selection, a failure costs at most an extra search, never a lost result.

## Edge cases

- **Empty Scope.** No Memory, no Experience. The gate must not expand: absence of content is not thin recall. The
  gate returns `sufficient` with reason `no-content`, and the result is today's normal empty result.
- **Caller passed `assembly: {"sections": []}`.** RFC 1489 defines this as a normal empty result after validation.
  Expansion does not run.
- **`max_bytes` at its 512-byte floor.** One item may be all that fits. Expansion still may not change the budget; a
  thin result here is a budget property, not a recall property, and the gate should report rather than expand.
- **Duplicate evidence.** Several candidates citing the same Artifact revision count once for the distinct-source
  signal, so a low distinct-source count is not by itself grounds to expand.

## Compatibility and API impact

None. `PreparedContext` keeps its four fields; `openapi/powercontext.yaml` is unchanged, so `make api-generate` is not
required. `PreparedContextBuild` gains an optional field with a `None` default, which is an internal, in-process
object. The feature is off by default and enabled by Runtime configuration.

## Testing

Gate and expander logic are tested as pure functions. Runtime-level tests assert, for a fixed candidate set and a fixed
budget: the non-expanded path produces byte-identical output to today; an insufficient round leads to exactly one
expansion; a second insufficient round stops; an empty Scope never expands; and an explicitly selected family set is
never widened.

Existing homes for these tests are `tests/builtin/runtime/test_prepared_context.py` (pure Builder and rendering
behaviour) and `tests/e2e/test_builtin_runtime.py` (Runtime-level prepare behaviour); the assembly-path counterpart is
`tests/e2e/test_context_text_assembly.py`.

End-to-end acceptance follows RFC 0028's rule that task benefit must be evaluated separately from context size: a
fixed task, model, and `max_bytes` under the RFC 1229 workload contract and the RFC 0081 harness, control
(single-pass prepare) versus treatment (gated prepare), on both SQLite and OceanBase, reporting task success,
injected bytes, `truncated_items`, `dropped_items`, expansion rounds, and added latency. The feature is not enabled by
default until that comparison shows benefit.

# Drawbacks

- **Added latency on thin queries.** One or two extra searches per prepare, precisely in the case where recall is
  already weak, so the added latency may not buy anything.
- **A new tunable surface.** Thresholds that decide "sufficient" are easy to set wrong and hard to justify
  empirically. Shipping them as a versioned policy mitigates but does not remove this.
- **Risk of masking a retrieval defect.** If the first round is thin because the index or the embedding is wrong, a
  second round with the same machinery will often also be thin, and the gate adds work without diagnosis.
- **Interaction with rerank.** With RFC 0080 enabled, expansion can double or triple generation calls per prepare.
- **More code in the prepare path** for a benefit that this RFC does not yet demonstrate.

# Rationale and alternatives

- **Do nothing.** Cheapest, and defensible: a caller can always raise `max_bytes` or re-query. But that pushes
  application policy into every provider adapter, which is what RFC 0028's motivation argues against, and each
  integration would invent a different retry rule.
- **Let the caller retry with a reworded query.** Same objection, and it additionally asks the caller to do retrieval
  work that RFC 0028 deliberately placed in the Runtime.
- **Raise the default search limit unconditionally** (for example, `limit` 10 to 16, or `mode` from `auto` to
  `hybrid`). This taxes every query, including well-served ones, and adds noise to a bounded budget. A gate pays only
  when recall is actually thin.
- **Leave it to the RFC 0080 listwise reranker.** Rerank selects within the candidate pool; it cannot surface evidence
  that never entered the pool. It is also off by default and costs a generation call per search.
- **Implement it in the host plugin.** Host-side context plugins already do this. For PowerContext that would move
  selection back out of the Runtime application boundary.
- **Expand across families.** Rejected: RFC 1489 makes family participation a caller decision, and searching an
  unselected family would spend recall and budget the caller did not grant.
- **Expand across time windows.** Not possible in v1: neither `PrepareContextRequest` nor `SearchMemoryRequest`
  carries a time or as-of parameter. Adding one is a separate contract change and belongs in its own RFC.

# Prior art

- **RFC 0080 rerank trace** is the direct precedent for attaching diagnostic detail to an in-process result while
  leaving the HTTP response alone. This RFC follows the same rule.
- **Host-side context plugins** in the OpenClaw ecosystem implement a completeness gate that emits
  `use` / `expand` / `max_expand`, plus an adaptive expansion loop that grows top-K (x2, then x3) and relaxes the age
  window, capped at two rounds. The shape is worth having; the implementation is not worth copying — their gate and
  their compression are regular-expression and keyword scoring with no real semantics, their cross-project contract is
  duck-typed with no schema, and their metrics module is explicitly decoupled from the decisions it measures.
- **Retrieval evaluation practice** (multi-stage recall then rerank) is standard. What is less common, and what this
  RFC borrows from the host-side implementations, is treating *insufficient recall* as a first-class state that
  triggers another bounded attempt rather than as a silent truncation.

# Unresolved questions

1. **Threshold calibration.** What values make the gate fire on genuinely thin recall and stay quiet otherwise? This
   RFC proposes shipping the policy disabled and calibrating against the existing workload suite before enabling it
   anywhere.
2. **Should the gate be per-family or global?** A global assessment is simpler; a per-family one can notice that one
   selected family returned nothing while another did well. Per-family may be strictly better, but it interacts with
   RFC 1489's per-section limits in ways that need a decision.
3. **Does `hybrid` switching belong in round 1?** On a Scope with no vector deployment, switching `mode` is a no-op
   and the round costs latency for nothing. The expander should probably skip mode switching when no vector channel is
   configured.
4. **Where does the cost go in the evaluation report?** RFC 1229 workloads should report added latency and, with
   rerank enabled, added generation calls, alongside the benefit metrics.
5. **Should a caller be able to opt out per request?** Today the feature would be deployment-wide. A per-request
   escape hatch may be unnecessary complexity, or may be needed by latency-sensitive integrations.

# Future possibilities

- **A time-window parameter on search**, which would let a later RFC add "relax the time window" as a genuine
  expansion action rather than the mode/limit actions available today.
- **Feeding gate outcomes back into retrieval ranking** — recording which candidates were selected and which lost, so
  retrieval quality can evolve. Deliberately out of scope here, and it must respect the boundary in RFC 0051 and
  #1425: no automatic decay, and no automatic retirement.
- **Reporting expansion in the public contract**, if a future profile makes recall effort part of what an Agent or an
  operator is expected to see. That would be a separate API change.
- **Sharing the gate with other bounded-selection operations**, if PowerContext grows a second operation that also
  selects evidence under a budget.
