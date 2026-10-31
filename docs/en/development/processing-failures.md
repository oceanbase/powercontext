# Processing failure diagnostics

This page is the shared contract for classifying artifact-processing failures and for the safe
diagnostics attached to them. It extends the existing operational logging fields; it does not
introduce a second telemetry system.

## Shared fields

Every processing failure keeps the existing identity and outcome fields (`family`, `operation`,
`worker_id`, `request_generation`, `outcome`, `error_code`, `exception_type`) and may add:

| Field | Meaning | Values |
|---|---|---|
| `processing_stage` | The stage that owned the failure | `generation`, `embedding`, `output_parsing`, `business_validation`, `persistence`, `usage_accounting` (reserved for usage reporting or persistence) |
| `failure_reason` | A bounded reason, only when evidence supports it | `context_overflow`, `output_truncated`, `rate_limit`, `timeout`, `schema_invalid`, `provider_unavailable`, `configuration`, `invalid_output`, `storage_failure`, `usage_limit` |
| `input_item_count` | Number of input items in the failing call | Non-negative integer |
| `estimated_input_tokens` | Estimated input tokens | Non-negative integer |
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
| Memory / Atomic Memory | Partial | Covered | Partial | Covered | Partial | Missing |
| Topic Memory | Partial | Partial | Partial | Missing | Missing | Missing |
| Experience | Partial | N/A | Partial | Missing | Missing | Missing |
| Profile | Partial | N/A | Partial | Missing | Missing | Missing |
| Skill | Partial | N/A | Partial | Missing | Missing | Missing |
| Handoff | Partial | N/A | Partial | Missing | Missing | Missing |
| Prompt | Partial | N/A | Partial | Missing | Missing | Missing |

Memory / Atomic Memory is tracked at operation granularity while it is the active slice:

| Operation | Stage | Status | Notes |
|---|---|---|---|
| `atomic-memory.extract` | Generation | Partial | Provider timeout, unavailability, rate limit, and configuration are classified; a provider finish reason is only visible where the adapter exposes it. |
| `atomic-memory.extract` | Output parsing | Partial | Schema and Pydantic output failures are classified; some provider truncation paths surface as schema failures. |
| `atomic-memory.extract` | Business validation | Covered | Candidate content and cited-evidence rules carry `business_validation` / `invalid_output`. |
| `atomic-memory.reconcile` | Generation | Partial | Same provider mapping as extraction. |
| `atomic-memory.reconcile` | Output parsing | Partial | Same schema mapping as extraction. |
| `atomic-memory.reconcile` | Business validation | Covered | Reconciliation output and content rules carry `business_validation` / `invalid_output`. |
| Embedding (extract, reconcile, query) | Embedding | Covered | Provider status, wrong vector count/order/type, and embedding profile/input failures are classified. |
| Publish / index | Persistence | Partial | `AtomicMemoryIndexError` codes are mapped; other storage failures keep unknown. |
| Usage accounting | Usage accounting | Missing | Usage-reporting and persistence failures are not classified; inference usage limits are reported in their originating stage (generation, or embedding when an adapter reports the limit). |

The first rollout slice covers Memory / Atomic Memory: provider failures, budget exhaustion, and
extraction/reconciliation validation are classified, and safe accounting fields travel from the
child Worker to the supervisor log. Other Families keep their current behavior until their own slice
lands; the matrix is updated in the same change as each slice.

## Rollout

1. Shared diagnostics contract, allowlisted fields, Worker propagation, and the Memory / Atomic
   Memory slice (this change).
2. Topic Memory: tag its staged generation, index/embedding, and persistence failures.
3. Experience, Profile, and Skill: tag generation output validation and persistence entry points.
4. Handoff and Prompt: tag generation and publication failures, then add usage-accounting
   classification and complete the matrix.

For new instrumentation, cover the observable behavior: successful classification, propagation
across a real spawned Worker, sensitive-value exclusion, and that a diagnostics failure does not
change the business outcome.
