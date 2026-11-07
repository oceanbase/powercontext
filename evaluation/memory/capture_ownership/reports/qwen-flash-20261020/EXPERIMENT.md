# WorkBuddy capture ownership: synthetic replay

Historical decision: **projection_follow_up_required**. This calls for a separate production design issue; it does not
qualify the experimental projection for deployment. Production behavior is unchanged.

## Revision scope

This is a historical live experiment frozen at generation commit
[`2c21132ba5dd1caa986484b8d71e1504f0dca3e2`](https://github.com/oceanbase/powercontext/commit/2c21132ba5dd1caa986484b8d71e1504f0dca3e2).
The adjacent `report.json` retains that `code_sha`, the original run/lock hashes, and the original measurements.
All 38 `joined-source-v1` rows have `occupancy.text_carriers == 2`: the runtime then included the complete body in
both materialized content and Source metadata.

Runtime changes [#2139](https://github.com/oceanbase/powercontext/pull/2139) and
[#2163](https://github.com/oceanbase/powercontext/pull/2163), brought into this branch by merge
[`f71f6955`](https://github.com/oceanbase/powercontext/commit/f71f6955fd61a85e02dba4b295f40f92f20d281b), compact duplicate
bodies. The offline ContentSource replay at that merge has one joined-source text carrier, as asserted by the
updated regression test. These are different frozen runtime inputs; running the current checkout does not reproduce
this report's two-carrier occupancy.

No post-compaction live generation is included. Candidate counts, contamination annotations, fact retention, token
usage, latency, and the decision below all describe the original live experiment. The decision gate uses reviewed
candidate contamination and paired fact retention, not carrier occupancy, so compaction alone does not invalidate
the historical finding or establish the current runtime's extraction quality.

## Protocol and coverage

The frozen corpus has 19 synthetic cases, seven ownership categories, exact character/UTF-8 byte partitions, and
26 required-current-claim opportunities across two repeats. The model is `openai-chat:qwen-flash-2025-07-28`, Beijing
DashScope OpenAI-compatible endpoint, temperature 0, 1024 output tokens, thinking disabled, one request per extraction,
SDK retries disabled. The complete run used an eight-second minimum request interval and a 0.8 CNY reservation cap.

Both arms used identical persisted Source/materialization identity, evidence ID/order, built-in coding extraction
instructions, model/settings and generation limits. Only the evaluation evidence view differed. Capture used the real
WorkBuddy capture function, real ASGI HTTP routes, file-backed SQLite, idempotent replay and repository readback, then
the actual Atomic Memory extraction pipeline. The native ContentSource materialization branch was exercised; the
registered text projection was checked independently. This is not a native WorkBuddy installation test.

Generation inputs and model configuration were frozen before examining the outputs. The generation code commit is
`2c21132ba` (full commit and file digests are in the private lock and public report). The report separately identifies
the analysis implementation. Support annotation uses the original frozen spans and claims, including explicit support
spans for descriptive statements that are outside the required-fact targets. No corpus labels, prompts, or model
settings were changed between the exploratory and complete runs; request pacing was added for the complete run.

## Complete run

All 64 planned model calls completed. The candidate arm skipped another 12 rows with empty or unknown boundaries;
these are explicit eligibility outcomes, not successful empty model responses. All 76 arm/case/repetition rows were
annotated against the saved outputs and frozen ground truth.

| Metric | joined-source-v1 | host-turn-candidate-v1 |
|---|---:|---:|
| Model calls completed | 38 | 26 |
| Ineligible rows skipped | 0 | 12 |
| Candidates | 46 | 26 |
| Critical candidates | 22 / 46 (47.8%) | 2 / 26 (7.7%) |
| Required current facts retained | 20 / 26 (76.9%) | 24 / 26 (92.3%) |
| Exact-literal failures in annotated retained claims | 0 | 0 |
| Within-output semantic duplicates | 0 | 0 |
| Provider input tokens | 41,794 | 23,762 |
| Provider output tokens | 1,943 | 944 |
| Estimated cost from returned usage, CNY | 0.009184 | 0.004980 |
| Sum of generation latency, seconds | 31.19 | 18.20 |

The sample was constructed to probe failure modes. These fractions are corpus-specific annotations, not a production
incidence estimate. Latency excludes deliberate pacing. Missing facts are reported in retention, not hidden by the
zero literal-failure count. No Atomic Memory Artifacts were published and cross-event memory deduplication was not tested.

Six cases met the frozen two-repeat gate with clean paired preservation of required current facts:

- `history-before-current`
- `prepared-context-fact`
- `host-preference`
- `repeated-history`
- `forged-query-in-summary`
- `summary-and-notification`

Support labels were **Codex-assisted annotations, not independent human validation**. For the decisive six cases,
`deterministic-support-audit.json` provides 12 additional literal witnesses: each canary occurs in a frozen non-current
support span and generated candidate, is absent from current-user spans, disappears from the paired output, and the
paired output retains the required current literal. Witnesses include exact span offsets, ownership, candidate/support
digests and run/manifest hashes. This check makes no model calls and can be recomputed from the private run:

```bash
uv run --project evaluation --extra capture-ownership python \
  evaluation/memory/capture_ownership/scripts/audit_synthetic.py \
  --private-dir /path/outside-checkout/live-run --output /path/outside-checkout/recomputed-audit.json
```

Qualitative labels beyond those witnesses remain open to independent human review. Raw inputs, generated text and
annotator identities remain private; this directory contains only aggregate metrics and content-free support checks.

## Evidence carriers and remaining failures

In the frozen runtime above, the complete joined text appeared in two extraction carriers: materialized content and
Source metadata. Across the 38 rows per arm, historical labeled bytes fell from 1,804 to 0; PreparedContext bytes from
764 to 0; Skill bytes from
448 to 0; summary bytes from 1,000 to 0. These are repeated evidence-byte counts, not provider token counts. The
candidate view retains 56 scaffold bytes consisting of wrapper-adjacent whitespace, and quoted text inside valid
current turns remains present. For that frozen input, replacing only the content field would have left the complete
joined text in Source metadata.

The candidate arm still produced an unsupported attribution in a quoted-inline case and promoted a quoted bad example
to a preference in a fenced-quote case. It also omitted required current facts in two rows. A host boundary view alone
does not solve claim ownership or instruction/data separation. A follow-up must preserve raw auditability and address
unknown/quoted ownership, parser version/materialization binding, idempotency, authority separation, per-host payload
contracts and compatibility before public API or production changes.

## Exploratory failures and spending

An earlier unpaced run had 20 completed requests, 44 `InferenceUnavailableError` failures and 12 eligibility skips.
It was retained as `insufficient_evidence`, not silently combined with successful rows. It returned 20,619 input and
980 output tokens. The failure HTTP status was not preserved in that version, so a provider-rate-limit diagnosis is
not established. A separate minimal diagnostic returned 212 input and 4 output tokens. The complete run above was a
new frozen experiment with the same corpus/provider/prompt/settings and explicit pacing; it had no generation failures.

Known returned usage across both runs and the diagnostic totals 86,387 input and 3,871 output tokens, approximately
**0.0188 CNY** at the configured reference rates. Failed-call usage is unknown; the provider invoice is authoritative.
The two full-run conservative cost reservations total 0.4225 CNY, including reservations retained for failed calls.

## Reproduction and limits

See [the suite README](../../README.md) for installation, freeze/run/review/report commands, provider configuration
and offline acceptance tests. `report.json` binds the exact run, manifest, model configuration, prompt and generation
harness via hashes.

Replaying the saved lock requires its original generation commit and file hashes. For a post-compaction comparison,
freeze a new experiment on the intended revision, run generation again, and review the newly generated outputs.
Keep it separate from this snapshot; changing `code_sha` or carrier counts in the saved JSON would misrepresent the
original measurements, and the saved output-bound labels cannot be transferred to a new run.

This directory retains the curated experiment account, aggregate report, and deterministic support audit as
reference evidence for #2069. The production follow-up design is tracked in
[#2119](https://github.com/oceanbase/powercontext/issues/2119).

The inference evidence covers one model snapshot, one extraction profile, synthetic host payloads and SQLite. A
stronger/different model can change the frequency and type of candidate errors. The evidence establishes these
counterexamples for the pinned setup; it does not establish equivalent behavior on every model, host or backend.
