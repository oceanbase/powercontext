# Which issue or RFC does this PR close?

Refs oceanbase/powercontext#2074. The evaluation concludes `insufficient_evidence`; it does not claim every acceptance
item has passed. Issue closure remains a maintainer decision based on the reported limitations.

# Rationale for this change

Current Source-count reduction cannot admit an oversized single Source. This evaluation measures bounded admission
against complete projection and current window reduction using persisted, re-read Sources and the actual
`AtomicMemoryGenerationPipeline`, with native generation instructions held fixed across the formal comparison.

# What changes are included in this PR?

- Add the versioned evaluation CLI, synthetic fixtures, admission arms, deterministic preparation, scoring,
  cancellation/failure handling, opt-in live execution, privacy-safe reports and replay receipts.
- Preserve historical protocols and their failed outcomes. The fixed native comparison attempted all 471 planned calls:
  291 returned valid Pipeline outputs, 178 had known output failures, and two historical responses remain unavailable.
- Record 177 candidate citation rejections, one output-length failure and six accepted Unicode outputs missing required
  byte literals. Input coverage gains do not override those failures. The frozen result remains `insufficient_evidence`.
- Add the requesting user's reported human-check feedback with explicitly unenumerated coverage, a failure lookup index and
  a queue of all 533 unresolved candidates. This is not independent review or completed semantic adjudication.
- Register evaluation packaging, tests and types. Keep historical v14 contract bytes protected by a dedicated check.
  Supporting validation fixes cover optional LongMemEval evidence capability, SWE-bench counter type compatibility,
  test-process import setup and isolated tox environments.

# Are there any user-facing changes?

The new commands and reports are evaluation-only. Production Source capture/projection, Memory processing,
reconciliation, search, lifecycle, public APIs and persisted production formats are unchanged.
No production follow-up is authorized. Private prompts, responses, credentials and local audit paths are excluded.

# How was this change tested?

Review-preparation checks and their exact results are recorded in `results/v20-review/validation.json`.
Historical execution receipts in `results/v20-live/validation.json` separately document 556 model-free passing tests,
four-version v20 focused checks, repository checks, types, builds and 469 matching recorded-response replays.
Two unavailable responses are not counted as replay passes. No new provider requests are made for review preparation.

The requesting user's human-check feedback is preserved as reported, with no per-call coverage or semantic judgments
invented. The 226 unresolved candidates from accepted outputs and 307 from rejected outputs remain unresolved.

Local `upstream/master` merge preflight found nine conflicts. No upstream merge or history rewrite was performed,
so this branch is not yet ready to merge against that ref. The ref was not refreshed from the network.
Full runtime tests and full multi-version tox were not rerun for this documentation-only preparation.

# AI usage statement

OpenAI Codex assisted evaluation implementation, tests, execution bookkeeping, analysis and documentation.
The human review feedback was supplied by the requesting user; Codex organized it without attributing model-generated
judgments to a human. Unresolved semantic judgments and unavailable responses remain explicit.
