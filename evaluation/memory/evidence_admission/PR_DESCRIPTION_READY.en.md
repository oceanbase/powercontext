# Which issue or RFC does this PR close?

Related to [oceanbase/powercontext#2074](https://github.com/oceanbase/powercontext/issues/2074).

This PR delivers an evaluation and its qualified findings. The single frozen decision is
**`insufficient_evidence`**. It does not claim that all acceptance gates passed or authorize a production follow-up.
Maintainers can review the documented limitations when deciding whether this delivery is sufficient to close the issue.

# Rationale for this change

Reducing the number of Sources cannot make an extraction input fit when a single Source projection already exceeds
the budget. Mixed evidence can also spend the budget on oversized fields, tool text, scaffolding, or quoted history
while increasing attribution risk.

This evaluation compares complete projection, current window reduction, and bounded fragments under frozen conditions.
It measures input admission separately from generated fact retention, exact-literal preservation, provenance,
determinism, cancellation, and cost. Better input coverage does not offset critical output failures.

# What changes are included in this PR?

- Add `evaluation/memory/evidence_admission/` with a versioned CLI, committed synthetic fixtures and experiment
  manifests, evaluation-only admission policies, deterministic scoring, machine-readable reports, model-free tests,
  and explicitly opt-in live execution.
- Exercise real Source capture, persistence, close/reopen, readback, and projection before constructing
  `AtomicMemoryExtractionInput`. Live runs invoke the actual `AtomicMemoryGenerationPipeline`.
- Freeze the fixture content, logical Source identities, generation instructions, model settings, and budgets across
  `complete-projection-v1`, `window-reduction-v1`, and `bounded-fragments-v3`. Structured tiering is not applicable
  because the Content adapter does not expose trustworthy structural provenance.
- Cover single-Source and many-Source overflow, mixed and oversized fields, positional facts, replay, exact literals,
  Unicode boundaries, impossible completeness, failures, Scope isolation, and cancellation. Record immutable identities,
  admitted ranges, omissions, stable reasons, input/output hashes, and distinct completeness states.
- Preserve historical protocols and failed experiments separately. Historical responses are not rewritten or
  backfilled to satisfy the integrated upstream schema's required `description` field.
- Add an append-only human-review supplement with strict call/output/context binding, partial judgments, and a
  confirmation worksheet for ambiguous sample descriptions. The tools refuse to overwrite original sessions or
  treat missing judgments as approval.
- Retain the evaluation infrastructure fixes for frozen JSON byte verification, LongMemEval's optional evidence-reader
  capability, numeric evaluation-counter validation, and Git-freeze test setup. Keep capture ownership and size
  admission as separate evaluations and decisions.

## Frozen comparison results

The formal matrix contains **972 rows**: 36 cases × 3 arms × 3 budgets × 3 repetitions.
The historical live configuration requested `deepseek-v4-flash` through the OpenAI-compatible DeepSeek endpoint,
with thinking disabled, temperature 0, a 2,048-token output limit, and no retries. Configuration and output hashes
are retained in the execution records.

Input budgets are 2,048, 4,096, and 8,192 under the native estimator, not hard limits on provider-billed tokens.
The candidate's frozen limits allow up to 16 requests per sample and a cumulative estimated-input multiplier of 8.
Equal per-input budgets therefore do not imply equal request counts or total cost; these are reported separately.
Live findings are bound to execution revision `1515a0d11cc2040d0beb341f6626a33c09cce73b`; the full
historical branch is archived as `evaluation/2074-bounded-evidence-admission-37commits`.

All **471 planned requests were attempted**; none remain unrun. There are 469 auditable responses and two historical
calls with unavailable responses and usage.

| Arm | Attempted requests | Valid Pipeline outputs | Known output failures | Historically unavailable |
| --- | ---: | ---: | ---: | ---: |
| `complete-projection-v1` | 42 | 41 | 0 | 1 |
| `window-reduction-v1` | 102 | 102 | 0 | 0 |
| `bounded-fragments-v3` | 327 | 148 | 178 | 1 |
| **Total** | **471** | **291** | **178** | **2** |

The candidate's offline admission covers 495/495 required parent claims and 531/531 required atoms in the eligible
measured rows. These counts include budgets and repetitions; they are not generated-fact retention rates.

The live results retain the following failures:

- 177 candidate outputs contain evidence IDs absent from their requests, affecting 292 candidates. The actual
  Pipeline rejected these outputs.
- One candidate request reaches the common output limit without producing a valid structured output.
- Six accepted Unicode outputs each preserve only 1/3 required exact literals.
- 533 candidate judgments remain semantically unresolved: 226 in accepted outputs and 307 in rejected outputs.

The requesting user reported 46 sampled outputs across 11 scenarios. Of those descriptions, 29 can be uniquely
located and 17 still need an arm or request index. Two false-attribution judgments and 13 non-atomic judgments
are bound to their original contexts. They are partial judgments, not 15 completed semantic reviews, and do not
clear the original unresolved counts or alter the original scores.

Both unavailable calls still have saved request records, but their original response bodies, parsed outputs, and
usage have not been recovered. Unknown usage remains unknown. Replacement requests would not restore those records.

The recorded cumulative known-cost upper bound is CNY 3.521362, with CNY 4.227072 reserved for unknown usage:
CNY 7.748434 against the authorized cumulative CNY 20 cap. These are bounds and reservations, not an account invoice.
Preparing the review and PR materials adds no provider requests.

## Decision and limitations

The measured candidate fails the production follow-up gate. Under the frozen reporting rules, unresolved critical
semantics and the two unavailable response/usage records also prevent a decisive `no_production_change` outcome.
The decision therefore remains **`insufficient_evidence`**; thresholds are not relaxed after inspecting outputs.
The results do not establish that every bounded admission policy is unsuitable.

The findings are limited to the measured synthetic workloads, provider configuration, and execution revision.
The model alias does not guarantee a fixed physical model snapshot. Local SQLite and controlled cancellation tests
do not establish acceptance for every remote backend, host, or provider cancellation/refund behavior.
No new live comparison has been performed under the integrated upstream schema, and historical measurements are
not presented as current-runtime validation. Private-corpus execution remains opt-in.

Evidence and review entry points:

- `evaluation/memory/evidence_admission/results/v20-live/`: frozen configuration, results, diagnostics, replay,
  validation, and artifact hashes.
- `evaluation/memory/evidence_admission/HUMAN_REVIEW.zh.md` and `results/v20-human-sample/`: declared sample coverage,
  bound partial judgments, byte checks, and frozen-context validation.
- `evaluation/memory/evidence_admission/ACCEPTANCE_CLOSEOUT.zh.md` and `REVIEW_CONFIRMATION.zh.md`: acceptance mapping,
  remaining limitations, and the 17 unconfirmed sample descriptions.
- `evaluation/memory/evidence_admission/results/acceptance-closeout-20261107/validation.json`: engineering
  validation results, including failed and interrupted attempts.
- `evaluation/memory/evidence_admission/results/acceptance-closeout-20261107/final-hooks.json`,
  `artifact-index.json`, and `COMPLETE.json`: sealed closeout evidence for the supplemental documents, with
  per-hook results and SHA-256 indexes. The closeout status is `closed_with_failures`.

# Are there any user-facing changes?

This PR adds evaluation commands, tests, and reports. Production sources are unchanged relative to the integrated
upstream revision `ed8902b234c4111b6f54e66694806430e25be3a9`.

There is no new production admission policy, truncation, fragment registration, feature flag, migration, or public API.
Authorization, exact Source windows, CAS, cancellation, search, ranking, citation, lifecycle, and Artifact write
semantics remain unchanged.

Published aggregate artifacts exclude raw private Source text, rendered provider prompts, response bodies,
credentials, and sensitive paths. Private audit material stays outside the repository. Reproducing historical
execution requires the corresponding frozen commits and locked environments; those commits are retained in the
archived branch `evaluation/2074-bounded-evidence-admission-37commits` and in the local backup bundle.

# How was this change tested?

| Validation | Status and scope |
| --- | --- |
| `make check` | Passed: lock-file checks, repository hooks, root and integration type checks, and 61 integration-manifest tests |
| `make build` | Passed: runtime wheel and source distribution |
| Recorded evidence-admission offline regression | 538 passed, 41 skipped; the historical schema-compatibility skips are retained and are not counted as passes |
| Added review-binding regressions | 14 tests passed on Python 3.11, 3.12, 3.13, and 3.14; this is targeted coverage, not a full four-version matrix |
| Recorded Ruff, evaluation types, and evaluation packaging | Passed in the human-review supplement validation |
| Full four-version evaluation matrix | Passed on each version: 1,466 passed, 42 skipped, followed by successful evaluation type checks |
| Full four-version root main suite | Completed with two failures on Python 3.12 and one on Python 3.14; no complete-matrix pass is claimed |
| Root type checks | Passed on all four versions; Python 3.12 and 3.14 were checked separately after their failed main runs |
| Serial ZCode service-chain partition | Passed on Python 3.11 (4 passed); Python 3.12/3.13/3.14 not run (no matching local interpreter) |

The Python 3.11 and 3.13 root main suites each passed with 9,584 passed and 847 skipped,
followed by successful type checks.
The Python 3.12 root main suite reported 9,584 passed, 845 skipped, and two failures:

- `tests/test_handoff_report.py::test_handoff_report_statement_count_does_not_grow_with_scope_count`:
  the observed SQL statement count exceeds the asserted bound.
- `tests/test_operational_metrics.py::test_healthy_combined_api_exposes_idle_stalled_and_stopped_processing[timeout-True]`:
  an unexpected scope-discovery failure remains in the diagnostic snapshot.

These failures remain unresolved. The corresponding runtime and test sources match the integrated upstream revision,
but their cause has not been established; they are not labeled harmless or fixed by changing assertions or deadlines.
Python 3.12's type-check step was not reached in that failed main invocation; its separate type check passed.

The Python 3.14 root main suite reported 9,583 passed, 847 skipped, and one failure in
`tests/test_upgrade_release_gate.py::test_upgrade_command_owner_finishes_cleanup_after_gate_cancellation[parent-term]`.
The test's process-identity observer raised `ProcessLookupError` during a `/proc` read, preventing completion of its
cancellation assertions. This failure also remains recorded; the test and its production dependencies are unchanged.
Python 3.14's separately executed type check passed.

The full matrix uses a parallel root main suite and an independent serial run of the ZCode service-chain tests,
matching the Makefile's isolation requirement. Evaluation environments retain their complete configured model-free
test suites and type checks. Both root partitions must pass before a version is considered fully validated.
The serial ZCode service-chain partition ran on Python 3.11 and passed (4 passed); Python 3.12, 3.13, and 3.14 were
not run because no matching local Python environment was available. This is recorded as `completed_py311_only`,
not as a four-version pass. Interrupted attempts are recorded separately and never counted as successful runs.

The original live, review, integration, and human-supplement records remain unchanged. The closeout evidence is
separate and does not overwrite original outputs, states, scores, thresholds, or unknown-usage reservations.
Default tests require no provider credentials or live model access. Opt-in live commands, environment-variable
requirements, cost controls, cancellation, and report regeneration are documented in the evaluation package.

# AI usage statement

OpenAI Codex assisted with implementation, testing, integration, audit binding, analysis, and documentation.
The requesting user supplied the human inspection feedback and explicit partial judgments. The assistant did not
sign off unreviewed candidates or establish a second independent human reviewer. Unresolved semantics, unavailable
responses, skipped tests, and validation boundaries remain explicit. The branch was pushed to the requester's fork
at the requester's direction; no PR has been created.
