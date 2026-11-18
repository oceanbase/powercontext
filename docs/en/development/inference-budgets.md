---
title: Inference budgets and admission
description: Complete-request admission, execution allowances, family selection rules, and coverage boundaries.
---

# Inference budgets and admission

Budgets constrain both single-request capacity and operation execution. Input, output reservation, and context checks
complement request counts, deadlines, and the existing Topic Memory and Dream work ledgers. Both must allow the operation.

## Complete requests

Owned OpenAI Chat, Responses, and Anthropic SDK clients use an HTTP request hook after serialization and before network
transport. Budget admission is independent of privacy mode: `protection.mode=disabled` does not disable it.

Accounting includes effective instructions, provider-rendered output instructions, tools and schemas, evidence, previous
responses, and retry feedback. Unsent local input schemas are excluded. Model identifiers, output caps, and sampling
controls are not input content. Extension fields are counted conservatively. Remote history references, images, audio,
and other opaque content are rejected when their complete input cannot be inspected.

Model-parameter snapshots are selection estimates. FunctionModel and TestModel have no HTTP protocol and use prepared
parameters for local tests; these do not establish SDK assembly coverage. Custom models must expose complete-request
accounting to participate in controlled generation.

For a shared input/output context window:

```text
allowed complete input = min(applicable input limit, context window - response reserve - safety margin)
```

`separate_limits` checks input and output independently and additionally checks their sum when a joint ceiling is declared.
Estimator identity and version are fixed. Estimates and provider usage remain distinct; a character estimate plus margin
cannot promise exact agreement with every model tokenizer.

## Configuration and output

`generation_model_context_window_tokens` defaults to 125000. This is a configuration default, not a claim about every
model or gateway alias. Operators should declare actual deployment capacity; aliases are not used to infer it.

`generation_request_budget` declares deployment input, output, margin, and estimator controls.
`generation_operation_budgets` narrows input, output, request count, and time for registered operations. Unknown operations,
invalid counts, and allocations exceeding explicit deployment ceilings are rejected. Existing `max_tokens` and an explicit
deployment output limit must satisfy configuration consistency checks.

The output limit actually sent respects family/stage policy, operation and deployment ceilings, and any explicit reserve.
When no reserve was explicitly configured, the request's actual output cap determines its reservation. The ordinary default
applies only without a stage policy or request setting; it does not silently cap Topic's derived output at 4096. The HTTP
hook checks serialized output fields again, so `extra_body` cannot enlarge the admitted cap.

Ordinary generators execute `generation_limits(operation)`. Separate decision/rerank endpoints use their own settings;
without declared independent capacity they do not borrow the generation window or claim capacity protection. Capacity must
be checked against the real endpoint in each deployment.

## Family and entry-point rules

| Family / entry point | Request capacity and selection | Progress and cumulative allowances |
| --- | --- | --- |
| Atomic extraction / reconciliation | Keep contiguous windows and comparison batches; selection checks both legacy business limits and request allowances; final SDK input is checked separately | Only reducible rejections before any model call use existing window reduction; reject oversized single or minimum comparison inputs; never advance past unprocessed input |
| Ordinary Profile generation | Preserve the previous Profile and select a contiguous Source prefix | One deadline spans local selections; an already-sent request or retry overflow never starts fresh generation; commit only the processed prefix |
| Automatic Experience incubation | Keep complete Task Outcomes; reject oversized individual cases rather than slicing; check complete requests | Do not publish incomplete cases or advance failed input |
| Explicit Experience / Skill generation | Caller-selected evidence is required; check both character bounds and the complete request | Reject rather than dropping evidence or creating a degraded Candidate |
| Handoff | Count objective, rollover reasons, required evidence, and output instructions | Do not omit essential handoff information to fit |
| Seven Topic stages | Preserve probe, planner, temporary, reduce, and related strategies; bind each operation and check retry history | Reuse one authoritative work budget, reservations, fence, cursor CAS, and atomic publication; retries neither replace ledgers nor reset allowances |
| Prompt demonstrations | Count the dynamically generated output schema | Enforce operation request/time allowances without silently reducing the requested count |
| Dream operations | Admit actual inputs against deployment capacity; intersect output with DreamBudget | Preserve Run calls, deadlines, attempts, and review semantics; do not consume ordinary Source cursors |

Topic's initial selection allowance and complete input including retry history have different accounting scopes. Existing
stage reservations cover structured retries. Final admission uses that authority without reserving an entire stage again
for each request. Exhausted work allowances cannot be reset by request admission.

Failure recovery belongs to #2128 and user-visible blocked status to #2130. Admission provides precise pre-send facts and
does not add a generic recovery state machine.

## Independent embedding limits

Both embedding adapters check per-text tokens, per-batch tokens, and per-batch item counts. Undeclared dimensions remain
unbounded; generation context settings do not protect embeddings. Existing contiguous `embedding_batch_size` batching is
retained. All planned batches are checked before the first send. Oversized batches are rejected; individual texts are not
split and vector count, order, and profile stay unchanged. This is not token-adaptive batching.

Typed inference failures retain known usage from completed earlier batches for the existing reporter. Missing usage stays
unknown.

## Rejection and observation

Local rejection raises `InferenceBudgetExceededError`. HTTP returns 422 / `inference_budget_exceeded`; MCP and Python Client
retain the same domain facts. Details include operation, stage, dimension, estimate, limit, units, reserve, margin,
estimator, and phase. They contain no evidence, prompts, credentials, or raw provider exceptions. SDK wrapping preserves
the local error rather than retrying it as a transient connection failure.

The HTTP/MCP `inference.budget.rejected` event uses registered fields in JSON, console, and Server-owned Worker logging.
`unit=inference`; measurement units use `budget_unit`. Background failures retain `inference_budget_exceeded`.
Operational logs are not a durable budget ledger.

## Effective values

`scripts/inference_budget_matrix.py` generates this table from defaults and family policies. Complete-request and selection
ceilings have different accounting scopes; the deployment window is not every family's evidence allowance. Actual model
settings, explicit allocations, and bound prompts can tighten execution further. Request counts and timeouts use the same
resolver as Runtime.

<!-- budget-matrix:begin -->
| operation | binding dimension | complete input ceiling (tokens) | selection ceiling (tokens) | output reserve (tokens) | safety margin (tokens) | max requests | timeout (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `atomic_memory.extract` | `context_tokens` | 114654 | 24000 | 4096 | 6250 | 2 | 30 |
| `atomic_memory.reconcile` | `context_tokens` | 114654 | 24000 | 4096 | 6250 | 2 | 30 |
| `experience.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `experience.incubate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `handoff.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `memory.rerank` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `memory.write-gate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `profile.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `prompt_demonstrations` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `skill.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `topic_memory.evolve` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.global` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.planner` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.probe` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.reconcile` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.reduce` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.temporary` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |

Embedding (independent limits; unset means unbounded):

- items per batch: not set
- input tokens per text: not set
- input tokens per batch: not set
- estimator: `character:weighted@1`

Dream (charged per Run against `DreamBudget`, admitted against the deployment window only):

- allowed input (tokens): 114654
- context limit (tokens): 114654
- output reserve (tokens): 4096
- safety margin (tokens): 6250
- max output (tokens): 4096
- max model calls per Run: 2
- timeout per Run (s): 120
<!-- budget-matrix:end -->

## Validation boundaries

Local SDK tests exercise real assembly and zero outbound requests; test providers do not establish real model tokenization
or response behavior. SQLite, actual spawned Workers, cumulative allowances, and formatted logs have separate behavior
tests. Real OceanBase and production model acceptance require independent execution and cannot be inferred from substitutes,
SQLite, or the configuration table.
