- Proposal Name: `memory_search_reranking`
- Start Date: 2026-08-06
- Related RFCs: [RFC 0014](0014_memory_layer_design.md),
  [RFC 0016](0016_pydantic_ai_inference_integration.md), and [RFC 0028](0028_context_pack.md)

# Summary

This RFC adds an optional listwise reranking stage to PowerContext Memory search. FTS and vector channels still
retrieve candidates and Reciprocal Rank Fusion (RRF) still produces the coarse order. When reranking is enabled, a
structured generation request selects a sparse, ordered subset from that bounded pool before `MemoryService.search()`
returns final hits.

The first policy is `powercontext.memory.rerank.listwise.v1`: retrieve up to 30 coarse candidates by default and let
the configured generation model select no more than the caller's requested `limit`. Reranking is disabled by default,
does not change stored Memory or indexes, and preserves every selected hit's exact Artifact, entry, and Revision
identity.

# Motivation

Hybrid retrieval is designed for recall. A broad Top-30 pool can contain the required evidence while placing it below
several entries that share vocabulary but do not answer the query. Sending all candidates to a downstream model adds
noise and token cost; truncating the coarse order to Top-10 loses relevant evidence.

LoCoMo evaluation established the useful intermediate policy:

```text
Hybrid/RRF Top-30
  -> one structured listwise generation request
  -> sparse answer-oriented selection, at most Top-10
```

This policy previously could be evaluated as benchmark-side post-processing, but that did not make reranking available
to Server, Runtime, or SDK users. Product behavior belongs after fusion inside the Memory search service. Benchmarks
should configure and observe that behavior through normal PowerContext interfaces.

# Guide-level explanation

## Enable reranking

Reranking is a deployment policy. Configure a generation model and enable it on the Runtime:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED=true
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT=30
powercontext server run
```

The existing search request remains unchanged:

```python
page = await runtime.memory.for_scope("project").search(
    SearchMemoryRequest(query="Which deployment decision is current?", limit=10, mode="hybrid")
)
```

With reranking disabled, `limit=10` means fuse and return the first ten coarse hits. With reranking enabled and a
candidate limit of 30, PowerContext fuses up to 30 coarse hits, asks the reranker to select at most ten, and returns
only that selection.

The reranker may return fewer than ten hits when only a sparse subset is useful. If its structured output contains no
valid rank, PowerContext safely falls back to the first ten coarse ranks. It removes duplicate, out-of-range, and
invented ranks before applying the selection.

## Understand the cost

Each non-empty reranked search adds one structured generation operation. The operation uses the configured generation
timeout and request bound and fixes temperature to zero. This improves repeatability but does not make every provider
deterministic.

Reranking is therefore appropriate when answer quality matters more than the added model latency and token cost. Keep
it disabled for low-latency lexical lookup or when no generation model is available.

## Observe a search

The in-process Runtime result includes a `rerank` trace containing:

- the versioned policy ID;
- exact coarse candidate hits;
- selected original ranks;
- discarded-rank and fallback diagnostics;
- rerank latency and portable request/token usage.

The HTTP v1 response continues to expose the final hits without this diagnostic trace. This keeps the existing OpenAPI
contract compatible. A benchmark can use the in-process trace to score the coarse pool and final selection separately.

# Reference-level explanation

## Configuration contract

`RuntimeConfig` defines two fields:

| Field | Default | Contract |
| --- | ---: | --- |
| `memory_rerank_enabled` | `false` | Assemble and apply the listwise Memory reranker. |
| `memory_rerank_candidate_limit` | `30` | Coarse fused pool, from 1 through 100. |

The first implementation reuses `InferenceConfig.generation_model`, `generation_timeout_seconds`, and
`generation_max_requests`. Startup fails with a configuration error when reranking is enabled without a generation
model or an explicitly injected `MemoryReranker`.

An injected reranker is an application composition choice and is applied even when the environment flag is false. This
supports tests and deployments with a provider-specific adapter while keeping environment-driven composition explicit.

## Search algorithm

For query `q`, requested final limit `k`, and configured candidate limit `c`:

1. Normalize `q` and select FTS, vector, or hybrid mode by the existing capability rules.
2. Set `coarse_limit = max(k, c)` when a reranker is configured; otherwise use `k`.
3. Ask each selected backend channel for `max(4 * coarse_limit, 32)` candidates.
4. Apply existing FTS/vector admission rules and RRF, bounded by `coarse_limit`.
5. If no reranker is configured or no hits remain, return the first `k` hits.
6. Send the normalized query, candidate rank, and candidate Memory text to the reranker.
7. Normalize selected ranks, preserve their model-provided order, and resolve them to the original `MemoryHit` objects.
8. Return final hits plus the in-process rerank trace.

The model never supplies Artifact IDs, entry IDs, scores, or citations. It selects integer positions only. PowerContext
resolves those positions against the already admitted pool, so reranking cannot invent evidence identity or modify a
stored Revision.

## Listwise policy

`powercontext.memory.rerank.listwise.v1` asks for candidates that directly state the person, event, date, duration,
count, list, reason, or outcome requested by the query. It keeps complementary evidence for multi-fact questions and
does not prefer newer content unless the query asks for current state or evidence conflicts.

Candidate Memory is untrusted evidence, not instructions. The structured schema accepts only a tuple of integer ranks.
Normalization:

- preserves the first occurrence of each in-range rank;
- discards duplicates and ranks outside `1..candidate_count`;
- stops after `k` valid ranks;
- falls back to coarse ranks `1..k` only when no valid rank remains.

Sparse valid output is not padded. Padding would reintroduce candidates that the model intentionally rejected.

## Consistency and concurrency

Every candidate remains anchored to one exact immutable Memory Revision. Read-only search does not hold the scope
mutation lock while calling the model. This prevents slow rerank calls from serializing concurrent searches in the
same scope.

A concurrent Memory mutation may make a search result refer to the immediately preceding Revision. That result remains
an exact, valid citation; it cannot mix hit identity across Revisions. Mutation paths retain the scope lock and their
existing compare-and-swap behavior.

## Failure behavior

Provider timeouts, unavailable providers, and invalid structured generation output follow the existing inference error
contract. They are not silently converted to coarse results. Callers may retry the idempotent search operation. The
only built-in fallback is for a successfully validated response that contains no usable rank.

This fail-closed inference behavior keeps degraded quality visible. A deployment that requires search availability
without model dependency should leave reranking disabled. A configurable fail-open policy is outside this RFC.

## Persistence and API impact

Reranking writes no tables, projections, cursors, or Artifact Revisions. Enabling or disabling it requires no migration
and does not alter embedding profile identity.

The transport-neutral Runtime returns final reranked hits and an optional trace. The HTTP v1 mapping continues to
return only final hits, so `openapi/powercontext.yaml` and generated clients do not change. The request `limit` always
means the maximum number of final hits.

## Evaluation boundary

LoCoMo owns dataset loading, Source expansion for answer generation, answer prompts, deterministic metrics, and judge
policies. It may select `none` or `llm` as a run parameter, but it must not construct a rerank generator, implement
selection, or reorder hits itself. Candidate metrics come from the PowerContext rerank trace; final metrics come from
the returned hits.

Accuracy claims must name the judge policy. A topical-policy LLM-judge score is not interchangeable with the stricter
judge, Exact Match, Token F1, or BLEU-1.

# Drawbacks

- One generation request per non-empty search materially increases latency and token cost.
- Listwise prompts can consume large contexts when Memory entries are long.
- Reusing the generation model is convenient but may be slower than a dedicated cross-encoder rerank service.
- The in-process diagnostic trace retains candidate text and should be handled as sensitively as normal search output.

# Rationale and alternatives

Simply returning a smaller coarse Top-K was rejected because it trades away recall before relevance can be compared.
A lexical reranker was rejected because evaluation did not improve evidence selection. Performing rerank in the
benchmark was rejected because it produces no PowerContext capability and can drift from production behavior.

A dedicated provider-neutral `MemoryReranker` port keeps Memory selection semantics in the Artifact Family while the
Pydantic AI structured generator remains an inference adapter. Selecting ranks instead of regenerated hits preserves
identity and makes output validation small and deterministic.

# Prior art

PowerContext already separates backend channel retrieval, candidate admission, and RRF fusion in RFC 0014, and uses
schema-bound generation adapters from RFC 0016. The LoCoMo retrieval experiment supplied the empirical candidate and
final limits for the first policy. This RFC composes those existing mechanisms rather than introducing another index
or persistence authority.

# Unresolved questions

- Should HTTP clients be able to request or receive a redacted rerank trace?
- Should a future policy allow fail-open fallback on provider outages?
- What provider-neutral usage contract is required for dedicated rerank endpoints that do not report chat tokens?

# Future possibilities

A later implementation may add a dedicated rerank-model adapter, batch searches, query-class-aware candidate limits,
or latency budgets. Those extensions must preserve exact hit identity, explicit policy versioning, default-off
compatibility, and separate coarse versus final evaluation.
