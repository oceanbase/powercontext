# Evidence admission evaluation

Decision: `insufficient_evidence`

Model-free preparation: True. Live coverage: False.
Deterministic preparation: True. Cancellation valid: True.

| Arm | Samples | Prepared | Budget rejected | Cancelled | Unavailable | Eligible claims retained | Exact literals retained |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| complete-projection-v1 | 180 | 39 | 96 | 27 | 18 | 51/216 | 0/927 |
| window-reduction-v1 | 180 | 45 | 90 | 27 | 18 | 99/216 | 0/927 |
| bounded-fragments-v1 | 180 | 135 | 0 | 27 | 18 | 156/216 | 81/927 |

Eligible facts use the jointly feasible reference input; literal counts include every preparation-scored sample.

## Preparation by fixture (first replay, main budget)

| Fixture | Arm | Budget | Status | Completeness | Required spans retained | Jointly fits |
| --- | --- | ---: | --- | --- | ---: | --- |
| small-complete | complete-projection-v1 | 4096 | prepared | complete | 1/1 | True |
| small-complete | window-reduction-v1 | 4096 | prepared | complete | 1/1 | True |
| small-complete | bounded-fragments-v1 | 4096 | prepared | complete | 1/1 | True |
| single-source-overflow | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| single-source-overflow | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| single-source-overflow | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| many-source-overflow | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/8 | True |
| many-source-overflow | window-reduction-v1 | 4096 | prepared | complete | 8/8 | True |
| many-source-overflow | bounded-fragments-v1 | 4096 | prepared | bounded | 2/8 | True |
| oversized-tool-field | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| oversized-tool-field | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| oversized-tool-field | bounded-fragments-v1 | 4096 | prepared | bounded | 2/2 | True |
| positional-facts | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/3 | True |
| positional-facts | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/3 | True |
| positional-facts | bounded-fragments-v1 | 4096 | prepared | bounded | 3/3 | True |
| repeated-history | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| repeated-history | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| repeated-history | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| mixed-scaffolding | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| mixed-scaffolding | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| mixed-scaffolding | bounded-fragments-v1 | 4096 | prepared | bounded | 0/1 | True |
| forged-role-marker | complete-projection-v1 | 4096 | prepared | complete | 1/1 | True |
| forged-role-marker | window-reduction-v1 | 4096 | prepared | complete | 1/1 | True |
| forged-role-marker | bounded-fragments-v1 | 4096 | prepared | complete | 1/1 | True |
| exact-literals | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| exact-literals | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| exact-literals | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| unicode-boundaries | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| unicode-boundaries | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| unicode-boundaries | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| observation-overflow | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| observation-overflow | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/2 | True |
| observation-overflow | bounded-fragments-v1 | 4096 | prepared | bounded | 2/2 | True |
| metadata-fact | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| metadata-fact | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/1 | True |
| metadata-fact | bounded-fragments-v1 | 4096 | prepared | bounded | 1/1 | True |
| negative-control | complete-projection-v1 | 4096 | prepared | complete | 0/0 | True |
| negative-control | window-reduction-v1 | 4096 | prepared | complete | 0/0 | True |
| negative-control | bounded-fragments-v1 | 4096 | prepared | complete | 0/0 | True |
| impossible-completeness | complete-projection-v1 | 4096 | budget_rejected | bounded | 0/96 | False |
| impossible-completeness | window-reduction-v1 | 4096 | budget_rejected | bounded | 0/96 | False |
| impossible-completeness | bounded-fragments-v1 | 4096 | prepared | bounded | 1/96 | False |
| missing-source | complete-projection-v1 | 4096 | unavailable | unavailable | unavailable | None |
| missing-source | window-reduction-v1 | 4096 | unavailable | unavailable | unavailable | None |
| missing-source | bounded-fragments-v1 | 4096 | unavailable | unavailable | unavailable | None |
| invalid-projection | complete-projection-v1 | 4096 | unavailable | unavailable | unavailable | None |
| invalid-projection | window-reduction-v1 | 4096 | unavailable | unavailable | unavailable | None |
| invalid-projection | bounded-fragments-v1 | 4096 | unavailable | unavailable | unavailable | None |
| cancel-before-read | complete-projection-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-before-read | window-reduction-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-before-read | bounded-fragments-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-projection | complete-projection-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-projection | window-reduction-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-projection | bounded-fragments-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-generation | complete-projection-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-generation | window-reduction-v1 | 4096 | cancelled | cancelled | unavailable | None |
| cancel-generation | bounded-fragments-v1 | 4096 | cancelled | cancelled | unavailable | None |
| identity-replay | complete-projection-v1 | 4096 | prepared | complete | 1/1 | True |
| identity-replay | window-reduction-v1 | 4096 | prepared | complete | 1/1 | True |
| identity-replay | bounded-fragments-v1 | 4096 | prepared | complete | 1/1 | True |

Candidate preparation samples with required-content loss: 24.
Critical quality decisions are per sample; aggregate retention is descriptive only.

## Generation and usage

Live generation: **not run**. Provider-dependent quality and usage metrics are unmeasured.

| Arm | Scored samples | Required facts generated | Candidates | Exact / near duplicates | Unsupported / false attribution / invalid support / unresolved |
| --- | ---: | ---: | ---: | ---: | ---: |
| complete-projection-v1 | 0 | unmeasured | None | None / None | None / None / None / None |
| window-reduction-v1 | 0 | unmeasured | None | None / None | None / None / None / None |
| bounded-fragments-v1 | 0 | unmeasured | None | None / None | None / None / None / None |

Generation recall uses all required claims in each scored sample, including omitted or impossible claims.
Partial window generations are included; their fixture may still be budget-rejected. Missing generations are not scored as zero recall.

| Arm | Extractor attempts | Reported requests | Input tokens | Output tokens | Usage complete |
| --- | ---: | ---: | ---: | ---: | --- |
| complete-projection-v1 | 0 | 0 | None | None | None |
| window-reduction-v1 | 0 | 0 | None | None | None |
| bounded-fragments-v1 | 0 | 0 | None | None | None |

Token and request totals contain reported successful-call usage only; incomplete usage is not a zero-cost claim.
Currency costs require the selected provider's prices and are not estimated here.

## Limitations

- `synthetic_sources_are_not_observed_host_payloads`
- `sqlite_only_no_oceanbase_claim`
- `fragment_references_are_evaluation_only`
- `local_task_cancellation_does_not_prove_remote_provider_cancellation`
- `no_production_memory_publication_or_worker_recovery_claim`
- `live_generation_not_run`
- `freeze_not_committed`
