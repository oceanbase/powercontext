# Evidence admission v6 offline protocol

v6 is an evaluation-only protocol for response audit, independent fact scoring and explicit verbatim copying. It does not change the production Atomic Memory schema, normalization, admission algorithms, or v5 code and results. Its entry point is `python -m powercontext_eval_evidence_admission.v6`. There is deliberately no live command.

## Identity and compatibility

The v6 package contains its own contract, schema, instructions, reviewed atom mapping and code identity. The contract pins its atom-review file hash; each preparation and replay records the complete v6 identity and rejects a code change during execution. Artifact directories are new and cannot overwrite an existing result.

The frozen v5 modules, fixture catalogs, manifests and dependency locks remain unchanged. v6 lives in a separate subpackage; its identity must be checked independently of the legacy v5 identity. A v5 result is never relabeled as a v6 model generation result.

## Distinct scoring obligations

- Fact coverage: statement, owner, applicability, uncertainty and effective time.
- Atomicity: facts which can change independently should be separate; a combined candidate is reported explicitly.
- Exact literal retention: commands, paths, identifiers and quantities preserve their original spelling.
- Raw span fidelity: code points, UTF-8 bytes and line endings survive serialization and rendering unchanged.
- Cited support: each candidate's own supplied evidence must cover the claim; an unrelated repeated value cannot establish support.
- Output validity: response completion, JSON/schema validity and domain validation are separate from factual correctness.

The recorded `exact-literals` case has four independent atom identities: deployment command, deployment configuration path, deployment job and deployment limit. The complete command remains one literal; separately emitting `deployctl`, `--region` and `--retries` does not preserve it. Context-free string occurrence is not semantic approval. An explicit review bound to case/kind/text identity is required, and source support and exact spelling are checked again for every candidate instance. Reviews are bound to the original run manifest and are assistant reviews, not independent human reviews.

v5's composite scoring runs unchanged alongside these diagnostics. Re-scoring historical output under corrected granularity measures a contract difference, not an improved generation. Rejected and failed samples deliver no validated atoms; their identities remain in the denominator. Raw Unicode losses remain failures and cannot be normalized into a pass.

## Explicit copy prototype

The template instructions retain the production CODING extraction policy, including language, time/conflict and durability rules. Only the output representation and explicit copying rules change.

Input preparation indexes only strings present in the already admitted input and covered by its trusted supports. An index binds the evidence identity, materialization, projection, field, source byte range, payload pointer and value hash. Full fields can have multiple supported ranges; their gaps never become copyable. The index does not duplicate string values in the model input.

An output candidate contains prose parts and explicit `copy` parts. A copy names a `quote_id` and local UTF-8 byte offsets within the indexed string slice. Quote IDs are independent of citation IDs and cannot replace the top-level `evidence_ids`.

The renderer validates candidate citation ownership, range bounds and visible sequence boundaries, then copies exactly the selected bytes. It does not read Source storage or fixture annotations. Missing selections stay missing. It rejects UTF-8, CRLF, combining-mark, emoji modifier and ZWJ splits detectable inside the supplied slice. This is not a claim of a complete Unicode grapheme segmentation implementation or of visibility into omitted neighboring text.

Limits are 1,024 UTF-8 bytes per copy, 8,192 per rendered candidate, 16 parts per candidate and 64 candidates per output. These are resource limits, not proof of semantic completeness. Quoting all input is not semantic approval. The raw template and rendered candidates must be retained and scored separately in a future model run. Historical v5 output has no model-selected v6 copy references and is not automatically converted.

Atomic Memory new writes still use NFC and outer whitespace stripping. Internal CRLF is not removed by this normalization. The prototype preserves bytes before persistence only. Requiring raw NFD and outer whitespace in final persisted Memory text needs a separate production contract and compatibility design.

## Private attempt audit

The evaluation wrapper records attempt start, HTTP response, SDK response and exactly one terminal outcome. HTTP response capture precedes SDK parsing and schema/domain checks; a rejected response can therefore retain reported usage and response-model identity. Transport, response completion, JSON parsing, schema, domain/citation errors, cancellation and unknown causes are distinguished using observed evidence. Once the SDK has returned validated structured output, a subsequent domain failure takes precedence over a raw JSON-format observation; wrapped JSON accepted by the SDK is covered by regression tests. Missing usage remains unknown, even for an otherwise successful result.

The transport is for the current non-streaming OpenAI-compatible evaluation path. Local tests exercise the real OpenAI/Pydantic AI stack using `httpx.MockTransport`; they do not demonstrate real provider behavior. Streaming aggregation is not implemented or claimed here.

Raw response bodies go only into a new private audit directory outside the repository, with directory mode 0700 and file mode 0600. Capture is bounded to 65,536 bytes; a larger response is marked incomplete. Do not enable this synthetic-corpus facility for sensitive user data. Request headers, authorization and arbitrary exception messages are not recorded. The audit is not an operational log and installs no logging handlers. Audit write failure aborts the evaluation; cancellation still propagates.

## Replay and offline preparation

The targeted matrix contains four failure cases × five arms × three budgets × three replays = 180 sample identities. Five protection cases add 225 identities. The complete historical replay covers all 900 primary sample records and 548 returned outputs. All recorded candidates, inputs, supports, statuses and usage are immutable. The 13 missing rejected responses cannot be reconstructed or assigned a more specific historical cause.

Three preparation runs cover 900 + 360 + 360 = 1,620 identities. The existing v5 preparation boundary is executed with an empty local response probe so its actual selected inputs can be inspected without a provider. These probe calls are not model generations or provider requests. Their preparation fields must equal the corresponding frozen reference records.

Each selected request is then charged for v6 instructions, the input plus quote index, and the output JSON schema using the existing character estimator. This is a fixed-selection experiment: an overflow is rejected and reported; there is no silent reselection, sample deletion, extra request, budget increase or production token-limit claim. Both v5 and v6 are also compared on the same cost basis including their instructions, inputs and output schemas; the legacy v5 estimate is retained separately. Per-call, cumulative-input and per-sample call-count limits are checked. Provider wire overhead and actual tokenizer usage are unknown.

The offline cost gate requires unchanged reference inputs, reference candidate retention 495/495 and no new request-budget overflow across all arms. Passing deterministic copy tests does not establish model compliance with the new schema. Any offline cost failure blocks the proposed online canary.

## Prospective online budget

The budget draft pairs v5/v6 protocols with the same candidate admission strategy: four failure cases at three budgets and three repetitions, plus three protection cases at 4K and three repetitions. This is 90 sample identities; actual request and estimated token caps are derived from preparation, not guessed from sample count. A draft containing budget-invalid v6 samples is explicitly blocked. It is not authorization to call a provider, resume either skipped dataset, or make production changes.
