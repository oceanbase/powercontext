---
title: "Processing failure diagnostics"
description: "Artifact processing failure categories, diagnostic field definitions, and rules for keeping business content out of logs."
---

# Processing failure diagnostics

This page is the shared contract for classifying artifact-processing failures and for the safe
diagnostics attached to them. It extends the existing operational logging fields; it does not
introduce a second telemetry system.

## Shared fields

Every processing failure keeps the existing identity and outcome fields (`family`, `operation`,
`worker_id`, `request_generation`, `outcome`, `error_code`, `exception_type`) and may add:

| Field | Meaning | Values |
|---|---|---|
| `processing_stage` | The stage that owned the failure | `generation`, `embedding`, `output_parsing`, `business_validation`, `persistence`, `usage_accounting` (model usage reporting and recording) |
| `failure_reason` | A bounded reason, only when evidence supports it | `context_overflow`, `output_truncated`, `rate_limit`, `timeout`, `schema_invalid`, `provider_unavailable`, `configuration`, `invalid_output`, `storage_failure`, `usage_limit` |
| `input_item_count` | Number of input items in the failing call | Non-negative integer |
| `estimated_input_tokens` | Estimated input tokens; for a window token ceiling, the charged or reserved token count | Non-negative integer |
| `token_estimator_id` | Identity of the estimator that produced the estimate | Bounded string |
| `effective_budget` | Budget applied to the call | Non-negative integer |
| `provider_request_id` | Provider request identity, when reported | Bounded string |
| `finish_reason` | Provider finish reason, when reported | Bounded string |
| `failure_location` | Stable internal location or operation identifier | Bounded string |
| `cause_exception_type` | Original provider or library exception type before mapping | Bounded string |

`processing_stage` is distinct from the existing `stage` field. `stage` names the emitting boundary or
domain location: Worker and supervisor values (`worker_process`, `work_admission`, `supervisor`,
`lease_renewal`, `scope_discovery`, `pending_observation`, `term_revocation`, `worker_termination`),
domain block names such as `topic_memory`, and the child Worker's `worker` fallback when an exception
carries no stage. A child failure without a domain block may omit `stage` entirely. `processing_stage`
is the bounded domain stage. Counts and identifiers are bounded before they reach a log record, and
unknown or unsupported values are omitted rather than guessed.

For Topic Memory window budgets, request and attempt ceilings retain the usage-limit reason and failure location without token-specific fields. A rejected reservation does not persist its proposed charges. Later reads omit token-specific fields when the committed counters cannot establish the limiting dimension; they never reconstruct token overflow from the failure code alone.

## Privacy boundary

Diagnostics never carry Source or Artifact bodies, prompts, model responses, credentials,
authorization headers, SQL parameters, or arbitrary exception text. Provider identifiers and reasons
are length-limited printable text. A field being allowlisted is not a redaction engine: values must
still be safe at the call site.

## Coverage matrix

`Covered` means the stage has a stable classification and a test. `Partial` means some failures are
classified through the inferred operation or exception type but not every raise site is tagged.
`Missing` means the stage is not classified yet. `N/A` means the Family does not perform that stage
in its processing path.

| Family | Generation | Embedding | Output parsing | Business validation | Persistence | Usage accounting |
|---|---|---|---|---|---|---|
| Memory / Atomic Memory | Partial | Covered | Partial | Covered | Partial | Partial |
| Topic Memory | Partial | Covered | Partial | Covered | Partial | Partial |
| Experience | Partial | N/A | Partial | Partial | Partial | Partial |
| Profile | Partial | N/A | Partial | Partial | Partial | Partial |
| Skill | Partial | N/A | Partial | Partial | Partial | Partial |
| Handoff | Partial | N/A | Partial | Partial | Partial | Partial |
| Prompt | Partial | N/A | Partial | Partial | Partial | N/A |

Memory / Atomic Memory and Topic Memory are tracked at operation granularity while they are the active slices:

| Operation | Stage | Status | Notes |
|---|---|---|---|
| `atomic-memory.extract` | Generation | Partial | Provider timeout, unavailability, rate limit, and configuration are classified; the final provider finish reason is retained when the adapter exposes a supported value. |
| `atomic-memory.extract` | Output parsing | Partial | Schema rejections retain the final response metadata when available. Explicit provider length evidence identifies truncation. |
| `atomic-memory.extract` | Business validation | Covered | Candidate content and cited-evidence rules carry `business_validation` / `invalid_output`. |
| `atomic-memory.reconcile` | Generation | Partial | Same provider mapping as extraction. |
| `atomic-memory.reconcile` | Output parsing | Partial | Same schema mapping as extraction. |
| `atomic-memory.reconcile` | Business validation | Covered | Reconciliation output and content rules carry `business_validation` / `invalid_output`. |
| Embedding (extract, reconcile, query) | Embedding | Covered | Provider status, wrong vector count/order/type, and embedding profile/input failures are classified. |
| Publish / index | Persistence | Partial | `AtomicMemoryIndexError` codes are mapped; other storage failures keep unknown. |
| Usage accounting | Usage accounting | Partial | The shared reporter and recorder classify usage failures. Inference usage limits keep the generation or embedding stage. |

Topic Memory operation-level coverage:

| Operation | Stage | Status | Notes |
|---|---|---|---|
| `topic-memory` stages (`probe`, `global`, `planner`, `evolve`, `temporary`, `reconcile`) | Generation | Partial | Provider timeout, unavailability, rate limit, and configuration are classified; the final provider finish reason is retained when the adapter exposes a supported value. |
| `topic-memory` stages | Output parsing | Partial | Provider schema failures use the shared mapping; semantic rejections are business validation. |
| `topic-memory` stages | Business validation | Covered | Every `TopicMemoryGenerationError` code maps to bounded diagnostics: domain and model rejections carry a stage and, when evidence supports it, a reason; control-flow rejections carry only `failure_location`. Input-budget failures carry input counts, token estimates, estimator identity, and the effective budget; the window token ceiling carries the charged tokens and the ceiling, while attempt and request ceilings are reported through their block details. |
| `topic-memory.index` and query embedding | Embedding | Covered | A missing embedding adapter and a wrong vector count are classified. |
| Publish / persistence | Persistence | Partial | Projection content and vector-completeness rules are business validation; storage-invariant failures map to `persistence` / `storage_failure`; unsupported capabilities report `configuration`; publisher conflicts keep their domain outcomes, and other storage failures keep unknown. A Topic head conflict records `head_conflict` as the attempt's last error and does not spend a window attempt. |
| Usage accounting | Usage accounting | Partial | The shared reporter and recorder classify usage failures. The window provider budget keeps `generation` / `usage_limit`. |

Experience, Profile, and Skill operation-level coverage:

| Operation | Stage | Status | Notes |
|---|---|---|---|
| `experience.incubate` | Business validation | Partial | Bounded input and out-of-window evidence carry explicit diagnostics; structural limits do not establish context overflow. |
| `profile.generate` | Business validation | Partial | Invalid Markdown carries the input count and original validation exception type without exposing the content. |
| Experience / Skill typed generation | Output parsing | Partial | Invalid typed results use the shared schema mapping; unclassified provider details remain unknown. |
| Dream (`refine_experience`, `revise_profile`, `derive_skill`, `revise_skill`) | Generation / business validation | Partial | Caught attempt failures emit `dream.attempt.failed` using the existing worker/request identity. Evidence limits, unavailable evidence, and rejected request targets carry `business_validation` and their failure location. |
| `experience.create` / `experience.replace`, `profile.create` / `profile.replace`, `skill.create` / `skill.replace` | Business validation / persistence | Partial | Scoped registry writes distinguish malformed content, concurrency rejections, and database driver errors. |
| `experience.approve` / `profile.approve` / `skill.approve` | Business validation / persistence | Partial | Candidate approval reports final rejections after retry recovery. Stale targets and terminal Candidate decisions retain their domain outcomes. |
| `profile.commit_generated`, `experience.publish`, `skill.publish` | Persistence | Partial | Generated Profile writes and cross-Scope delivery report driver failures, including transaction exit failures. Profile cross-Scope publication is N/A. Host Skill deployment and unrelated storage entry points are outside this coverage. |
| Usage accounting | Usage accounting | Partial | Model usage paths that use the shared reporter and recorder are classified. |

Handoff and Prompt operation-level coverage:

| Operation | Stage | Status | Notes |
|---|---|---|---|
| `handoff.generate` | Generation / output parsing / business validation | Partial | Explicit preparation and source-triggered generation report known provider and schema failures. Objective, citation, rollover, and byte-limit violations are invalid output. Missing evidence carries the validation stage and location. |
| `handoff.finalize` / `handoff.commit` | Business validation | Partial | Evidence, scope, and provenance rejections retain their domain errors. A revision conflict keeps its existing outcome and carries no storage-failure reason. |
| `handoff.commit` | Persistence | Partial | Database driver failures in the Handoff commit path carry `persistence` / `storage_failure`. |
| `prompt.generate_demonstrations` | Generation / output parsing / business validation | Partial | Provider/schema errors use shared classification. Invalid demonstration results are business validation. Demonstration generation never saves a Prompt revision. |
| `prompt.create` / `prompt.replace` | Business validation / persistence | Partial | The scoped management operations report known Prompt rejections and database driver failures, including failures at transaction exit. Other Prompt write paths are outside this coverage. |
| Handoff / Prompt | Embedding | N/A | These processing paths do not generate embeddings. |
| Handoff | Usage accounting | Covered | Model usage reporting and recording use the shared diagnostics. |
| Prompt demonstrations | Usage accounting | N/A | Demonstrations currently do not submit records to the shared recorder. |

Covered boundaries emit up to one `artifact.operation.failed` event per failed operation. Each process
emits at most 16 events in a rolling second for the same Family, operation, error code, and failure
reason. The event carries the existing Scope and bound request or trace identity, when present. It uses the shared diagnostic
fields and omits traceback, raw error text, SQL, and content. Unclassified errors omit unknown fields.
Logging has no effect on the returned error, transaction outcome, or retry behavior.

The matrices describe classification coverage, not completion of the whole issue. Dream failure
records contain bounded diagnostic fields only, never exception text or evidence content. Its
persisted run outcome and retry policy remain authoritative; the failure record is an observation,
not a new durable failure history. Durable-block classification currently covers Topic Memory only;
Dream uses its existing Run outcomes and attempt logs, not a durable scheduler block.

A timeout from Dream's own execution deadline produces the terminal Run code `budget_exceeded`.
Its attempt log reports `generation` / `usage_limit` with `failure_location=budget_exceeded` and
`exception_type=TimeoutError`. The stage identifies the owner of the execution budget. A bare
`TimeoutError` raised before that deadline expires follows the terminal `generation_failed` path
and logs `timeout` without inferring a stage.
Provider `InferenceTimeoutError` keeps its operation-specific stage and `timeout` reason and remains
retryable within the existing Run budget.

## Provider response evidence

For structured generation, an error at the end of the Agent's output parsing uses metadata from
the final provider response. A supported length finish reason yields `generation / output_truncated`;
a response with no such evidence retains its original classification. Starting another provider
request clears the previous response evidence. This preserves the configured retry and continuation
policy and the precedence of business validation, usage limits, and provider errors. A stream that
ends without a terminal signal remains a failure with an unknown reason.

Only recognized finish values and bounded, identifier-shaped provider response IDs enter
diagnostics. Context overflow requires a typed provider error, a recognized context-limit code,
or a specific provider message about input context. An output-token setting rejection does not
establish input context overflow.

The built-in Worker and Supervisor carry the same safe evidence for one invocation. The Supervisor
limits repeated `artifact_processing.failed` logs to 16 per rolling second per controlled error
code and reason in its process. Dream attempt logs use the same limit per family, operation, code,
and reason. Limiting these logs does not suppress cancellation or change processing state, attempt
counts, or retry behavior.

## Model usage accounting

The shared model usage reporter and recorder emit `model_usage.accounting.failed` with
`processing_stage=usage_accounting`. The operation is `model_usage` and the unit is `statistics`.
These failures leave the model result, business outcome, and existing usage retry policy in place.

| Failure location | Meaning | Reason |
|---|---|---|
| `report` | Bound reporter raised an error | `timeout` for a typed timeout |
| `enqueue` / `queue_full` | Recorder could not accept a usage record | `timeout` for a typed error, otherwise omitted |
| `write` | Recorder stopped trying to write a batch | `timeout` for a typed timeout, `storage_failure` for a database driver error |
| `flush_timeout` | Caller stopped waiting for its recorder checkpoint | `timeout` |
| `drain_timeout` / `cleanup_timeout` | Shutdown reached its drain or native cleanup deadline | `timeout` |
| `consumer` | Recorder's consumer stopped with an unexpected error | `timeout` for a typed timeout |

The error code uses the `usage_accounting_` prefix and the location, with a `_timeout` suffix when the
reason is timeout and the location does not already end in it. Unknown reasons are omitted. Write
failure can include an indeterminate commit, and a flush timeout only describes the wait. The
`input_item_count` is the number of original usage records involved when known, including the queued
backlog discarded at drain timeout. Family and model capability are recorded when known and
unambiguous. Detached writes may span multiple requests and do not inherit a request or trace from
the first offer. Built-in Workers keep their bound Worker and request-generation identity. The
supervisor still receives the existing loss counters, while the child log carries the cause.

The limit is 16 records per rolling second per process for the same family, operation, error code,
and reason. Recorder counters still count all drops and failed records. No usage payloads, SQL, or
exception messages are logged. Separate recall-token measurements and family-specific model budget
accounting are outside this coverage. Prompt demonstration generation currently bypasses the shared
recorder.

## Rollout

1. Shared diagnostics contract, allowlisted fields, Worker propagation, and the Memory / Atomic
   Memory slice.
2. Topic Memory: tag its staged generation, index/embedding, and persistence failures.
3. Experience, Profile, and Skill: tag generation output validation and persistence entry points.
4. Handoff and Prompt: tag generation and publication failures.
5. Classify failures in shared model usage reporting and recording.

For new instrumentation, cover the observable behavior: successful classification, propagation
across a real spawned Worker, sensitive-value exclusion, and that a diagnostics failure does not
change the business outcome.
