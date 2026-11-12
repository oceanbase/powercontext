# Evidence admission evaluation

Decision: `insufficient_evidence`

Model-free preparation: True. Live coverage: False.
Deterministic preparation: True. Cancellation valid: True.

| Arm | Samples | Prepared | Budget rejected | Cancelled | Unavailable | Eligible claims retained | Exact literals retained |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| complete-projection-v1 | 72 | 0 | 72 | 0 | 0 | 0/108 | 0/36 |
| window-reduction-v1 | 72 | 0 | 72 | 0 | 0 | 0/108 | 0/36 |
| bounded-fragments-v1 | 72 | 72 | 0 | 0 | 0 | 42/108 | 9/36 |
| bounded-fragments-v2 | 72 | 72 | 0 | 0 | 0 | 54/108 | 9/36 |
| bounded-fragments-v3 | 72 | 72 | 0 | 0 | 0 | 108/108 | 36/36 |

Eligible facts use the jointly feasible reference input; literal counts include every preparation-scored sample.

## Preparation by fixture (first replay, main budget)

| Fixture | Arm | Budget | Status | Completeness | Required spans retained | Jointly fits |
| --- | --- | ---: | --- | --- | ---: | --- |
| probe-shifted-middle | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/3 | True |
| probe-shifted-middle | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/3 | True |
| probe-shifted-middle | bounded-fragments-v1 | 4096 | prepared | bounded | 2/3 | True |
| probe-shifted-middle | bounded-fragments-v2 | 4096 | prepared | bounded | 3/3 | True |
| probe-shifted-middle | bounded-fragments-v3 | 4096 | prepared | bounded | 3/3 | True |
| probe-wide-metadata | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| probe-wide-metadata | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| probe-wide-metadata | bounded-fragments-v1 | 4096 | prepared | bounded | 1/2 | True |
| probe-wide-metadata | bounded-fragments-v2 | 4096 | prepared | bounded | 1/2 | True |
| probe-wide-metadata | bounded-fragments-v3 | 4096 | prepared | bounded | 2/2 | True |
| probe-reordered-metadata | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| probe-reordered-metadata | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| probe-reordered-metadata | bounded-fragments-v1 | 4096 | prepared | bounded | 1/2 | True |
| probe-reordered-metadata | bounded-fragments-v2 | 4096 | prepared | bounded | 1/2 | True |
| probe-reordered-metadata | bounded-fragments-v3 | 4096 | prepared | bounded | 2/2 | True |
| probe-user-metadata-fact | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-user-metadata-fact | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-user-metadata-fact | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| probe-user-metadata-fact | bounded-fragments-v2 | 4096 | prepared | bounded | 1/1 | True |
| probe-user-metadata-fact | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |
| probe-near-repeat-number | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-near-repeat-number | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-near-repeat-number | bounded-fragments-v1 | 4096 | prepared | bounded | 0/1 | True |
| probe-near-repeat-number | bounded-fragments-v2 | 4096 | prepared | bounded | 0/1 | True |
| probe-near-repeat-number | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |
| probe-near-repeat-negation | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-near-repeat-negation | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-near-repeat-negation | bounded-fragments-v1 | 4096 | prepared | bounded | 0/1 | True |
| probe-near-repeat-negation | bounded-fragments-v2 | 4096 | prepared | bounded | 0/1 | True |
| probe-near-repeat-negation | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |
| probe-unicode-crlf | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-unicode-crlf | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-unicode-crlf | bounded-fragments-v1 | 4096 | prepared | bounded | 0/1 | True |
| probe-unicode-crlf | bounded-fragments-v2 | 4096 | prepared | bounded | 0/1 | True |
| probe-unicode-crlf | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |
| probe-cross-fragment-record | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-cross-fragment-record | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| probe-cross-fragment-record | bounded-fragments-v1 | 4096 | prepared | bounded | 0/1 | True |
| probe-cross-fragment-record | bounded-fragments-v2 | 4096 | prepared | bounded | 0/1 | True |
| probe-cross-fragment-record | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |

Candidate preparation samples with required-content loss: 0.
Critical quality decisions are per sample; aggregate retention is descriptive only.

## Generation and usage

Live generation: **not run**. Provider-dependent quality and usage metrics are unmeasured.

| Arm | Scored samples | Required facts generated | Candidates | Exact / near duplicates | Unsupported / false attribution / invalid support / unresolved |
| --- | ---: | ---: | ---: | ---: | ---: |
| complete-projection-v1 | 0 | unmeasured | None | None / None | None / None / None / None |
| window-reduction-v1 | 0 | unmeasured | None | None / None | None / None / None / None |
| bounded-fragments-v1 | 0 | unmeasured | None | None / None | None / None / None / None |
| bounded-fragments-v2 | 0 | unmeasured | None | None / None | None / None / None / None |
| bounded-fragments-v3 | 0 | unmeasured | None | None / None | None / None / None / None |

Generation recall uses all required claims in each scored sample, including omitted or impossible claims.
Partial window generations are included; their fixture may still be budget-rejected. Missing generations are not scored as zero recall.

| Arm | Extractor attempts | Reported requests | Input tokens | Output tokens | Usage complete |
| --- | ---: | ---: | ---: | ---: | --- |
| complete-projection-v1 | 0 | 0 | None | None | None |
| window-reduction-v1 | 0 | 0 | None | None | None |
| bounded-fragments-v1 | 0 | 0 | None | None | None |
| bounded-fragments-v2 | 0 | 0 | None | None | None |
| bounded-fragments-v3 | 0 | 0 | None | None | None |

Token and request totals contain reported successful-call usage only; incomplete usage is not a zero-cost claim.
Currency costs require the selected provider's prices and are not estimated here.

## Limitations


## bounded-fragments-v3 paired preparation comparison

No observed fact/literal/status regression: True.
Fully fixed baseline failure combinations: 18/18.

| Fixture | Budget | Baseline facts | Candidate facts | Repetitions | Fully fixed |
| --- | ---: | ---: | ---: | ---: | --- |
| probe-cross-fragment-record | 2048 | 0/1 | 1/1 | 3 | True |
| probe-cross-fragment-record | 4096 | 0/1 | 1/1 | 3 | True |
| probe-cross-fragment-record | 8192 | 0/1 | 1/1 | 3 | True |
| probe-near-repeat-negation | 2048 | 0/1 | 1/1 | 3 | True |
| probe-near-repeat-negation | 4096 | 0/1 | 1/1 | 3 | True |
| probe-near-repeat-negation | 8192 | 0/1 | 1/1 | 3 | True |
| probe-near-repeat-number | 2048 | 0/1 | 1/1 | 3 | True |
| probe-near-repeat-number | 4096 | 0/1 | 1/1 | 3 | True |
| probe-near-repeat-number | 8192 | 0/1 | 1/1 | 3 | True |
| probe-reordered-metadata | 2048 | 1/2 | 2/2 | 3 | True |
| probe-reordered-metadata | 4096 | 1/2 | 2/2 | 3 | True |
| probe-shifted-middle | 2048 | 2/3 | 3/3 | 3 | True |
| probe-shifted-middle | 8192 | 2/3 | 3/3 | 3 | True |
| probe-unicode-crlf | 2048 | 0/1 | 1/1 | 3 | True |
| probe-unicode-crlf | 4096 | 0/1 | 1/1 | 3 | True |
| probe-unicode-crlf | 8192 | 0/1 | 1/1 | 3 | True |
| probe-wide-metadata | 2048 | 1/2 | 2/2 | 3 | True |
| probe-wide-metadata | 4096 | 1/2 | 2/2 | 3 | True |

| Arm | Prepared calls | Total estimated input tokens |
| --- | ---: | ---: |
| complete-projection-v1 | 0 | 0 |
| window-reduction-v1 | 0 | 0 |
| bounded-fragments-v1 | 72 | 336984 |
| bounded-fragments-v2 | 72 | 339048 |
| bounded-fragments-v3 | 72 | 341958 |

Prepared calls are potential live calls, not provider requests. Each request and the versioned cumulative caps are checked.
Generation quality is unmeasured. Window arms may use more than one call per sample.

## Limitation details

- `synthetic_sources_are_not_observed_host_payloads`
- `sqlite_only_no_oceanbase_claim`
- `fragment_references_are_evaluation_only`
- `local_task_cancellation_does_not_prove_remote_provider_cancellation`
- `no_production_memory_publication_or_worker_recovery_claim`
- `v3_requires_combined_dataset_review`
- `live_generation_not_run`
- `freeze_not_committed`
