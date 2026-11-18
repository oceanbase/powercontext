---
title: "RFC 2127: Inference Budget and Admission Contract"
description: A unified contract for budget semantics, effective-value resolution, admission decisions, and rejection behavior before a model request is sent, covering every artifact family and embedding.
---

- Proposal name: `inference_budget_contract`
- Start date: 2026-11-10
- Tracking issue: [oceanbase/powercontext#2127](https://github.com/oceanbase/powercontext/issues/2127)
- Related RFCs: [RFC 0016](/en/rfcs/0016_pydantic_ai_inference_integration), [RFC 2086](/en/rfcs/2086-core-inference-gates-and-optional-gateway),
  [RFC 1417](/en/rfcs/1417_topic_memory), [RFC 1510](/en/rfcs/1510-artifact-dreaming), [RFC 1809](/en/rfcs/1809-atomic-memory),
  [RFC 1718](/en/rfcs/1718_memory_capacity_contract)
- Related work: [#2128](https://github.com/oceanbase/powercontext/issues/2128), [#2126](https://github.com/oceanbase/powercontext/issues/2126),
  [#2129](https://github.com/oceanbase/powercontext/issues/2129), [#2130](https://github.com/oceanbase/powercontext/issues/2130),
  [#2074](https://github.com/oceanbase/powercontext/issues/2074), [#1989](https://github.com/oceanbase/powercontext/issues/1989)

# Inference budget and admission contract

Single-request capacity and cumulative work allowance answer different questions: whether the request fits and whether
execution is still authorized. Both must hold. Families own selection and publication; shared inference owns accounting,
admission, and safe rejection without duplicating Topic/Dream ledgers. Configuration and entry-point coverage are documented
in [Inference budgets and admission](/en/development/inference-budgets).

## Complete-request boundary

Owned SDK HTTP request hooks inspect serialized requests after provider templates, structured-output instructions, and
protocol conversion, before network transport. Disabling privacy protection does not disable admission. Model-parameter
snapshots are selection estimates, not final requests; calling `prepare_request()` twice is not an accounting strategy.

Count effective instructions, output schemas, tools, evidence, history, and retry feedback. Exclude unsent input schemas and
non-input controls. Count extension inputs conservatively and reject invisible remote history or opaque media. Prepared
parameters of in-process test models establish local handling only.

Check every outbound request. Controlled SDKs must not retry local admission errors as connection failures. SDK wrapping
must retain the original local error and safe facts.

## Capacity, output, and execution

For a shared window:

```text
estimated_complete_input <= min(input_allowance, context_window - output_reserve - safety_margin)
```

Separate limits check input and output independently and also their sum when a joint window is declared. Output reservation
covers the actual sent response cap. Explicit family/stage, operation, and deployment ceilings intersect. Without an explicit
reserve, derive it from the actual cap; ordinary defaults do not override Topic's stage policy. Recheck serialized output
fields against extension overrides.

Operation request counts and timeouts enter `InferenceLimits` through `generation_limits(operation)`. Counts refer to one
Agent execution; Dream Runs and Topic work budgets retain their own units. Already-reserved authority must not be rejected
as unallocated balance or charged again for every request.

`generation_model_context_window_tokens` defaults to 125000 as a configuration default, not a claim about arbitrary models.
Custom gateways should declare actual limits rather than deriving them from aliases. Paths without complete accounting do
not claim capacity protection. Separate decision/rerank capacity requires independent declaration.

## Family invariants

- Atomic Memory retains contiguous windows, comparison batches, and existing reduction. Selection respects business and
  request allowances; reject oversized single items or minimum comparison context.
- Profile retains its previous content and commits only an admitted prefix. One deadline covers local selection; a sent
  request or retry-history overflow must not start fresh generation.
- Experience preserves complete cases. Explicit Experience/Skill inputs are required. Reject oversized items rather than
  slicing evidence to fit.
- Handoff preserves essential state, next actions, and citations; Prompt demonstrations preserve requested counts and their
  complete dynamic schema.
- All seven Topic stages check complete requests while retaining planning, reduction, retry reservations, the authoritative
  work ledger, fencing, and atomic publication.
- Dream retains explicit evidence, Run calls, deadlines, claims/attempts, and review semantics without changing Source cursors.

Failures never publish incomplete success or advance unprocessed input. Reselection, retries, and restarts cannot reset
existing allowances. Recovery belongs to #2128; new oversized-content projections or fragmentation require #2074's gate.
Checks remain valid with #1989 encoding optimizations disabled.

## Embedding

Per-text tokens, per-batch tokens, and batch item count are independent. Both adapters validate all planned batches before
the first send and retain contiguous count-based batching. Reject oversized batches without splitting texts or changing
vector order, count, or profile. Generation windows do not protect embeddings.

Typed inference failures retain known usage from completed batches; missing counts remain unknown. Reservations are not
provider usage.

## Errors and observation

`InferenceBudgetExceededError` maps to HTTP 422 / `inference_budget_exceeded`. MCP and Python Client retain the same operation,
stage, dimension, estimated_value, limit_value, unit, output_reserve_tokens, safety_margin_tokens, estimator_id,
estimator_version, and phase. Internal dispatch facts distinguish already-sent requests; missing usage never proves zero sends.

The HTTP/MCP `inference.budget.rejected` event uses `log_safely`, `unit=inference`, and `budget_unit` for measurement units.
Allowlisted fields survive JSON, console, and Server-owned Worker logging. No content, credentials, or raw provider errors
are recorded. Logging failures do not change results, cancellation propagates, and execution identities are not metric
labels. Logs are not ledgers.

## Validation

Real SDK assembly with local transports covers templates, schemas, Unicode, output caps, retry growth, concurrency, and zero
outbound requests. Business tests cover legal reduction, single-item and fixed-context rejection, unchanged cursors and
Candidates, cumulative allowances, failure usage, and formatted logs. Real spawned Workers verify process configuration and
logging. SQLite does not establish OceanBase acceptance; a test provider does not establish production token accounting.
