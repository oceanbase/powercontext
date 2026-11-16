# Which issue or RFC does this PR close?

Related to [oceanbase/powercontext#2074](https://github.com/oceanbase/powercontext/issues/2074).

The frozen decision is **`insufficient_evidence`**. Bounded admission improves input coverage, but the measured
candidate fails generation-quality gates and critical evidence remains unresolved. This delivery does not claim
that all issue acceptance gates passed. Maintainers decide whether the qualified findings are sufficient to close it.

# Rationale for this change

Reducing the number of Sources cannot admit an extraction input when a single Source projection exceeds the budget.
This evaluation compares complete projection, current window reduction, and bounded fragments after capturing,
persisting, reopening, and re-reading real Sources and constructing `AtomicMemoryExtractionInput`.
Live execution uses the actual `AtomicMemoryGenerationPipeline`. All arms share frozen generation instructions,
output schema, model settings, and per-input budgets; input admission and generated quality are scored separately.

# What changes are included in this PR?

- A versioned `evaluation/memory/evidence_admission/` CLI, synthetic fixtures and manifests, deterministic preparation
  and replay, evaluation-only admission arms, scoring, opt-in live execution, and privacy-safe reporting.
- Coverage for single- and multi-Source overflow, mixed and oversized fields, positional facts, exact literals,
  CJK/UTF-8 boundaries, impossible completeness, replay, faults, cancellation, and Scope isolation. Structured tiering
  is not applicable because the Content adapter provides no trustworthy structural ownership tiers.
- Immutable Source/materialization/range support and separate states for completed, bounded, unavailable, and
  cancelled work. Capture ownership and size admission remain separate evaluations and decisions.
- Crash-safe v19/v20 scoring continuation: returned responses and usage are recorded as `scoring_pending` before
  scoring. Resume scores the saved response without resending or double charging. A required response missing a score
  is a `scoring_incomplete` blocker and cannot yield `no_production_change`.
- Current-runtime regressions alongside explicit historical instruction/schema fixtures. Historical responses,
  scores, thresholds, and unavailable usage are not rewritten to satisfy the rebased runtime.
- Runtime sources included in the committed-freeze check, frozen JSON byte verification, optional evidence-reader
  handling, and numeric counter compatibility in evaluation tools.
- Generated reports and historical run records distributed as a separate checksummed archive. Historical helpers
  require its extracted `results` directory through `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS`.
- A documented pre-execution cost calculation for generic `run-live`/`run-smoke`. These commands enforce a request
  count, not a currency limit; the historical v20 budget ledger is a separate execution path.

# Evidence and review entry points

Download [powercontext-2074-evidence-8bf9c58d.tar.gz](https://github.com/user-attachments/files/34846203/powercontext-2074-evidence-8bf9c58d.tar.gz).

- SHA-256: `ff529bf0875c2441835bc5f35af3fe60df0c62c8722370f410aec73e499c0d65`
- Results packaging snapshot: `8bf9c58d428995c97858b6764d4563c3cfe9ea44`
- Final live continuation execution revision: `1515a0d11cc2040d0beb341f6626a33c09cce73b`

The packaging snapshot is not the execution revision; per-run revisions and hashes remain inside the archive.
Verify its SHA-256, unpack it, and set `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` to the absolute extracted `results`
directory. [Archive instructions](RESULTS_ARTIFACT.md) include verification and setup commands.

| Path inside the extracted archive | Evidence |
| --- | --- |
| `results/v20-live/` | Frozen configuration, results, diagnostics, recorded replay, validation, and artifact hashes |
| `results/v20-human-sample/` | Declared sample coverage, partial judgments, byte checks, and context binding |
| `results/acceptance-closeout-20261107/validation.json` | Historical engineering validation, including failed and interrupted attempts |
| `results/acceptance-closeout-20261107/final-hooks.json` | Historical per-hook results |
| `results/acceptance-closeout-20261107/artifact-index.json` | Closeout file hashes |
| `results/acceptance-closeout-20261107/COMPLETE.json` | Completion marker; the recorded closeout status is `closed_with_failures` |

These are archive members, not in-repository file links. The committed [human-review explanation](HUMAN_REVIEW.zh.md),
[acceptance mapping](ACCEPTANCE_CLOSEOUT.zh.md), and [review confirmation](REVIEW_CONFIRMATION.zh.md) remain available.
The archive contains public sanitized evidence, not raw private Source text, rendered prompts, private response bodies,
credentials, or sensitive paths. Complete response replay additionally requires the original private audit material.

# Historical code availability

The rebuilt delivery branch `Lkx-JY/powercontext: evaluation/2074-bounded-evidence-admission` does not retain the original
execution revision in its ancestry. The current PR implementation and the historical execution code have distinct identities.

The original history is published separately as `Lkx-JY/powercontext: evaluation/2074-bounded-evidence-admission-37commits`:

- [Fixed archive tip](https://github.com/Lkx-JY/powercontext/tree/3cc97c26e6cc6a6d99c678bc4da10b54755a9960):
  `3cc97c26e6cc6a6d99c678bc4da10b54755a9960`.
- [Final execution revision](https://github.com/Lkx-JY/powercontext/tree/1515a0d11cc2040d0beb341f6626a33c09cce73b):
  `1515a0d11cc2040d0beb341f6626a33c09cce73b`, contained in the archive history.

The remote archive SHA and a fetch of the execution revision into a fresh repository were verified on November 16, 2026.
[Historical code and reproduction](HISTORY_REPRODUCTION.md) gives exact recovery commands. Publishing code does not
recover missing private response bodies; the result archive and original private audit inputs remain separate requirements.

# Recorded result and limitations

The formal comparison has 972 rows: 36 cases × 3 arms × 3 budgets × 3 repetitions. Historical generation requested
`deepseek-v4-flash` with thinking disabled, temperature zero, a 2,048-token output limit, and no retries.
Input budgets are 2,048/4,096/8,192 estimated tokens, not provider-billed token caps. The candidate can use up to
16 requests per sample and an estimated-input multiplier of eight; equal per-input budgets do not mean equal cost.

| Arm | Attempted requests | Valid Pipeline outputs | Known output failures | Historically unavailable |
| --- | ---: | ---: | ---: | ---: |
| `complete-projection-v1` | 42 | 41 | 0 | 1 |
| `window-reduction-v1` | 102 | 102 | 0 | 0 |
| `bounded-fragments-v3` | 327 | 148 | 178 | 1 |
| Total | 471 | 291 | 178 | 2 |

All planned requests were attempted. The candidate's historical input coverage is 495/495 parent facts and 531/531
atomic facts in eligible measured rows. These counts include budgets and repetitions and are not generation scores:

- 177 candidate outputs cite evidence IDs absent from their requests and are rejected by the Pipeline.
- One candidate output reaches the common output limit without valid structured output.
- Six accepted Unicode outputs each preserve only 1/3 required byte literals.
- 533 candidate judgments remain semantically unresolved: 226 in accepted outputs and 307 in rejected outputs.

All 469 available historical responses replayed identically; two historical response/usage records remain unavailable.
The user reported 46 sampled outputs across 11 scenarios: 29 are uniquely located, and 17 still need an arm or request
index. Two false-attribution and 13 non-atomic judgments are bound partial judgments, not completed semantic reviews.
They do not clear the original unresolved counts. No independent second reviewer is claimed.

The historical cumulative known-cost upper bound is CNY 3.521362, with CNY 4.227072 reserved for unknown usage:
CNY 7.748434 against the authorized cumulative CNY 20 cap. These are bounds/reservations, not an account invoice
or authorization for new calls. The generic live CLI does not inherit that ledger or money cap.

The candidate fails the production follow-up gates. Missing response/usage evidence and unresolved critical semantics
also block a decisive `no_production_change` outcome under the frozen rules. The findings do not establish that every
bounded strategy lacks value. They are limited to synthetic workloads, the recorded provider configuration, and the
execution revisions; a model alias is not a fixed physical snapshot. No new comparison under the rebased schema,
remote-backend acceptance, host qualification, or provider cancellation/refund guarantee is claimed.

# Are there any user-facing changes?

This PR adds evaluation commands and documentation. Production source and Gateway behavior are unchanged relative
to the reviewed upstream base `cfdb617cddc77994e5e7033bc3b7ac84d47940c9`.
The refusal-coverage change is excluded from the evaluation diff and retained in Git history for a separate follow-up; this PR
does not claim to fix that upstream gap. No new production admission policy, feature flag, migration, or API is added.

# How was this change tested?

The preceding local evaluation run completed with 1,460 passed and 48 skipped, followed by 104 passed in the included
tool-recovery suite. The user subsequently reported that `make check`, `make evaluation-unit-test`, the focused
non-live suite, Ruff lint/format, evaluation types, `make test`, `make build`, and `tox` passed before these local changes.
Those reports do not establish validation of the latest documentation and scope changes or the eventual conflict-resolved tree.

The current changes were checked statically for whitespace, evidence links, archive identities, and Gateway diff scope.
Full validation is left to the user. Historical bundle-dependent tests skip by default; the bundle does not replace the
matching historical runtime or unavailable private responses. Default tests need no provider credentials or network.
Live execution, manual cost calculation, cancellation, audit, and report regeneration are documented in
[the runbook](LIVE_RUNBOOK.md) and [Chinese instructions](LIVE_RUNBOOK.zh.md).

The PR was reported as `CONFLICTING` / `DIRTY` against `master` before this update. No upstream merge or conflict
resolution is included in this change; remote mergeability and CI must be checked at the new head before merging.

# AI usage statement

OpenAI Codex assisted with implementation, testing, analysis, and documentation. The requesting user supplied the
human inspection feedback and explicit partial judgments. Unreviewed candidates were not signed off, and unresolved
semantics, unavailable responses, skipped tests, and validation boundaries remain explicit.
