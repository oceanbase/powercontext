# Evidence admission evaluation

Decision: `insufficient_evidence`

Model-free preparation: True. Live coverage: False.
Deterministic preparation: True. Cancellation valid: True.

| Arm | Samples | Prepared | Budget rejected | Cancelled | Unavailable | Eligible claims retained | Exact literals retained |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| complete-projection-v1 | 72 | 3 | 69 | 0 | 0 | 24/171 | 0/36 |
| window-reduction-v1 | 72 | 9 | 63 | 0 | 0 | 72/171 | 0/36 |
| bounded-fragments-v1 | 72 | 72 | 0 | 0 | 0 | 105/171 | 36/36 |
| bounded-fragments-v2 | 72 | 72 | 0 | 0 | 0 | 159/171 | 36/36 |
| bounded-fragments-v3 | 72 | 72 | 0 | 0 | 0 | 171/171 | 36/36 |

Eligible facts use the jointly feasible reference input; literal counts include every preparation-scored sample.

## Preparation by fixture (first replay, main budget)

| Fixture | Arm | Budget | Status | Completeness | Required spans retained | Jointly fits |
| --- | --- | ---: | --- | --- | ---: | --- |
| validation-reordered-sources | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/8 | True |
| validation-reordered-sources | window-reduction-v1 | 4096 | prepared | complete | 8/8 | True |
| validation-reordered-sources | bounded-fragments-v1 | 4096 | prepared | bounded | 2/8 | True |
| validation-reordered-sources | bounded-fragments-v2 | 4096 | prepared | complete | 8/8 | True |
| validation-reordered-sources | bounded-fragments-v3 | 4096 | prepared | complete | 8/8 | True |
| validation-crlf-scaffolding | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-crlf-scaffolding | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-crlf-scaffolding | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| validation-crlf-scaffolding | bounded-fragments-v2 | 4096 | prepared | bounded | 1/1 | True |
| validation-crlf-scaffolding | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |
| validation-history-offset | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-history-offset | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-history-offset | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| validation-history-offset | bounded-fragments-v2 | 4096 | prepared | bounded | 1/1 | True |
| validation-history-offset | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |
| validation-off-center-facts | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/3 | True |
| validation-off-center-facts | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/3 | True |
| validation-off-center-facts | bounded-fragments-v1 | 4096 | prepared | bounded | 2/3 | True |
| validation-off-center-facts | bounded-fragments-v2 | 4096 | prepared | bounded | 2/3 | True |
| validation-off-center-facts | bounded-fragments-v3 | 4096 | prepared | bounded | 3/3 | True |
| validation-metadata-pressure | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| validation-metadata-pressure | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| validation-metadata-pressure | bounded-fragments-v1 | 4096 | prepared | bounded | 1/2 | True |
| validation-metadata-pressure | bounded-fragments-v2 | 4096 | prepared | bounded | 2/2 | True |
| validation-metadata-pressure | bounded-fragments-v3 | 4096 | prepared | bounded | 2/2 | True |
| validation-durable-metadata | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-durable-metadata | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-durable-metadata | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| validation-durable-metadata | bounded-fragments-v2 | 4096 | prepared | bounded | 1/1 | True |
| validation-durable-metadata | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |
| validation-tool-array | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| validation-tool-array | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| validation-tool-array | bounded-fragments-v1 | 4096 | prepared | bounded | 2/2 | True |
| validation-tool-array | bounded-fragments-v2 | 4096 | prepared | bounded | 2/2 | True |
| validation-tool-array | bounded-fragments-v3 | 4096 | prepared | bounded | 2/2 | True |
| validation-literal-offset | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-literal-offset | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| validation-literal-offset | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| validation-literal-offset | bounded-fragments-v2 | 4096 | prepared | bounded | 1/1 | True |
| validation-literal-offset | bounded-fragments-v3 | 4096 | prepared | bounded | 1/1 | True |

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
Fully fixed baseline failure combinations: 4/4.

| Fixture | Budget | Baseline facts | Candidate facts | Repetitions | Fully fixed |
| --- | ---: | ---: | ---: | ---: | --- |
| validation-metadata-pressure | 2048 | 1/2 | 2/2 | 3 | True |
| validation-off-center-facts | 2048 | 2/3 | 3/3 | 3 | True |
| validation-off-center-facts | 4096 | 2/3 | 3/3 | 3 | True |
| validation-off-center-facts | 8192 | 2/3 | 3/3 | 3 | True |

| Arm | Prepared calls | Total estimated input tokens |
| --- | ---: | ---: |
| complete-projection-v1 | 3 | 20571 |
| window-reduction-v1 | 33 | 75861 |
| bounded-fragments-v1 | 72 | 334218 |
| bounded-fragments-v2 | 96 | 372183 |
| bounded-fragments-v3 | 96 | 374025 |

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
