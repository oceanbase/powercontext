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
- Durable JSON publication uses a unique temporary file in the destination directory. Interrupted writes cannot
  block later scoring recovery through a stale `state.json.tmp`. File and directory fsync are retained; immutable
  audit records use atomic no-overwrite publication, including concurrent writers. Temporary files are never
  promoted to authoritative state, and normal cleanup removes only the current writer's own temporary file.
- Versioned persisted-material replay identity excludes only the envelope's server-owned `saved_at`, preserving
  detection of Source value, metadata and nested business-timestamp changes. Old preparation identities are not migrated.
- Bound review import reads `.parsed.json` for schema-valid Pipeline rejections and `.output.json` for returned
  outputs. Call, input, request, response and original review bindings are checked; rejected attempts remain rejected
  and contribute no delivered facts or qualified literals. Reviews are append-only derivatives.
- Parsed-output identities are persisted before Pipeline validation and verified during v19/v20 recovery, including
  the completed-score-before-cursor-advance state. Missing bindings or changed evidence invalidate the cached score
  and stop without resending or double charging. Report generation revalidates review context and recomputes derivative
  scores instead of trusting the saved score cache.
- Formal retention, paired improvements and decisions use deterministic exact-ground-truth scores or bound human
  reviews. Assistant-only semantic reviews remain separately visible as `assistant_diagnostic_score` and cannot clear
  unresolved formal semantics. Reports retain `qualifying_human_review_missing` until qualifying human evidence exists.
  Human reviews append to `<call_id>.human.json` without replacing assistant or original audit records; existing human
  reviews in the legacy path remain supported, and competing human derivatives are rejected.
- Zero-fact negative controls require empty candidate lists. Nonempty outputs are quality failures even when a bound
  review marks them supported with no entailed atoms; report rows expose `negative_control_nonempty_output`.
- Review import receipts distinguish the imported review's `unresolved` count from `formal_unresolved` and
  `formal_human_review_required`, and identify `reviewer_kind`/`score_kind`. A zero diagnostic count is not formal approval.
- Current-runtime regressions alongside explicit historical instruction/schema fixtures. Historical responses,
  scores, thresholds, and unavailable usage are not rewritten to satisfy the rebased runtime.
- Runtime sources included in the committed-freeze check, frozen JSON byte verification, optional evidence-reader
  handling, and numeric counter compatibility in evaluation tools.
- Generated reports and historical run records distributed as a separate checksummed archive. Historical helpers
  require its extracted `results` directory through `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS`.
  Documentation links point to the archive instructions and retain each member path without restoring result files.
- Run-specific human sample declarations and v6 assistant atom reviews are externalized into a separate,
  byte-preserving review supplement. Its published download, checksum and member hashes are documented in
  [Historical review receipts](HISTORICAL_REVIEWS.md). The original result archive and checksum are unchanged.
  Review readers use `POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS`, verify receipt bytes before parsing and retain
  original manifest/result/queue/state bindings. Default synthetic tests and protocol loading need no supplement.
  Current v6/v7 dependency identities declare expected receipt hashes without impersonating historical execution
  identities or claiming verification before replay. Explicit custom declarations require a checksum in the CLI.
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

The public archive has not been rescored with the current formal-review and zero-fact negative-control fixes.
Read-only inspection found assistant-diagnostic score receipts in the v18/v19/v20 reports; all three already record
`insufficient_evidence`. Corrected historical formal metrics and complete semantic review are not established.
The original private session audit material is unavailable in the current checkout and absent from the public archive.
Local mock-provider regressions validate the implementation and cannot substitute for historical rescoring.
If the original audit material becomes available, compatible identity-checked offline reanalysis must publish separate
derived reports with code/execution identities and preserve the original archive bytes and checksum; no new model call
is needed to rescore an existing response, and no replacement call recovers a missing historical response.

# Are there any user-facing changes?

This PR adds evaluation commands and documentation. Production source and Gateway behavior are unchanged relative
to the integrated upstream base `89d17a046a0a9c3ff113874b46131aee78425c34`.
The refusal-coverage change is excluded from the evaluation diff and retained in Git history for a separate follow-up; this PR
does not claim to fix that upstream gap. No new production admission policy, feature flag, migration, or API is added.

# How was this change tested?

The integration includes upstream `89d17a046a0a9c3ff113874b46131aee78425c34` and the receipt externalization.
The contributor reports `make test`, `make check`, and the complete `make evaluation-unit-test` passing after
integration and the UDS test isolation fix. These are local validation results; no passing remote CI result for the
updated PR head is claimed.

- The fixed-base schema-history check passed against that exact upstream revision; schema release/draft tests passed 38 cases.
- The final affected offline suite passed 297 tests across external receipts, synthetic scoring, v6-v8, the versioned CLI,
  inherited v10 scoring, v18 review/negative-control gates, durable writes and v19/v20 continuation.
- The versioned CLI suite also passed 31 tests independently. Its real subprocess completed in 2.17 seconds standalone
  and 1.98 seconds in the final combined suite, retaining the original 30-second timeout. The reviewer's timeout
  was not reproduced; no timeout increase or universal slow-environment resolution is claimed.
- Actual preserved receipts were loaded through the external readers: the declaration retains 46 sample selectors and
  15 partial annotations; the assistant receipt retains 29 entries. This is receipt/schema verification, not private
  session replay or historical semantic rescoring. The original result archive checksum was also reverified unchanged.
- Evaluator Ruff lint/format, evaluator source types, frozen-contract bytes and whitespace checks passed.
- The UDS startup-status test isolates Server logging configuration so it cannot suppress diagnostics in later tests.
  A UDS-then-LangChain privacy sequence reproduced the missing-log failure before the fix. Both startup outcomes
  followed by all 53 assistant-credential cases passed afterward (55 tests).
- The combined UDS, complete prompt-privacy, and LangChain suite passed 655 tests with one module skipped because
  optional `langfuse` was unavailable, plus 66 existing LangGraph deprecation warnings. The changed test passed
  Ruff lint/format and type checks.
- `make docs-test` passed on normal retry and all six blog tests passed. The first build stopped at a Next.js
  security-upgrade reminder before generating documentation redirects; the retry completed static-export validation.
  The dependency security reminder remains unresolved; no dependency version or security check was changed.

```bash
uv run --locked python scripts/schema_release.py check \
  --base-ref 89d17a046a0a9c3ff113874b46131aee78425c34
uv run --locked pytest tests/test_schema_release.py tests/test_schema_draft.py -q

uv run --project evaluation --locked pytest -c evaluation/pyproject.toml \
  evaluation/memory/evidence_admission/tests/test_{manifests,historical_review_receipts,sample_review,versioned_cli,v10_scoring,v18_comparison,v18_state_writes,v19_continuation,v20_continuation}.py \
  evaluation/memory/evidence_admission/tests/test_v{6,7,8}*.py -m "not live" -q
```

The tests use local Source/SQLite/Pipeline behavior with controlled provider responses. No new model request or
spending was incurred. Historical response bodies and unresolved semantic judgements remain unavailable/unresolved;
these checks do not establish new real-provider acceptance or corrected historical metrics.

The contributor's full evaluation-target result includes the root-environment recovery/budget group. Full-run
counts and logs were not supplied; the passing status is contributor-reported and is distinct from the focused
execution results above. Frozen protocol bytes, historical reports, and decision thresholds remain unchanged.
The [receipt supplement](https://github.com/user-attachments/files/35418256/powercontext-2074-review-receipts-0c467f5b.tar.gz)
is published. A downloaded copy matches the original archive byte for byte, and both receipt checksums were verified.
The [supplement instructions](HISTORICAL_REVIEWS.md) retain the source revision, archive hash and member hashes;
original result archives and historical execution identities remain unchanged.

Historical result helpers use `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS`; run-specific review receipts use the separate
`POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS` root. Default synthetic tests need neither archive, credentials nor network.
Live execution, cost calculation, cancellation and reports are documented in [the runbook](LIVE_RUNBOOK.md) and
[Chinese instructions](LIVE_RUNBOOK.zh.md). After publication, verify the actual workflow merge/base and full CI;
local fixed-base validation does not update an old GitHub Actions event's `SCHEMA_BASE`.

# AI usage statement

OpenAI Codex assisted with implementation, testing, analysis, and documentation. The requesting user supplied the
human inspection feedback and explicit partial judgments. Unreviewed candidates were not signed off, and unresolved
semantics, unavailable responses, skipped tests, and validation boundaries remain explicit.
