- Proposal Name: `decision_model_rerank_seam`
- Start Date: 2026-09-26
- RFC PR: [oceanbase/powercontext#1745](https://github.com/oceanbase/powercontext/pull/1745)
- Related RFCs: [RFC 0080](0080_memory_search_reranking.md), [RFC 0014](0014_memory_layer_design.md)

# Summary

This RFC adds an optional Memory reranking policy that reuses PowerContext's cross-family decision model port as a
second `MemoryReranker` implementation behind the reranking seam established by RFC 0080. Instead of one listwise
generation request, it asks the decision model one narrow keep/drop decision per coarse candidate, preserves the coarse
order among kept candidates, and emits only coarse integer ranks, so every selected hit keeps its exact Artifact,
entry, and Revision identity. The decision model is invoked only from deterministic Runtime code on the search path and
is never exposed as a model-callable tool. The policy is opt-in and disabled by default. Because the rerank stage is
fail-closed under RFC 0080, this RFC also separates the decision role's *runtime failure policy* from the existing
fail-open envelope, so the reranker fails closed while current advisory consumers keep failing open.

# Motivation

RFC 0080 introduced a provider-neutral rerank seam: an ordered, bounded coarse pool produced by fusion is handed to a
`MemoryReranker`, which returns a sparse subset of original ranks, and every selected hit keeps its exact identity. It
also shipped exactly one policy — a listwise structured generation request that reuses the configured generation
model.

PowerContext already exposes a **cross-family decision model port**. It answers a single narrow yes/no/abstain question
against a small, explicit request from deterministic Runtime code, and it is used by internal runtime steps rather than
by model-callable tooling. Some deployments have a decision backend available but no suitable listwise generation
behaviour, and there is value in being able to rerank from that decision capability without adopting a second listwise
prompt policy.

The seam is already the right place for this: it is provider-neutral, it preserves identity by construction, and it is
composable. Adding a second `MemoryReranker` implementation lets a deployment choose the reranking policy it can
actually support.

Expected outcome:

- an opt-in, default-off alternative rerank policy selectable per deployment;
- RFC 0080-compatible identity preservation and fail-closed failure behaviour;
- no new persistence, no new public HTTP/OpenAPI surface, and no change to the coarse pool's membership.

This is a **seam**, not a quality claim. This RFC does **not** assert that decision reranking retrieves better evidence
than the listwise policy or than coarse order. There is no evaluation authorising such a claim, and the RFC is
deliberately silent on it.

# Guide-level explanation

## What "decision reranking" means

Decision reranking is a second-stage selection policy for Memory search. The first stage is unchanged: lexical and
vector channels retrieve candidates and Reciprocal Rank Fusion produces a coarse order. The second stage differs from
the listwise policy in *how* it selects:

- the listwise policy sends the whole bounded candidate pool to one structured generation request and asks for a sparse
  ordered subset of ranks;
- decision reranking walks the coarse candidates one at a time and asks one narrow keep/drop question per candidate —
  "does this candidate answer the query?" — then keeps the candidates whose answer is *yes*, in coarse order, up to the
  caller's limit.

Because the decision model returns a single verdict for a single request, decision reranking is inherently
**per candidate**. It is not a listwise selection. This is the defining property of the policy and the source of both
its flexibility and its cost (see below).

## How a user should think about it

Reranking remains a **deployment policy**, exactly as in RFC 0080. It is off by default. Enabling it is an
assembly-time choice: a deployment supplies a `MemoryReranker` through the same injection path RFC 0080 already
defines, and that choice applies even when the environment rerank flag is false. No new environment switch or
configuration field is introduced. The search request API does not change and `limit` still means the maximum number of
final hits.

When decision reranking is enabled, a deployment should expect:

- the returned hits are always a subset of the same coarse pool, in coarse order, up to `limit`;
- the model selects positions, never identities — it never supplies Artifact IDs, entry IDs, scores, or citations, and
  it cannot modify stored Memory;
- the policy applies only to non-empty reranked searches; with no candidates, search returns quickly as it does today.

## The pivotal stance: a Runtime step, never a tool

The decision model is invoked **only** by deterministic Runtime code inside the search path. It is **never** registered
in the model-callable tool catalog, and it is never offered to an agent as a function to call.

This is a design decision, not an implementation detail:

- The rerank selection is a deterministic composition step whose inputs and outputs are PowerContext-owned. Keeping it
  in Runtime code keeps the contract small, testable, and independent of any tool schema.
- Exposing it as a callable tool would let a model recursively drive reranking, which multiplies cost and blast radius
  and makes the contract impossible to govern or bound.
- RFC 0080 already treats the rerank selection as a bounded, validated, non-authoritative step. Decision reranking
  preserves that property: the model proposes positions, PowerContext validates and resolves them.

## Failure and cost expectations

Two failure behaviours are deliberately **not** symmetrical with an advisory decision:

- **Enabled but not runnable is a configuration error.** If decision reranking is enabled without a usable decision
  backend, the Runtime fails at startup with a configuration error. This mirrors RFC 0080, which fails startup when
  reranking is enabled without a reranker or a generation model. The rationale is the same: a misconfigured reranker
  must be loud, not silently absent.
- **A backend failure at search time is not swallowed.** Provider timeouts, unavailable providers, and invalid
  structured output are not silently converted to a coarse result. The idempotent search call may be retried, but the
  caller observes a degraded rerank rather than a quiet fallback to coarse order.

Cost is the main trade-off. Decision reranking issues **one decision request per candidate**, bounded by the candidate
limit, whereas the listwise policy issues one request per search. Keep decision reranking disabled when model latency
and token cost matter more than the added selection stage.

# Reference-level explanation

## Relationship to RFC 0080

RFC 0080 defines the seam and its contract. This RFC adds a second implementation of the same port and must not change
any of RFC 0080's guarantees. The alignment is:

| RFC 0080 element | This RFC |
| --- | --- |
| `MemoryReranker` port (`policy_id`, `rerank(query, candidates, limit)`) | Reused unchanged; a new implementation is added behind it. |
| `MemoryRerankDecision` (`selected_ranks`, `usage`, `discarded_rank_count`, `used_fallback`) and the in-process trace | Reused unchanged; no field changes. |
| Coarse pool membership and rank→hit resolution | Unchanged. The adapter emits ranks only; the service resolves them. |
| Identity preservation (model never supplies IDs/scores/citations) | Preserved; the adapter emits coarse integer positions only. |
| Default-off deployment policy | Preserved; the new policy is opt-in and default off. |
| Injection path (an injected `MemoryReranker` is an application composition choice applied even when the environment flag is false) | Reused as the enabling mechanism; no new configuration field is added. |
| Fail-closed failure behaviour | Preserved and made explicit for this policy (see *Failure-policy separation* and *Assembly-time versus run-time failure*). |
| No persistence, migration, or HTTP/OpenAPI change | Preserved. |

RFC 0080 does not require a new environment switch to add a reranker implementation; it already defines the enabling
path:

> An injected reranker is an application composition choice and is applied even when the environment flag is false.
> This supports tests and deployments with a provider-specific adapter while keeping environment-driven composition
> explicit.

Decision reranking is a second `MemoryReranker` implementation, so it uses that same path: a deployment supplies the
implementation through application composition, and the choice applies even when the environment rerank flag is false.
Consequently this RFC adds **no** new configuration field and **no** new environment variable, does **not** revive the
existing zero-consumer `MemoryRerankMode` enum (doing so would place a second switch beside the `memory_rerank_enabled`
boolean), and changes **none** of RFC 0080's configuration-contract text.

## The decision reranker adapter

The new implementation is a `MemoryReranker`. Per RFC 0080 it receives the normalized query, the ordered coarse
candidates, and the final limit, and returns a `MemoryRerankDecision`.

```text
rerank(query, candidates, limit):
    validate bounds (len(candidates), min(limit, len(candidates)))
    kept   = []
    usages = []
    for rank, candidate in enumerate(candidates, start=1):
        result = await decision_model.evaluate(DecisionRequest(
            decision_kind = memory.rerank,      # a low-cardinality consumer label
            question      = "does this entry contain evidence that answers the query?",
            subject       = candidate.text,
            evidence      = (query,),
        ))
        usages.append(result.usage)
        if result.used_fallback:                 # DecisionResult.used_fallback: backend degraded -> fail closed
            raise RerankFailure()
        if result.outcome is keep_on:            # keep_on defaults to YES
            kept.append(rank)
        if len(kept) == limit:
            break
    selected = tuple(kept) or fallback_ranks(len(candidates), limit)
    return MemoryRerankDecision(
        selected_ranks       = selected,
        usage                = sum_usages(usages),
        discarded_rank_count = len(candidates) - len(selected),   # candidates not in the final selection
        used_fallback        = not kept,                          # MemoryRerankDecision field: built-in fallback used
    )
```

Notes:

- **Granularity.** `DecisionModel.evaluate` returns a single `DecisionOutcome` (`yes`/`no`/`abstain`) for one request.
  It cannot express a sparse ordered subset of ranks. Decision reranking therefore cannot be listwise; it is one
  bounded keep/drop decision per candidate.
- **Identity.** The adapter emits at most `limit` coarse integer ranks. The service resolves them back to the original
  `MemoryHit` objects. No candidate is added, removed, or re-identified.
- **`abstain` versus backend failure.** A healthy backend that abstains on one candidate is treated conservatively as a
  keep (do not drop evidence). A *failed* backend (`used_fallback = true`) is not a verdict and is not treated as a
  keep; it fails the rerank closed (see below).
- **Two different `used_fallback` fields, with opposite meanings.** The adapter *reads* `DecisionResult.used_fallback`,
  the backend-degradation marker: `true` means the decision backend failed, so the verdict is not trustworthy and the
  rerank fails closed. It *writes* `MemoryRerankDecision.used_fallback`, a different field defined by RFC 0080 that means
  "the built-in coarse fallback ranks were used because no valid selection remained". The two fields share a name but
  mean the opposite; the adapter must never conflate them. This is the read/write pair behind the *abstain versus
  backend failure* rule above.
- **`discarded_rank_count` is computed, not zero.** The adapter produces its own ranks and does not run RFC 0080's
  rank-normalisation, so it must report this field explicitly as the number of coarse candidates excluded from the final
  selection: `len(candidates) - len(selected)`. This includes the fallback case, where `selected` is `1..min(limit, n)`.
  A hard-coded `0` is incorrect, because the existing listwise policy records a real computed value for this field.
- **Fallback ranks.** When nothing is kept, the adapter falls back to coarse ranks `1..min(limit, n)`, matching the
  built-in fallback of the listwise policy and the "only when no valid rank remains" rule.
- **Usage.** `MemoryRerankDecision.usage` is a single portable usage value; the adapter must aggregate the per-candidate
  usages rather than reporting only one.

## Failure-policy separation

The decision port already applies a **fail-open envelope** to its handle: any non-cancellation exception from the
backend is converted into an `abstain` verdict carrying `used_fallback = true`, and `CancelledError` is re-raised so
cancellation still propagates. This is correct for advisory consumers: a misconfigured advisory decision must never
block the write or recall path it decorates.

Reusing that envelope for the rerank role would be a defect. A backend outage would become an `abstain`, the adapter
would treat it as "keep", and the search would silently return coarse order — precisely the "degraded quality hidden as
normal output" that RFC 0080 forbids ("They are not silently converted to coarse results").

This RFC therefore introduces a **role-level runtime failure policy**, declared by the consumer role and resolved by
composition:

- a `DecisionFailurePolicy` with two values, `fail_open` and `fail_closed`;
- the rerank role is `fail_closed`;
- advisory consumers remain `fail_open`.

Invariants that must hold:

- The single fail-open implementation point is preserved. Fail-open is still implemented once; the fail-closed role
  simply does **not** wrap its handle in it, rather than introducing a second degradation path.
- The `except Exception` degradation and the `CancelledError` pass-through semantics do not change.
- Fail-open remains the default for every consumer that does not opt into fail-closed.
- No new dependency is introduced; the failure policy is a standard-library enum.
- Dependency direction is unchanged (Runtime may depend on the Memory family; not the reverse).

The separation happens at the **wrapping** layer, not at the backend layer. A single decision backend instance may serve
both roles: the advisory/gate role wraps its handle in the fail-open envelope, while the rerank role uses the unwrapped
handle. For that reason decision reranking **may reuse the same decision backend configuration that other decision
consumers use**; a per-backend policy would be self-contradictory, because the same backend legitimately serves roles
with opposite failure preferences. What is required instead is that enabling decision reranking with **no usable
decision backend at all** is a startup configuration error (see below).

## Assembly-time versus run-time failure

These are two orthogonal dimensions and both must be stated together, so that "fail-closed" is not read as "enabled
without a backend still runs":

- **Assembly (composition time):** enabling decision reranking without any usable decision backend is a startup
  configuration error, consistent with RFC 0080.
- **Run time:** a backend failure or timeout during a search propagates; it is not degraded to coarse order.

So the rerank role is "fail-fast at assembly, fail-closed at run time", while advisory consumers are "fail-fast at
assembly (only when configured) and fail-open at run time".

## Cost accounting

RFC 0080's policy costs one generation request per non-empty reranked search, and the search path records a fixed
`generation_calls` of one. Decision reranking costs up to one decision request **per candidate**, bounded by the
candidate limit. A fixed accounting value therefore undercounts decision reranking and makes its cost un-observable.

This RFC fixes the accounting convention: a routed decision-reranked search must record the **actual number of decision
calls that occurred**, accumulated during the scan (an early break at the limit records only the calls actually made;
the fallback case records the full scan). No other convention is acceptable — in particular, "one logical rerank
operation" is rejected, because it is exactly the "fixed `1` while issuing N" that hides the true cost.

## Concurrency and consistency

Every candidate stays anchored to one exact, immutable Memory Revision. Read-only search does not hold the scope
mutation lock while calling the decision model, so slow decision calls do not serialize concurrent searches in the same
scope. A concurrent Memory mutation may make a result refer to the immediately preceding Revision; that result is still
an exact, valid citation and cannot mix hit identity across Revisions. This mirrors RFC 0080's concurrency contract.

## Configuration and compatibility

- There is **no** new configuration field and **no** new environment switch. Decision reranking is enabled by injecting
  a `MemoryReranker` in application composition, reusing RFC 0080's injection path. The default keeps today's behaviour
  byte-for-byte identical: no reranker is assembled, no decision call is made, and search returns the coarse order.
- No new persisted table, projection, cursor, or Artifact Revision; enabling or disabling requires no migration and does
  not change embedding profile identity.
- The HTTP v1 response is unchanged; `openapi/powercontext.yaml` and generated clients do not change.

## Observability

The in-process rerank trace continues to carry the versioned policy ID, exact coarse candidate hits, selected original
ranks, discarded-rank and fallback diagnostics, and portable usage. The HTTP v1 mapping continues to return only final
hits. The decision policy's own consumer label is a low-cardinality constant shared by the Runtime, so it appears
consistently in the trace and in structured logs.

# Drawbacks

- **Cost and latency.** Up to one decision request per candidate is materially more expensive than one listwise request
  per search. For large candidate limits this is the dominant drawback.
- **Loss of listwise context.** Per-candidate questions cannot see all candidates at once, so the model cannot compare
  candidates directly. Selection quality is not claimed to improve over listwise or over coarse order.
- **A second role failure policy.** Introducing `DecisionFailurePolicy` adds a concept to the decision port. It is
  justified by RFC 0080's fail-closed requirement, but it is still extra surface.
- **Untuned backend.** The decision model is not tuned for reranking. Deployments should not read this policy as a
  quality upgrade.
- **Data sensitivity.** Candidate text is sent to the decision backend; the trace retains candidate text and must be
  handled as sensitively as normal search output.

# Rationale and alternatives

**Why do this at all.** The rerank seam is provider-neutral and already the correct composition point. A deployment
that has a decision backend but no listwise generation behaviour gains a way to rerank without adopting a second
listwise prompt policy, and the change touches no persistence or public API.

**Alternative: reuse the existing single fail-open handle for the rerank role.** Rejected. RFC 0080 requires the rerank
stage to be fail-closed; wrapping it in the fail-open envelope would silently turn backend outages into coarse results.

**Alternative: put the failure policy on the port or on the backend instance.** Rejected. A single backend instance may
serve roles with opposite failure preferences, so a per-backend policy is self-contradictory; and the willingness to
tolerate degradation is a property of "what this role should do when the backend fails", not of the backend's
capability. Pinning it to the port would also force every implementer to declare it, enlarging a frozen contract.

**Alternative: make decision reranking listwise by asking the model for several ranks at once.** Rejected. The decision
port returns a single verdict per request; there is no bounded, validated way to read a sparse rank subset from it
without inventing a new schema, which is out of scope.

**Alternative: add a new configuration field, or revive the zero-consumer `MemoryRerankMode` enum, to select the
implementation.** Rejected. RFC 0080's injection path already lets a deployment choose a `MemoryReranker`
implementation, so no selector is required; reviving `MemoryRerankMode` would place a second switch beside the existing
`memory_rerank_enabled` boolean, and adding a field would change RFC 0080's configuration contract. An environment
switch remains only a possible future convenience (see *Future possibilities*).

**Impact of not doing this.** The seam stays single-policy. Deployments without a listwise generation policy cannot
rerank, and there is no way to exercise the seam with a non-listwise implementation.

# Prior art

Within PowerContext: RFC 0080 defines the rerank seam, its identity-preservation rule, its default-off deployment
policy, and its fail-closed failure behaviour; RFC 0014 defines Memory's hybrid retrieval and RRF fusion that produce
the coarse pool. The cross-family decision model port and its fail-open envelope are existing internal Runtime
contracts that this RFC composes rather than replaces.

As a general retrieval pattern, "retrieve a bounded first-stage pool, then re-select from it in a second stage" is
well established; the decision-theoretic variant here — a sequence of independent keep/drop decisions over candidates —
is a straightforward instance of that pattern.

No external tool, library, or framework is proposed as the source of this design; the motivation is derived from
PowerContext's own contracts and from provider-neutral reranking practice.

# Unresolved questions

- **Trace exposure.** Should HTTP clients be able to request or receive a redacted decision-rerank trace, as raised for
  the listwise policy as well?
- **Deployment guidance.** Should the RFC require documented guidance that a deployment needing search availability
  without model dependency must leave reranking disabled?
- **Out of scope / follow-ups.** A genuinely listwise decision policy (if the decision port grows a rank-set schema) is
  out of scope and, if pursued, should be its own RFC. The plan-state applicability observation discussed elsewhere is
  unrelated to this RFC and must not ride on it.

# Future possibilities

- An optional environment-variable selector for the reranking implementation, as pure convenience. It is explicitly not
  required for this RFC (the injection path already enables decision reranking), and if added it must not reintroduce
  two parallel toggles beside `memory_rerank_enabled`.
- A dedicated decision-rerank usage contract, and batch decision calls that issue one request for several candidates.
- Query-class-aware candidate limits and latency budgets for decision reranking.
- A shared "role failure policy" mechanism if more fail-closed decision roles appear, generalising the
  `DecisionFailurePolicy` introduced here.
- Separately scoring the coarse pool and the final selection in end-to-end evaluation, on the same trace the listwise
  policy already exposes.

Any such extension must preserve exact hit identity, explicit policy versioning, default-off compatibility, and a
separation between coarse-pool and final-selection evaluation.
