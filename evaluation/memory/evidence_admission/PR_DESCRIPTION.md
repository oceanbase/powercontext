# Which issue or RFC does this PR close?

Refs oceanbase/powercontext#2074.

The frozen decision is `insufficient_evidence`. The evaluation found input-coverage gains from bounded admission, but
the fixed native-generation comparison also recorded citation, structured-output, literal-preservation, and unresolved
semantic failures. This PR does not authorize a production behavior change.

# Rationale for this change

Current Source-count reduction cannot admit an oversized single Source. This evaluation compares complete projection,
current window reduction, and bounded fragments after persisting and re-reading real Sources and constructing the
production `AtomicMemoryExtractionInput`. All arms use the same frozen native generation instructions, output schema,
model settings, and per-call budget.

# What changes are included?

- A versioned `evaluation/memory/evidence_admission/` CLI, synthetic manifests, deterministic preparation and replay,
  admission arms, scoring, cancellation/failure handling, opt-in live execution, and privacy-safe reporting.
- Model-free coverage for single- and multi-Source overflow, mixed and oversized fields, positional facts, exact
  literals, CJK/UTF-8 boundaries, impossible completeness, replay, faults, cancellation, and Scope isolation.
- Crash-safe v19/v20 scoring continuation. A returned response and its usage are durably recorded as
  `scoring_pending` before scoring. Resume scores the captured response without resending the provider request or
  charging it twice. A required response with no score is a formal `scoring_incomplete` blocker and cannot produce
  `no_production_change`.
- Rebased-runtime expectations for the six evaluation tests reported in review. Historical budget tests explicitly use
  their recorded public instruction context; current-runtime tests keep the newly observed losses and budget
  rejection visible instead of rewriting historical outcomes.
- Generated reports, run records, and historical result bundles are removed from the code diff. Historical commands
  accept a separately extracted result bundle through `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS`.

# External evidence archive

The separately prepared archive preserves the former `results/` layout.

- Code snapshot used to assemble it: `8bf9c58d428995c97858b6764d4563c3cfe9ea44`
- File: `powercontext-2074-evidence-8bf9c58d.tar.gz`
- SHA-256: `ff529bf0875c2441835bc5f35af3fe60df0c62c8722370f410aec73e499c0d65`
- Download: `TO_BE_ADDED_AFTER_ARTIFACT_UPLOAD`

Per-run execution revisions and content hashes remain inside the archive manifests. The archive contains the previously
committed public, sanitized evidence only; it does not contain credentials, raw private Source text, prompts, private
model responses, sensitive paths, or cross-Scope identifiers.

# Recorded result and limitations

The 471-call fixed comparison attempted every planned call. It recorded 291 valid Pipeline outputs, 178 known output
failures, and two historical responses that remain unavailable. The historical preparation result retained 495/495
parent facts and 531/531 atomic facts for the candidate arm, but generation failures remain part of the decision:

- 177 candidate outputs cited evidence IDs absent from the request and were rejected by the actual Pipeline.
- One candidate output hit the common output limit without producing valid structured output.
- Six accepted Unicode outputs missed required byte literals.
- 533 candidates remain semantically unresolved.

These results support `insufficient_evidence`: the candidate did not pass the production follow-up gates, while the
available evidence is also insufficient to claim that every bounded-admission strategy lacks value. Input coverage is
reported separately from generation quality and cannot mask a severe output failure.

# User-facing and production changes

There are no production behavior or API changes. Source capture/projection, Memory publication, reconciliation, search,
lifecycle, authorization, CAS, and cancellation semantics are unchanged. Default tests require neither network access
nor provider credentials.

# Validation

Completed locally on the review-fix branch:

- `make evaluation-unit-test`: 1,460 passed and 48 skipped in the evaluation suite, followed by 104 passed in the
  included tool-recovery suite;
- `make check` passed before the final current-runtime expectation adjustments;
- the repository test suite reached one timing-sensitive operational-metrics failure; that test passed when rerun alone;
- focused Ruff lint/format and the affected evaluation files passed after the final adjustments.

Before updating the PR, rerun the repository-required checks at the final commit and replace the artifact download URL
placeholder above. Historical bundle-dependent tests skip by default and can be opted in by setting
`POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` to the verified extracted archive. The separately published archive is required
to reproduce historical receipts; it is not required for default offline validation.

# AI usage statement

OpenAI Codex assisted with evaluation implementation, test construction, execution bookkeeping, analysis, and
documentation. Human review feedback was supplied by the requesting user and was not expanded into unrecorded
per-candidate judgments. Unresolved semantics, unavailable responses, and validation limits remain explicit.
